__all__ = [
    'SMTP',
    ]


import socket
import logging
import asyncio
import collections

from enum import Enum
from email._header_value_parser import get_addr_spec, get_angle_addr
from email.errors import HeaderParseError


__version__ = '1.0a1'
__ident__ = 'Python SMTP {}'.format(__version__)
log = logging.getLogger('mail.log')


NEWLINE = '\n'
DATA_SIZE_DEFAULT = 33554432


class State(Enum):
    command = 0
    data = 1


class SMTP(asyncio.StreamReaderProtocol):
    command_size_limit = 512
    command_size_limits = collections.defaultdict(
        lambda x=command_size_limit: x)

    def __init__(self, handler,
                 *,
                 data_size_limit=DATA_SIZE_DEFAULT,
                 enable_SMTPUTF8=False,
                 decode_data=False,
                 loop=None):
        self.loop = loop if loop else asyncio.get_event_loop()
        super().__init__(
            asyncio.StreamReader(loop=self.loop),
            client_connected_cb=self._client_connected_cb,
            loop=self.loop)
        self.event_handler = handler
        self.data_size_limit = data_size_limit
        self.enable_SMTPUTF8 = enable_SMTPUTF8
        if enable_SMTPUTF8:
            if decode_data:
                raise ValueError(
                    "decode_data and enable_SMTPUTF8 cannot be set to "
                    "True at the same time")
            decode_data = False
        self._decode_data = decode_data
        if decode_data:
            self._emptystring = ''
            self._linesep = '\r\n'
            self._dotsep = '.'
            self._newline = NEWLINE
        else:
            self._emptystring = b''
            self._linesep = b'\r\n'
            self._dotsep = ord(b'.')
            self._newline = b'\n'
        self._set_rset_state()
        self.seen_greeting = ''
        self.extended_smtp = False
        self.command_size_limits.clear()
        self.fqdn = socket.getfqdn()   # XXX this blocks, fix it?

    @property
    def max_command_size_limit(self):
        try:
            return max(self.command_size_limits.values())
        except ValueError:
            return self.command_size_limit

    def connection_made(self, transport):
        super().connection_made(transport)
        self.peer = transport.get_extra_info('peername')
        self.transport = transport
        log.info('Peer: %s', repr(self.peer))
        # Process the client's requests.
        self.connection_closed = False
        self._handler_coroutine = self.loop.create_task(self._handle_client())

    def _client_connected_cb(self, reader, writer):
        # This is redundant since we subclass StreamReaderProtocol, but I like
        # the shorter names.
        self._reader = reader
        self._writer = writer

    def connection_lost(self, exc):
        log.exception('Disconnect')
        self._connection_closed = True
        super().connection_lost(exc)
        yield from self._handler_coroutine

    def _set_post_data_state(self):
        """Reset state variables to their post-DATA state."""
        self.smtp_state = State.command
        self.mailfrom = None
        self.rcpttos = []
        self.require_SMTPUTF8 = False

    def _set_rset_state(self):
        """Reset all state variables except the greeting."""
        self._set_post_data_state()
        self.received_data = ''
        self.received_lines = []

    @asyncio.coroutine
    def push(self, msg):
        response = bytes(
            msg + '\r\n', 'utf-8' if self.require_SMTPUTF8 else 'ascii')
        self._writer.write(response)
        log.debug(response)
        yield from self._writer.drain()

    @asyncio.coroutine
    def _handle_client(self):
        log.info('handling connection')
        yield from self.push('220 %s %s' % (self.fqdn, __version__))
        while not self.connection_closed:
            # XXX Put the line limit stuff into the StreamReader?
            line = yield from self._reader.readline()
            # XXX this rstrip may not completely preserve old behavior.
            line = line.decode('utf-8').rstrip('\r\n')
            log.info('Data: %r', line)
            if self.smtp_state is not State.command:
                yield from self.push('451 Internal confusion')
                continue
            if not line:
                yield from self.push('500 Error: bad syntax')
                continue
            i = line.find(' ')
            if i < 0:
                command = line.upper()
                arg = None
            else:
                command = line[:i].upper()
                arg = line[i+1:].strip()
            max_sz = (self.command_size_limits[command]
                      if self.extended_smtp
                      else self.command_size_limit)
            if len(line) > max_sz:
                yield from self.push('500 Error: line too long')
                continue
            method = getattr(self, 'smtp_' + command, None)
            if not method:
                yield from self.push(
                    '500 Error: command "%s" not recognized' % command)
                continue
            yield from method(arg)

    # SMTP and ESMTP commands
    @asyncio.coroutine
    def smtp_HELO(self, hostname):
        if not hostname:
            yield from self.push('501 Syntax: HELO hostname')
            return
        # See issue #21783 for a discussion of this behavior.
        if self.seen_greeting:
            yield from self.push('503 Duplicate HELO/EHLO')
            return
        self._set_rset_state()
        self.seen_greeting = hostname
        yield from self.push('250 %s' % self.fqdn)

    @asyncio.coroutine
    def smtp_EHLO(self, arg):
        if not arg:
            yield from self.push('501 Syntax: EHLO hostname')
            return
        # See issue #21783 for a discussion of this behavior.
        if self.seen_greeting:
            yield from self.push('503 Duplicate HELO/EHLO')
            return
        self._set_rset_state()
        self.seen_greeting = arg
        self.extended_smtp = True
        yield from self.push('250-%s' % self.fqdn)
        if self.data_size_limit:
            yield from self.push('250-SIZE %s' % self.data_size_limit)
            self.command_size_limits['MAIL'] += 26
        if not self._decode_data:
            yield from self.push('250-8BITMIME')
        if self.enable_SMTPUTF8:
            yield from self.push('250-SMTPUTF8')
            self.command_size_limits['MAIL'] += 10
        yield from self.push('250 HELP')

    @asyncio.coroutine
    def smtp_NOOP(self, arg):
        if arg:
            yield from self.push('501 Syntax: NOOP')
        else:
            yield from self.push('250 OK')

    @asyncio.coroutine
    def smtp_QUIT(self, arg):
        if arg:
            yield from self.push('501 Syntax: QUIT')
        else:
            yield from self.push('221 Bye')
            self._handler_coroutine.cancel()

    @asyncio.coroutine
    def close(self):
        # XXX this close is probably not quite right.
        if self._writer:
            self._writer.close()
        self._connection_closed = True

    def _strip_command_keyword(self, keyword, arg):
        keylen = len(keyword)
        if arg[:keylen].upper() == keyword:
            return arg[keylen:].strip()
        return ''

    def _getaddr(self, arg):
        if not arg:
            return '', ''
        if arg.lstrip().startswith('<'):
            address, rest = get_angle_addr(arg)
        else:
            address, rest = get_addr_spec(arg)
        if not address:
            return address, rest
        return address.addr_spec, rest

    def _getparams(self, params):
        # Return params as dictionary. Return None if not all parameters
        # appear to be syntactically valid according to RFC 1869.
        result = {}
        for param in params:
            param, eq, value = param.partition('=')
            if not param.isalnum() or eq and not value:
                return None
            result[param] = value if eq else True
        return result

    @asyncio.coroutine
    def smtp_HELP(self, arg):
        if arg:
            extended = ' [SP <mail-parameters>]'
            lc_arg = arg.upper()
            if lc_arg == 'EHLO':
                yield from self.push('250 Syntax: EHLO hostname')
            elif lc_arg == 'HELO':
                yield from self.push('250 Syntax: HELO hostname')
            elif lc_arg == 'MAIL':
                msg = '250 Syntax: MAIL FROM: <address>'
                if self.extended_smtp:
                    msg += extended
                yield from self.push(msg)
            elif lc_arg == 'RCPT':
                msg = '250 Syntax: RCPT TO: <address>'
                if self.extended_smtp:
                    msg += extended
                yield from self.push(msg)
            elif lc_arg == 'DATA':
                yield from self.push('250 Syntax: DATA')
            elif lc_arg == 'RSET':
                yield from self.push('250 Syntax: RSET')
            elif lc_arg == 'NOOP':
                yield from self.push('250 Syntax: NOOP')
            elif lc_arg == 'QUIT':
                yield from self.push('250 Syntax: QUIT')
            elif lc_arg == 'VRFY':
                yield from self.push('250 Syntax: VRFY <address>')
            else:
                yield from self.push(
                    '501 Supported commands: EHLO HELO MAIL RCPT '
                    'DATA RSET NOOP QUIT VRFY')
        else:
            yield from self.push(
                '250 Supported commands: EHLO HELO MAIL RCPT DATA '
                'RSET NOOP QUIT VRFY')

    @asyncio.coroutine
    def smtp_VRFY(self, arg):
        if arg:
            try:
                address, params = self._getaddr(arg)
            except HeaderParseError:
                address = None
            if address:
                yield from self.push(
                    '252 Cannot VRFY user, but will accept message '
                    'and attempt delivery')
            else:
                yield from self.push('502 Could not VRFY %s' % arg)
        else:
            yield from self.push('501 Syntax: VRFY <address>')

    @asyncio.coroutine
    def smtp_MAIL(self, arg):
        if not self.seen_greeting:
            yield from self.push('503 Error: send HELO first')
            return
        log.debug('===> MAIL %s', arg)
        syntaxerr = '501 Syntax: MAIL FROM: <address>'
        if self.extended_smtp:
            syntaxerr += ' [SP <mail-parameters>]'
        if arg is None:
            yield from self.push(syntaxerr)
            return
        arg = self._strip_command_keyword('FROM:', arg)
        address, params = self._getaddr(arg)
        if not address:
            yield from self.push(syntaxerr)
            return
        if not self.extended_smtp and params:
            yield from self.push(syntaxerr)
            return
        if self.mailfrom:
            yield from self.push('503 Error: nested MAIL command')
            return
        self.mail_options = params.upper().split()
        params = self._getparams(self.mail_options)
        if params is None:
            yield from self.push(syntaxerr)
            return
        if not self._decode_data:
            body = params.pop('BODY', '7BIT')
            if body not in ['7BIT', '8BITMIME']:
                yield from self.push(
                    '501 Error: BODY can only be one of 7BIT, 8BITMIME')
                return
        if self.enable_SMTPUTF8:
            smtputf8 = params.pop('SMTPUTF8', False)
            if smtputf8 is True:
                self.require_SMTPUTF8 = True
            elif smtputf8 is not False:
                yield from self.push('501 Error: SMTPUTF8 takes no arguments')
                return
        size = params.pop('SIZE', None)
        if size:
            if isinstance(size, bool) or not size.isdigit():
                yield from self.push(syntaxerr)
                return
            elif self.data_size_limit and int(size) > self.data_size_limit:
                yield from self.push(
                    '552 Error: message size exceeds fixed maximum message '
                    'size')
                return
        if len(params.keys()) > 0:
            yield from self.push(
                '555 MAIL FROM parameters not recognized or not implemented')
            return
        self.mailfrom = address
        log.info('sender: %s', self.mailfrom)
        yield from self.push('250 OK')

    @asyncio.coroutine
    def smtp_RCPT(self, arg):
        if not self.seen_greeting:
            yield from self.push('503 Error: send HELO first');
            return
        log.debug('===> RCPT %s', arg)
        if not self.mailfrom:
            yield from self.push('503 Error: need MAIL command')
            return
        syntaxerr = '501 Syntax: RCPT TO: <address>'
        if self.extended_smtp:
            syntaxerr += ' [SP <mail-parameters>]'
        if arg is None:
            yield from self.push(syntaxerr)
            return
        arg = self._strip_command_keyword('TO:', arg)
        address, params = self._getaddr(arg)
        if not address:
            yield from self.push(syntaxerr)
            return
        if not self.extended_smtp and params:
            yield from self.push(syntaxerr)
            return
        self.rcpt_options = params.upper().split()
        params = self._getparams(self.rcpt_options)
        if params is None:
            yield from self.push(syntaxerr)
            return
        # XXX currently there are no options we recognize.
        if len(params.keys()) > 0:
            yield from self.push(
                '555 RCPT TO parameters not recognized or not implemented')
            return
        self.rcpttos.append(address)
        log.info('recips: %s', self.rcpttos)
        yield from self.push('250 OK')

    @asyncio.coroutine
    def smtp_RSET(self, arg):
        if arg:
            yield from self.push('501 Syntax: RSET')
            return
        self._set_rset_state()
        yield from self.push('250 OK')

    @asyncio.coroutine
    def smtp_DATA(self, arg):
        if not self.seen_greeting:
            yield from self.push('503 Error: send HELO first');
            return
        if not self.rcpttos:
            yield from self.push('503 Error: need RCPT command')
            return
        if arg:
            yield from self.push('501 Syntax: DATA')
            return
        self.smtp_state = State.data
        yield from self.push('354 End data with <CR><LF>.<CR><LF>')
        data = []
        self.num_bytes = 0
        while not self.connection_closed:
            line = yield from self._reader.readline()
            if line == b'.\r\n':
                break
            self.num_bytes += len(line)
            if self.data_size_limit and self.num_bytes > self.data_size_limit:
                yield from self.push('552 Error: Too much mail data')
            # XXX this rstrip may not exactly preserve the old behavior
            line = line.rstrip(b'\r\n')
            if self._decode_data:
                data.append(line.decode('utf-8'))
            else:
                data.append(line)
        # Remove extraneous carriage returns and de-transparency
        # according to RFC 5321, Section 4.5.2.
        for i in range(len(data)):
            text = data[i]
            if text and text[0] == self._dotsep:
                data[i] = text[1:]
        self.received_data = self._newline.join(data)
        args = (self.peer, self.mailfrom, self.rcpttos,
                self.received_data)
        kwargs = {}
        if not self._decode_data:
            kwargs = {
                'mail_options': self.mail_options,
                'rcpt_options': self.rcpt_options,
            }
        status = self.event_handler.process_message(*args, **kwargs)
        self._set_post_data_state()
        if status:
            yield from self.push(status)
        else:
            yield from self.push('250 OK')

    # Commands that have not been implemented
    @asyncio.coroutine
    def smtp_EXPN(self, arg):
        yield from self.push('502 EXPN not implemented')
