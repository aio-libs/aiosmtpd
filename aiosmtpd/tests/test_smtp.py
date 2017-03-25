"""Test the SMTP protocol."""

import socket
import asyncio
import unittest

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server, __ident__ as GREETING
from contextlib import ExitStack
from smtplib import SMTP, SMTPDataError, SMTPResponseException
from unittest.mock import Mock, patch

CRLF = '\r\n'
BCRLF = b'\r\n'


class UTF8Controller(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True)


class NoDecodeController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=False)


class ReceivingHandler:
    box = None

    def __init__(self):
        self.box = []

    @asyncio.coroutine
    def handle_DATA(self, session, envelope):
        self.box.append(envelope.content)


class SizedController(Controller):
    def __init__(self, handler, size, loop=None, hostname='::0', port=8025):
        self.size = size
        super().__init__(handler, loop, hostname, port)

    def factory(self):
        return Server(self.handler, data_size_limit=self.size)


class SMTPUTF8Controller(Controller):
    def factory(self):
        return Server(self.handler, enable_SMTPUTF8=True)


class CustomHostnameController(Controller):
    def factory(self):
        return Server(self.handler, hostname='custom.localhost')


class CustomIdentController(Controller):
    def factory(self):
        server = Server(self.handler)
        server.__ident__ = 'Identifying SMTP v2112'
        return server


class ErroringHandler:
    error = None

    @asyncio.coroutine
    def handle_DATA(self, session, envelope):
        return '499 Could not accept the message'

    @asyncio.coroutine
    def handle_exception(self, error):
        self.error = error


class ErrorSMTP(Server):
    @asyncio.coroutine
    def smtp_HELO(self, hostname):
        raise ValueError('test')


class ErrorController(Controller):
    def factory(self):
        return ErrorSMTP(self.handler)


class TestProtocol(unittest.TestCase):
    def write(self, data):
        self.responses.append(data)

    def setUp(self):
        self.transport = Mock()
        self.transport.write = self.write
        self.responses = []
        self._old_loop = asyncio.get_event_loop()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()
        asyncio.set_event_loop(self._old_loop)

    def _get_protocol(self, *args, **kwargs):
        protocol = Server(*args, loop=self.loop, **kwargs)
        protocol.connection_made(self.transport)
        return protocol

    def test_honors_mail_delimeters(self):
        handler = ReceivingHandler()
        data = b'test\r\nmail\rdelimeters\nsaved'
        protocol = self._get_protocol(handler)
        protocol.data_received(BCRLF.join([
            b'HELO example.org',
            b'MAIL FROM: <anne@example.com>',
            b'RCPT TO: <anne@example.com>',
            b'DATA',
            data + b'\r\n.',
            b'QUIT\r\n'
            ]))
        try:
            self.loop.run_until_complete(protocol._handler_coroutine)
        except asyncio.CancelledError:
            pass
        self.assertEqual(len(handler.box), 1)
        self.assertEqual(handler.box[0], data)

    def test_empty_email(self):
        handler = ReceivingHandler()
        protocol = self._get_protocol(handler)
        protocol.data_received(BCRLF.join([
            b'HELO example.org',
            b'MAIL FROM: <anne@example.com>',
            b'RCPT TO: <anne@example.com>',
            b'DATA',
            b'.',
            b'QUIT\r\n'
            ]))
        try:
            self.loop.run_until_complete(protocol._handler_coroutine)
        except asyncio.CancelledError:
            pass
        self.assertEqual(self.responses[5], b'250 OK\r\n')
        self.assertEqual(len(handler.box), 1)
        self.assertEqual(handler.box[0], b'')


