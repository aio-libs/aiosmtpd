#! /usr/bin/env python3
"""An RFC 5321 smtp proxy with optional RFC 1870 and RFC 6531 extensions.

Usage: %(program)s [options] [localhost:localport [remotehost:remoteport]]

Options:

    --nosetuid
    -n
        This program generally tries to setuid `nobody', unless this flag is
        set.  The setuid call will fail if this program is not run as root (in
        which case, use this flag).

    --version
    -V
        Print the version number and exit.

    --class classname
    -c classname
        Use `classname' as the concrete SMTP proxy class.  Uses `PureProxy' by
        default.

    --size limit
    -s limit
        Restrict the total size of the incoming message to "limit" number of
        bytes via the RFC 1870 SIZE extension.  Defaults to 33554432 bytes.

    --smtputf8
    -u
        Enable the SMTPUTF8 extension and behave as an RFC 6531 smtp proxy.

    --debug
    -d
        Turn on debugging prints.

    --help
    -h
        Print this message and exit.

Version: %(__version__)s

If localhost is not given then `localhost' is used, and if localport is not
given then 8025 is used.  If remotehost is not given then `localhost' is used,
and if remoteport is not given, then 25 is used.
"""

# Overview:
#
# This file implements the minimal SMTP protocol as defined in RFC 5321.  It
# has a hierarchy of classes which implement the backend functionality for the
# smtpd.  A number of classes are provided:
#
#   SMTPServer - the base class for the backend.  Raises NotImplementedError
#   if you try to use it.
#
#   DebuggingServer - simply prints each message it receives on stdout.
#
#   PureProxy - Proxies all messages to a real smtpd which does final
#   delivery.  One known problem with this class is that it doesn't handle
#   SMTP errors from the backend server at all.  This should be fixed
#   (contributions are welcome!).
#
#   MailmanProxy - An experimental hack to work with GNU Mailman
#   <www.list.org>.  Using this server as your real incoming smtpd, your
#   mailhost will automatically recognize and accept mail destined to Mailman
#   lists when those lists are created.  Every message not destined for a list
#   gets forwarded to a real backend smtpd, as with PureProxy.  Again, errors
#   are not handled correctly yet.
#
#
# Author: Barry Warsaw <barry@python.org>
#
# TODO:
#
# - support mailbox delivery
# - alias files
# - Handle more ESMTP extensions
# - handle error codes from the backend smtpd

import sys
import os
import getopt
import time
import socket
import signal
import asyncio
import collections
from warnings import warn
from email._header_value_parser import get_addr_spec, get_angle_addr


__all__ = ["SMTPServer","DebuggingServer","PureProxy","MailmanProxy"]

program = sys.argv[0]
__version__ = 'Python SMTP proxy version 0.3'


class Devnull:
    def write(self, msg): pass
    def flush(self): pass


DEBUGSTREAM = Devnull()
NEWLINE = '\n'
COMMASPACE = ', '
DATA_SIZE_DEFAULT = 33554432


def usage(code, msg=''):
    print(__doc__ % globals(), file=sys.stderr)
    if msg:
        print(msg, file=sys.stderr)
    sys.exit(code)


