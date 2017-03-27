"""Handlers which provide custom processing at various events.

At certain times in the SMTP protocol, various events can be processed.  These
events include the SMTP commands, and at the completion of the data receipt.
Pass in an instance of one of these classes, or derive your own, to provide
your own handling of messages.  Implement only the methods you care about.
"""

import sys
import asyncio
import logging
import mailbox
import smtplib

from email import message_from_bytes, message_from_string
from email.feedparser import NLCRE
from public import public


EMPTYSTRING = ''
COMMASPACE = ', '
CRLF = '\r\n'
log = logging.getLogger('mail.debug')


def _format_peer(peer):
    # This is a separate function mostly so the test suite can craft a
    # reproducible output.
    return 'X-Peer: {!r}'.format(peer)


@public
class Debugging:
    def __init__(self, stream=None):
        self.stream = sys.stdout if stream is None else stream

    @classmethod
    def from_cli(cls, parser, *args):
        error = False
        stream = None
        if len(args) == 0:
            pass
        elif len(args) > 1:
            error = True
        elif args[0] == 'stdout':
            stream = sys.stdout
        elif args[0] == 'stderr':
            stream = sys.stderr
        else:
            error = True
        if error:
            parser.error('Debugging usage: [stdout|stderr]')
        return cls(stream)

    def _print_message_content(self, peer, data):
        in_headers = True
        for line in data.splitlines():
            # Dump the RFC 2822 headers first.
            if in_headers and not line:
                print(_format_peer(peer), file=self.stream)
                in_headers = False
            if isinstance(data, bytes):
                # Avoid spurious 'str on bytes instance' warning.
                line = line.decode('utf-8', 'replace')
            print(line, file=self.stream)

    @asyncio.coroutine
    def handle_DATA(self, session, envelope):
        print('---------- MESSAGE FOLLOWS ----------', file=self.stream)
        # Yes, actually test for truthiness since it's possible for either the
        # keywords to be missing, or for their values to be empty lists.
        add_separator = False
        if envelope.mail_options:
            print('mail options:', envelope.mail_options, file=self.stream)
            add_separator = True
        # rcpt_options are not currently support by the SMTP class.
        rcpt_options = envelope.rcpt_options
        if any(rcpt_options):                            # pragma: nocover
            print('rcpt options:', rcpt_options, file=self.stream)
            add_separator = True
        if add_separator:
            print(file=self.stream)
        self._print_message_content(session.peer, envelope.content)
        print('------------ END MESSAGE ------------', file=self.stream)


@public
class Proxy:
    def __init__(self, remote_hostname, remote_port):
        self._hostname = remote_hostname
        self._port = remote_port

    @asyncio.coroutine
    def handle_MAIL(self, envelope, address, options):
        envelope.client = s = smtplib.SMTP()
        s.connect(self._hostname, self._port)
        s.ehlo_or_helo_if_needed()
        code, resp = s.mail(address, options)
        if code == 250:
            envelope.mail_from = address
            envelope.mail_options = options
        return '%s %s'.format(code, resp)

    @asyncio.coroutine
    def handle_RCPT(self, envelope, address, options):
        s = envelope.client
        code, resp = s.rcpt(address, options)
        if code in (250, 251):
            envelope.rcpt_tos += address
            envelope.rcpt_options += options
        return '%s %s'.format(code, resp)

    @asyncio.coroutine
    def handle_DATA(self, session, envelope):
        lines = envelope.content.splitlines(keepends=True)
        # Look for the last header
        i = 0
        ending = CRLF
        for line in lines:                          # pragma: nobranch
            if NLCRE.match(line):
                ending = line
                break
            i += 1
        lines.insert(i, 'X-Peer: %s%s' % (session.peer[0], ending))
        data = EMPTYSTRING.join(lines)
        code, resp = envelope.client(data)
        return '%s %s'.format(code, resp)

    def handle_RSET(self, session, envelope):
        s = getattr(envelope, 'client', None)
        if s:
            s.quit()
            envelope.client = None

@public
class Sink:
    @classmethod
    def from_cli(cls, parser, *args):
        if len(args) > 0:
            parser.error('Sink handler does not accept arguments')
        return cls()

    @asyncio.coroutine
    def handle_DATA(self, session, envelope):
        pass                                        # pragma: nocover


@public
class Message:
    def __init__(self, message_class=None):
        self.message_class = message_class

    @asyncio.coroutine
    def handle_DATA(self, session, envelope):
        envelope = self.prepare_message(session, envelope)
        self.handle_message(envelope)

    def prepare_message(self, session, envelope):
        # If the server was created with decode_data True, then data will be a
        # str, otherwise it will be bytes.
        data = envelope.content
        if isinstance(data, bytes):
            message = message_from_bytes(data, self.message_class)
        else:
            assert isinstance(data, str), (
              'Expected str or bytes, got {}'.format(type(data)))
            message = message_from_string(data, self.message_class)
        message['X-Peer'] = str(session.peer)
        message['X-MailFrom'] = envelope.mail_from
        message['X-RcptTo'] = COMMASPACE.join(envelope.rcpt_tos)
        return message

    def handle_message(self, message):
        raise NotImplementedError                   # pragma: nocover


@public
class AsyncMessage(Message):
    def __init__(self, message_class=None, *, loop=None):
        super().__init__(message_class)
        self.loop = loop or asyncio.get_event_loop()

    @asyncio.coroutine
    def handle_DATA(self, session, envelope):
        message = self.prepare_message(session, envelope)
        yield from self.handle_message(message)

    @asyncio.coroutine
    def handle_message(self, message):
        raise NotImplementedError                   # pragma: nocover


@public
class Mailbox(Message):
    def __init__(self, mail_dir, message_class=None):
        self.mailbox = mailbox.Maildir(mail_dir)
        self.mail_dir = mail_dir
        super().__init__(message_class)

    def handle_message(self, message):
        self.mailbox.add(message)

    def reset(self):
        self.mailbox.clear()

    @classmethod
    def from_cli(cls, parser, *args):
        if len(args) < 1:
            parser.error('The directory for the maildir is required')
        elif len(args) > 1:
            parser.error('Too many arguments for Mailbox handler')
        return cls(args[0])