class TestSMTP(unittest.TestCase):
    def setUp(self):
        controller = UTF8Controller(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_helo(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            self.assertEqual(response, bytes(socket.getfqdn(), 'utf-8'))

    def test_helo_no_hostname(self):
        with SMTP(*self.address) as client:
            # smtplib substitutes .local_hostname if the argument is falsey.
            client.local_hostname = ''
            code, response = client.helo('')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: HELO hostname')

    def test_helo_duplicate(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.helo('example.org')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Duplicate HELO/EHLO')

    def test_ehlo(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            lines = response.splitlines()
            self.assertEqual(lines[0], bytes(socket.getfqdn(), 'utf-8'))
            self.assertEqual(lines[1], b'SIZE 33554432')
            self.assertEqual(lines[2], b'HELP')

    def test_ehlo_duplicate(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.ehlo('example.org')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Duplicate HELO/EHLO')

    def test_ehlo_no_hostname(self):
        with SMTP(*self.address) as client:
            # smtplib substitutes .local_hostname if the argument is falsey.
            client.local_hostname = ''
            code, response = client.ehlo('')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: EHLO hostname')

    def test_helo_then_ehlo(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.ehlo('example.org')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Duplicate HELO/EHLO')

    def test_ehlo_then_helo(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.helo('example.org')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Duplicate HELO/EHLO')

    def test_noop(self):
        with SMTP(*self.address) as client:
            code, response = client.noop()
            self.assertEqual(code, 250)

    def test_noop_with_arg(self):
        with SMTP(*self.address) as client:
            # .noop() doesn't accept arguments.
            code, response = client.docmd('NOOP', 'oops')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: NOOP')

    def test_quit(self):
        client = SMTP(*self.address)
        code, response = client.quit()
        self.assertEqual(code, 221)
        self.assertEqual(response, b'Bye')

    def test_quit_with_arg(self):
        client = SMTP(*self.address)
        code, response = client.docmd('QUIT', 'oops')
        self.assertEqual(code, 501)
        self.assertEqual(response, b'Syntax: QUIT')

    def test_help(self):
        with SMTP(*self.address) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('HELP')
            self.assertEqual(code, 250)
            self.assertEqual(response,
                             b'Supported commands: EHLO HELO MAIL RCPT '
                             b'DATA RSET NOOP QUIT VRFY')

    def test_help_helo(self):
        with SMTP(*self.address) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('HELP', 'HELO')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: HELO hostname')

    def test_help_ehlo(self):
        with SMTP(*self.address) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('HELP', 'EHLO')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: EHLO hostname')

    def test_help_mail(self):
        with SMTP(*self.address) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('HELP', 'MAIL')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')

    def test_help_mail_esmtp(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('HELP', 'MAIL')
            self.assertEqual(code, 250)
            self.assertEqual(
                response,
                b'Syntax: MAIL FROM: <address> [SP <mail-parameters>]')

    def test_help_rcpt(self):
        with SMTP(*self.address) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('HELP', 'RCPT')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: RCPT TO: <address>')

    def test_help_rcpt_esmtp(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('HELP', 'RCPT')
            self.assertEqual(code, 250)
            self.assertEqual(
                response,
                b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')

    def test_help_data(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('HELP', 'DATA')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: DATA')

    def test_help_rset(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('HELP', 'RSET')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: RSET')

    def test_help_noop(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('HELP', 'NOOP')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: NOOP')

    def test_help_quit(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('HELP', 'QUIT')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: QUIT')

    def test_help_vrfy(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('HELP', 'VRFY')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: VRFY <address>')

    def test_help_bad_arg(self):
        with SMTP(*self.address) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('HELP me!')
            self.assertEqual(code, 501)
            self.assertEqual(response,
                             b'Supported commands: EHLO HELO MAIL RCPT '
                             b'DATA RSET NOOP QUIT VRFY')

    def test_expn(self):
        with SMTP(*self.address) as client:
            code, response = client.expn('anne@example.com')
            self.assertEqual(code, 502)
            self.assertEqual(response, b'EXPN not implemented')

    def test_mail_no_helo(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: send HELO first')

    def test_mail_no_arg(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('MAIL')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')

    def test_mail_no_from(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('MAIL <anne@example.com>')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')

    def test_mail_params_no_esmtp(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> SIZE=10000')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')

    def test_mail_params_esmtp(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> SIZE=10000')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')

    def test_mail_from_twice(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: nested MAIL command')

    def test_mail_from_malformed(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('MAIL FROM: Anne <anne@example.com>')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')

    def test_mail_malformed_params_esmtp(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> SIZE 10000')
            self.assertEqual(code, 501)
            self.assertEqual(
                response,
                b'Syntax: MAIL FROM: <address> [SP <mail-parameters>]')

    def test_mail_missing_params_esmtp(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('MAIL FROM: <anne@example.com> SIZE')
            self.assertEqual(code, 501)
            self.assertEqual(
                response,
                b'Syntax: MAIL FROM: <address> [SP <mail-parameters>]')

    def test_mail_unrecognized_params_esmtp(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> FOO=BAR')
            self.assertEqual(code, 555)
            self.assertEqual(
                response,
                b'MAIL FROM parameters not recognized or not implemented')

    def test_mail_params_bad_syntax_esmtp(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> #$%=!@#')
            self.assertEqual(code, 501)
            self.assertEqual(
                response,
                b'Syntax: MAIL FROM: <address> [SP <mail-parameters>]')

    def test_rcpt_no_helo(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('RCPT TO: <anne@example.com>')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: send HELO first')

    def test_rcpt_no_mail(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('RCPT TO: <anne@example.com>')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: need MAIL command')

    def test_rcpt_no_arg(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('RCPT')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: RCPT TO: <address>')

    def test_rcpt_no_arg_esmtp(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('RCPT')
            self.assertEqual(code, 501)
            self.assertEqual(
                response,
                b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')

    def test_rcpt_no_address(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('RCPT TO:')
            self.assertEqual(code, 501)
            self.assertEqual(
                response,
                b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')

    def test_rcpt_with_params_no_esmtp(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd(
                'RCPT TO: <bart@example.com> SIZE=1000')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: RCPT TO: <address>')

    def test_rcpt_with_bad_params(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd(
                'RCPT TO: <bart@example.com> #$%=!@#')
            self.assertEqual(code, 501)
            self.assertEqual(
                response,
                b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')

    def test_rcpt_with_unknown_params(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd(
                'RCPT TO: <bart@example.com> FOOBAR')
            self.assertEqual(code, 555)
            self.assertEqual(
                response,
                b'RCPT TO parameters not recognized or not implemented')

    def test_rset(self):
        with SMTP(*self.address) as client:
            code, response = client.rset()
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')

    def test_rset_with_arg(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('RSET FOO')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: RSET')

    def test_vrfy(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('VRFY <anne@example.com>')
            self.assertEqual(code, 252)
            self.assertEqual(
              response,
              b'Cannot VRFY user, but will accept message and attempt delivery'
              )

    def test_vrfy_no_arg(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('VRFY')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: VRFY <address>')

    def test_vrfy_not_an_address(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('VRFY @@')
            self.assertEqual(code, 502)
            self.assertEqual(response, b'Could not VRFY @@')

    def test_data_no_helo(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('DATA')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: send HELO first')

    def test_data_no_rcpt(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('DATA')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: need RCPT command')

    def test_data_invalid_params(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('RCPT TO: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('DATA FOOBAR')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: DATA')

    def test_empty_command(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('')
            self.assertEqual(code, 500)
            self.assertEqual(response, b'Error: bad syntax')

    def test_too_long_command(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('a' * 513)
            self.assertEqual(code, 500)
            self.assertEqual(response, b'Error: line too long')

    def test_unknown_command(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('FOOBAR')
            self.assertEqual(code, 500)
            self.assertEqual(
                response,
                b'Error: command "FOOBAR" not recognized')


class TestSMTPWithController(unittest.TestCase):
    def test_mail_with_size_too_large(self):
        controller = SizedController(Sink(), 9999)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> SIZE=10000')
            self.assertEqual(code, 552)
            self.assertEqual(
                response,
                b'Error: message size exceeds fixed maximum message size')

    def test_mail_with_compatible_smtputf8(self):
        controller = SMTPUTF8Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> SMTPUTF8')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')

    def test_mail_with_unrequited_smtputf8(self):
        controller = SMTPUTF8Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')

    def test_mail_with_incompatible_smtputf8(self):
        controller = SMTPUTF8Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> SMTPUTF8=YES')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Error: SMTPUTF8 takes no arguments')

    def test_mail_invalid_body(self):
        controller = Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> BODY 9BIT')
            self.assertEqual(code, 501)
            self.assertEqual(response,
                             b'Error: BODY can only be one of 7BIT, 8BITMIME')

    def test_esmtp_no_size_limit(self):
        controller = SizedController(Sink(), size=None)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            for line in response.splitlines():
                self.assertNotEqual(line[:4], b'SIZE')

    def test_process_message_error(self):
        controller = Controller(ErroringHandler())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            with self.assertRaises(SMTPDataError) as cm:
                client.sendmail('anne@example.com', ['bart@example.com'], """\
From: anne@example.com
To: bart@example.com
Subject: A test

Testing
""")
                self.assertEqual(cm.exception.code, 499)
                self.assertEqual(cm.exception.response,
                                 b'Could not accept the message')

    def test_too_long_message_body(self):
        controller = SizedController(Sink(), size=100)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.helo('example.com')
            mail = '\r\n'.join(['z' * 20] * 10)
            with self.assertRaises(SMTPResponseException) as cm:
                client.sendmail('anne@example.com', ['bart@example.com'], mail)
            self.assertEqual(cm.exception.smtp_code, 552)
            self.assertEqual(cm.exception.smtp_error,
                             b'Error: Too much mail data')

    def test_dots_escaped(self):
        handler = ReceivingHandler()
        controller = UTF8Controller(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.helo('example.com')
            mail = CRLF.join(['Test', '.', 'mail'])
            client.sendmail('anne@example.com', ['bart@example.com'], mail)
            self.assertEqual(len(handler.box), 1)
            self.assertEqual(handler.box[0], 'Test\r\n.\r\nmail')

    def test_unexpected_errors(self):
        handler = ErroringHandler()
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with ExitStack() as resources:
            # Suppress logging to the console during the tests.  Depending on
            # timing, the exception may or may not be logged.
            resources.enter_context(patch('aiosmtpd.smtp.log.exception'))
            client = resources.enter_context(
                SMTP(controller.hostname, controller.port))
            code, response = client.helo('example.com')
        self.assertEqual(code, 500)
        self.assertEqual(response, b'Error: (ValueError) test')
        self.assertIsInstance(handler.error, ValueError)

    def test_unexpected_errors_unhandled(self):
        handler = Sink()
        handler.error = None
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with ExitStack() as resources:
            # Suppress logging to the console during the tests.  Depending on
            # timing, the exception may or may not be logged.
            resources.enter_context(patch('aiosmtpd.smtp.log.exception'))
            client = resources.enter_context(
                SMTP(controller.hostname, controller.port))
            code, response = client.helo('example.com')
        self.assertEqual(code, 500)
        self.assertEqual(response, b'Error: (ValueError) test')
        # handler.error did not change because the handler does not have a
        # handle_exception() method.
        self.assertIsNone(handler.error)


class TestCustomizations(unittest.TestCase):
    def test_custom_hostname(self):
        controller = CustomHostnameController(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            self.assertEqual(response, bytes('custom.localhost', 'utf-8'))

    def test_custom_greeting(self):
        controller = CustomIdentController(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP() as client:
            code, msg = client.connect(controller.hostname, controller.port)
            self.assertEqual(code, 220)
            # The hostname prefix is unpredictable.
            self.assertEqual(msg[-22:], b'Identifying SMTP v2112')

    def test_default_greeting(self):
        controller = Controller(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP() as client:
            code, msg = client.connect(controller.hostname, controller.port)
            self.assertEqual(code, 220)
            # The hostname prefix is unpredictable.
            self.assertEqual(msg[-len(GREETING):], bytes(GREETING, 'utf-8'))

    def test_mail_invalid_body_param(self):
        controller = NoDecodeController(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP() as client:
            code, msg = client.connect(controller.hostname, controller.port)
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> BODY=FOOBAR')
            self.assertEqual(code, 501)
            self.assertEqual(
                response,
                b'Error: BODY can only be one of 7BIT, 8BITMIME')