class SMTPChannel(asyncio.StreamReaderProtocol):
    COMMAND = 0
    DATA = 1

    command_size_limit = 512
    command_size_limits = collections.defaultdict(lambda x=command_size_limit: x)

    @property
    def max_command_size_limit(self):
        try:
            return max(self.command_size_limits.values())
        except ValueError:
            return self.command_size_limit

    def __init__(self, server, conn, addr, data_size_limit=DATA_SIZE_DEFAULT,
                 map=None, enable_SMTPUTF8=False, decode_data=None, *,
                 loop=None):
        self.loop = loop if loop else asyncio.get_event_loop()
        super().__init__(
            asyncio.StreamReader(loop=self.loop),
            client_connected_cb=self._client_connected_cb,
            loop=self.loop)
        self.smtp_server = server
        self.conn = conn
        self.addr = addr
        self.data_size_limit = data_size_limit
        self.enable_SMTPUTF8 = enable_SMTPUTF8
        if enable_SMTPUTF8:
            if decode_data:
                ValueError("decode_data and enable_SMTPUTF8 cannot be set to"
                           " True at the same time")
            decode_data = False
        if decode_data is None:
            warn("The decode_data default of True will change to False in 3.6;"
                 " specify an explicit value for this keyword",
                 DeprecationWarning, 2)
            decode_data = True
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

    def connection_made(self, transport):
        super().connection_made(transport)
        self.peer = transport.get_extra_info('peername')
        self.transport = transport
        print('Peer:', repr(self.peer), file=DEBUGSTREAM)
        # Process the client's requests.
        self.connection_closed = False
        self._handler_coroutine = self.loop.create_task(self._handle_client())

    def _client_connected_cb(self, reader, writer):
        # This is redundant since we subclass StreamReaderProtocol, but I like
        # the shorter names.
        self._reader = reader
        self._writer = writer

    def connection_lost(self, exc):
        print('Disconnect:', str(exc), file=DEBUGSTREAM)
        self._connection_closed = True
        super().connection_lost(exc)
        yield from self._handler_coroutine

    def _set_post_data_state(self):
        """Reset state variables to their post-DATA state."""
        self.smtp_state = self.COMMAND
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
        self._writer.write(bytes(
            msg + '\r\n', 'utf-8' if self.require_SMTPUTF8 else 'ascii'))
        yield from self._writer.drain()

    @asyncio.coroutine
    def _handle_client(self):
        print('handling connection', file=DEBUGSTREAM)
        yield from self.push('220 %s %s' % (self.fqdn, __version__))
        while not self.connection_closed:
            # XXX Put the line limit stuff into the StreamReader?
            line = yield from self._reader.readline()
            # XXX this rstrip may not completely preserve old behavior.
            line = line.decode('utf-8').rstrip('\r\n')
            print('Data:', repr(line), file=DEBUGSTREAM)
            if self.smtp_state != self.COMMAND:
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
                        if self.extended_smtp else self.command_size_limit)
            if len(line) > max_sz:
                yield from self.push('500 Error: line too long')
                continue
            method = getattr(self, 'smtp_' + command, None)
            if not method:
                yield from self.push('500 Error: command "%s" not recognized' % command)
                continue
            yield from method(arg)

    # SMTP and ESMTP commands
    @asyncio.coroutine
    def smtp_HELO(self, arg):
        if not arg:
            yield from self.push('501 Syntax: HELO hostname')
            return
        # See issue #21783 for a discussion of this behavior.
        if self.seen_greeting:
            yield from self.push('503 Duplicate HELO/EHLO')
            return
        self._set_rset_state()
        self.seen_greeting = arg
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
        # arg is ignored
        yield from self.push('221 Bye')
        # XXX this close is probably not quite right.
        yield from self.close()

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
                yield from self.push('501 Supported commands: EHLO HELO MAIL RCPT '
                          'DATA RSET NOOP QUIT VRFY')
        else:
            yield from self.push('250 Supported commands: EHLO HELO MAIL RCPT DATA '
                      'RSET NOOP QUIT VRFY')

    @asyncio.coroutine
    def smtp_VRFY(self, arg):
        if arg:
            address, params = self._getaddr(arg)
            if address:
                yield from self.push('252 Cannot VRFY user, but will accept message '
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
        print('===> MAIL', arg, file=DEBUGSTREAM)
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
                yield from self.push('501 Error: BODY can only be one of 7BIT, 8BITMIME')
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
            if not size.isdigit():
                yield from self.push(syntaxerr)
                return
            elif self.data_size_limit and int(size) > self.data_size_limit:
                yield from self.push('552 Error: message size exceeds fixed maximum message size')
                return
        if len(params.keys()) > 0:
            yield from self.push('555 MAIL FROM parameters not recognized or not implemented')
            return
        self.mailfrom = address
        print('sender:', self.mailfrom, file=DEBUGSTREAM)
        yield from self.push('250 OK')

    @asyncio.coroutine
    def smtp_RCPT(self, arg):
        if not self.seen_greeting:
            yield from self.push('503 Error: send HELO first');
            return
        print('===> RCPT', arg, file=DEBUGSTREAM)
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
            yield from self.push('555 RCPT TO parameters not recognized or not implemented')
            return
        self.rcpttos.append(address)
        print('recips:', self.rcpttos, file=DEBUGSTREAM)
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
        self.smtp_state = self.DATA
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
        status = self.smtp_server.process_message(*args, **kwargs)
        self._set_post_data_state()
        if not status:
            yield from self.push('250 OK')
        else:
            yield from self.push(status)

    # Commands that have not been implemented
    @asyncio.coroutine
    def smtp_EXPN(self, arg):
        yield from self.push('502 EXPN not implemented')


class SMTPServer:
    # SMTPChannel class to use for managing client connections
    channel_class = SMTPChannel

    def __init__(self, localaddr, remoteaddr,
                 data_size_limit=DATA_SIZE_DEFAULT, map=None,
                 enable_SMTPUTF8=False, decode_data=None):
        self._localaddr = localaddr
        self._remoteaddr = remoteaddr
        self.data_size_limit = data_size_limit
        self.enable_SMTPUTF8 = enable_SMTPUTF8
        if enable_SMTPUTF8:
            if decode_data:
                raise ValueError("The decode_data and enable_SMTPUTF8"
                                 " parameters cannot be set to True at the"
                                 " same time.")
            decode_data = False
        if decode_data is None:
            warn("The decode_data default of True will change to False in 3.6;"
                 " specify an explicit value for this keyword",
                 DeprecationWarning, 2)
            decode_data = True
        self._decode_data = decode_data

        print('%s created at %s\n\tLocal addr: %s\n\tRemote addr:%s' % (
            self.__class__.__name__, time.ctime(time.time()),
            localaddr, remoteaddr), file=DEBUGSTREAM)

    # API for "doing something useful with the message"
    def process_message(self, peer, mailfrom, rcpttos, data, **kwargs):
        """Override this abstract method to handle messages from the client.

        peer is a tuple containing (ipaddr, port) of the client that made the
        socket connection to our smtp port.

        mailfrom is the raw address the client claims the message is coming
        from.

        rcpttos is a list of raw addresses the client wishes to deliver the
        message to.

        data is a string containing the entire full text of the message,
        headers (if supplied) and all.  It has been `de-transparencied'
        according to RFC 821, Section 4.5.2.  In other words, a line
        containing a `.' followed by other text has had the leading dot
        removed.

        kwargs is a dictionary containing additional information. It is empty
        unless decode_data=False or enable_SMTPUTF8=True was given as init
        parameter, in which case ut will contain the following keys:
            'mail_options': list of parameters to the mail command.  All
                            elements are uppercase strings.  Example:
                            ['BODY=8BITMIME', 'SMTPUTF8'].
            'rcpt_options': same, for the rcpt command.

        This function should return None for a normal `250 Ok' response;
        otherwise, it should return the desired response string in RFC 821
        format.

        """
        raise NotImplementedError


class DebuggingServer(SMTPServer):

    def _print_message_content(self, peer, data):
        inheaders = 1
        lines = data.splitlines()
        for line in lines:
            # headers first
            if inheaders and not line:
                peerheader = 'X-Peer: ' + peer[0]
                if not isinstance(data, str):
                    # decoded_data=false; make header match other binary output
                    peerheader = repr(peerheader.encode('utf-8'))
                print(peerheader)
                inheaders = 0
            if not isinstance(data, str):
                # Avoid spurious 'str on bytes instance' warning.
                line = repr(line)
            print(line)

    def process_message(self, peer, mailfrom, rcpttos, data, **kwargs):
        print('---------- MESSAGE FOLLOWS ----------')
        if kwargs:
            if kwargs.get('mail_options'):
                print('mail options: %s' % kwargs['mail_options'])
            if kwargs.get('rcpt_options'):
                print('rcpt options: %s\n' % kwargs['rcpt_options'])
        self._print_message_content(peer, data)
        print('------------ END MESSAGE ------------')


class PureProxy(SMTPServer):
    def __init__(self, *args, **kwargs):
        if 'enable_SMTPUTF8' in kwargs and kwargs['enable_SMTPUTF8']:
            raise ValueError("PureProxy does not support SMTPUTF8.")
        super(PureProxy, self).__init__(*args, **kwargs)

    def process_message(self, peer, mailfrom, rcpttos, data):
        lines = data.split('\n')
        # Look for the last header
        i = 0
        for line in lines:
            if not line:
                break
            i += 1
        lines.insert(i, 'X-Peer: %s' % peer[0])
        data = NEWLINE.join(lines)
        refused = self._deliver(mailfrom, rcpttos, data)
        # TBD: what to do with refused addresses?
        print('we got some refusals:', refused, file=DEBUGSTREAM)

    def _deliver(self, mailfrom, rcpttos, data):
        import smtplib
        refused = {}
        try:
            s = smtplib.SMTP()
            s.connect(self._remoteaddr[0], self._remoteaddr[1])
            try:
                refused = s.sendmail(mailfrom, rcpttos, data)
            finally:
                s.quit()
        except smtplib.SMTPRecipientsRefused as e:
            print('got SMTPRecipientsRefused', file=DEBUGSTREAM)
            refused = e.recipients
        except (OSError, smtplib.SMTPException) as e:
            print('got', e.__class__, file=DEBUGSTREAM)
            # All recipients were refused.  If the exception had an associated
            # error code, use it.  Otherwise,fake it with a non-triggering
            # exception code.
            errcode = getattr(e, 'smtp_code', -1)
            errmsg = getattr(e, 'smtp_error', 'ignore')
            for r in rcpttos:
                refused[r] = (errcode, errmsg)
        return refused


class MailmanProxy(PureProxy):
    def __init__(self, *args, **kwargs):
        if 'enable_SMTPUTF8' in kwargs and kwargs['enable_SMTPUTF8']:
            raise ValueError("MailmanProxy does not support SMTPUTF8.")
        super(PureProxy, self).__init__(*args, **kwargs)

    def process_message(self, peer, mailfrom, rcpttos, data):
        from io import StringIO
        from Mailman import Utils
        from Mailman import Message
        from Mailman import MailList
        # If the message is to a Mailman mailing list, then we'll invoke the
        # Mailman script directly, without going through the real smtpd.
        # Otherwise we'll forward it to the local proxy for disposition.
        listnames = []
        for rcpt in rcpttos:
            local = rcpt.lower().split('@')[0]
            # We allow the following variations on the theme
            #   listname
            #   listname-admin
            #   listname-owner
            #   listname-request
            #   listname-join
            #   listname-leave
            parts = local.split('-')
            if len(parts) > 2:
                continue
            listname = parts[0]
            if len(parts) == 2:
                command = parts[1]
            else:
                command = ''
            if not Utils.list_exists(listname) or command not in (
                    '', 'admin', 'owner', 'request', 'join', 'leave'):
                continue
            listnames.append((rcpt, listname, command))
        # Remove all list recipients from rcpttos and forward what we're not
        # going to take care of ourselves.  Linear removal should be fine
        # since we don't expect a large number of recipients.
        for rcpt, listname, command in listnames:
            rcpttos.remove(rcpt)
        # If there's any non-list destined recipients left,
        print('forwarding recips:', ' '.join(rcpttos), file=DEBUGSTREAM)
        if rcpttos:
            refused = self._deliver(mailfrom, rcpttos, data)
            # TBD: what to do with refused addresses?
            print('we got refusals:', refused, file=DEBUGSTREAM)
        # Now deliver directly to the list commands
        mlists = {}
        s = StringIO(data)
        msg = Message.Message(s)
        # These headers are required for the proper execution of Mailman.  All
        # MTAs in existence seem to add these if the original message doesn't
        # have them.
        if not msg.get('from'):
            msg['From'] = mailfrom
        if not msg.get('date'):
            msg['Date'] = time.ctime(time.time())
        for rcpt, listname, command in listnames:
            print('sending message to', rcpt, file=DEBUGSTREAM)
            mlist = mlists.get(listname)
            if not mlist:
                mlist = MailList.MailList(listname, lock=0)
                mlists[listname] = mlist
            # dispatch on the type of command
            if command == '':
                # post
                msg.Enqueue(mlist, tolist=1)
            elif command == 'admin':
                msg.Enqueue(mlist, toadmin=1)
            elif command == 'owner':
                msg.Enqueue(mlist, toowner=1)
            elif command == 'request':
                msg.Enqueue(mlist, torequest=1)
            elif command in ('join', 'leave'):
                # TBD: this is a hack!
                if command == 'join':
                    msg['Subject'] = 'subscribe'
                else:
                    msg['Subject'] = 'unsubscribe'
                msg.Enqueue(mlist, torequest=1)


class Options:
    setuid = True
    classname = 'PureProxy'
    size_limit = None
    enable_SMTPUTF8 = False


def parseargs():
    global DEBUGSTREAM
    try:
        opts, args = getopt.getopt(
            sys.argv[1:], 'nVhc:s:du',
            ['class=', 'nosetuid', 'version', 'help', 'size=', 'debug',
             'smtputf8'])
    except getopt.error as e:
        usage(1, e)

    options = Options()
    for opt, arg in opts:
        if opt in ('-h', '--help'):
            usage(0)
        elif opt in ('-V', '--version'):
            print(__version__)
            sys.exit(0)
        elif opt in ('-n', '--nosetuid'):
            options.setuid = False
        elif opt in ('-c', '--class'):
            options.classname = arg
        elif opt in ('-d', '--debug'):
            DEBUGSTREAM = sys.stderr
        elif opt in ('-u', '--smtputf8'):
            options.enable_SMTPUTF8 = True
        elif opt in ('-s', '--size'):
            try:
                int_size = int(arg)
                options.size_limit = int_size
            except:
                print('Invalid size: ' + arg, file=sys.stderr)
                sys.exit(1)

    # parse the rest of the arguments
    if len(args) < 1:
        localspec = 'localhost:8025'
        remotespec = 'localhost:25'
    elif len(args) < 2:
        localspec = args[0]
        remotespec = 'localhost:25'
    elif len(args) < 3:
        localspec = args[0]
        remotespec = args[1]
    else:
        usage(1, 'Invalid arguments: %s' % COMMASPACE.join(args))

    # split into host/port pairs
    i = localspec.find(':')
    if i < 0:
        usage(1, 'Bad local spec: %s' % localspec)
    options.localhost = localspec[:i]
    try:
        options.localport = int(localspec[i+1:])
    except ValueError:
        usage(1, 'Bad local port: %s' % localspec)
    i = remotespec.find(':')
    if i < 0:
        usage(1, 'Bad remote spec: %s' % remotespec)
    options.remotehost = remotespec[:i]
    try:
        options.remoteport = int(remotespec[i+1:])
    except ValueError:
        usage(1, 'Bad remote port: %s' % remotespec)
    return options


if __name__ == '__main__':
    options = parseargs()
    # Become nobody
    classname = options.classname
    if "." in classname:
        lastdot = classname.rfind(".")
        mod = __import__(classname[:lastdot], globals(), locals(), [""])
        classname = classname[lastdot+1:]
    else:
        import __main__ as mod
    class_ = getattr(mod, classname)
    proxy = class_((options.localhost, options.localport),
                   (options.remotehost, options.remoteport),
                   options.size_limit, enable_SMTPUTF8=options.enable_SMTPUTF8)
    if options.setuid:
        try:
            import pwd
        except ImportError:
            print('Cannot import module "pwd"; try running with -n option.', file=sys.stderr)
            sys.exit(1)
        nobody = pwd.getpwnam('nobody')[2]
        try:
            os.setuid(nobody)
        except PermissionError:
            print('Cannot setuid "nobody"; try running with -n option.', file=sys.stderr)
            sys.exit(1)

    def handler():
        return class_.channel_class(proxy, 'FIXME', 'FIXME', options.size_limit,
                                    None, options.enable_SMTPUTF8)

    import logging
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    server = loop.run_until_complete(
        loop.create_server(handler, options.localhost, options.localport))
    loop.add_signal_handler(signal.SIGINT, loop.stop)
    loop.run_forever()
    server.close()
    # XXX This cleanup is probably incomplete.
    loop.run_until_complete(server.wait_closed())
    loop.close()
