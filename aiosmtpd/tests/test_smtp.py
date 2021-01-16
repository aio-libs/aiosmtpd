"""Test the SMTP protocol."""

import time
import socket
import asyncio
import unittest
import warnings

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import (
    AuthResult,
    CALL_LIMIT_DEFAULT,
    Envelope as SMTPEnvelope,
    MISSING,
    SMTP as Server,
    Session as SMTPSession,
    __ident__ as GREETING,
    auth_mechanism,
)
from aiosmtpd.testing.helpers import (
    ReceivingHandler,
    SUPPORTED_COMMANDS_NOTLS,
    assert_auth_invalid,
    assert_auth_required,
    assert_auth_success,
    reset_connection,
    send_recv,
)
from base64 import b64encode
from contextlib import ExitStack
from smtplib import (
    SMTP,
    SMTPAuthenticationError,
    SMTPDataError,
    SMTPResponseException,
    SMTPServerDisconnected
)
from unittest.mock import Mock, PropertyMock, patch

from typing import ContextManager, Tuple, cast

CRLF = '\r\n'
BCRLF = b'\r\n'


ModuleResources = ExitStack()


def setUpModule():
    # Needed especially on FreeBSD because socket.getfqdn() is slow on that OS,
    # and oftentimes (not always, though) leads to Error
    ModuleResources.enter_context(patch("socket.getfqdn", return_value="localhost"))


def tearDownModule():
    ModuleResources.close()


def auth_callback(mechanism, login, password):
    if login and login.decode() == 'goodlogin':
        return True
    else:
        return False


class DecodingController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True, enable_SMTPUTF8=True,
                      auth_require_tls=False, auth_callback=auth_callback)


class PeekerHandler:
    sess: SMTPSession = None

    async def handle_MAIL(
            self, server, session, envelope, address, mail_options
    ):
        self.sess = session
        return "250 OK"

    async def auth_DENYMISSING(self, server, args):
        return MISSING

    async def auth_DENYFALSE(self, server, args):
        return False

    async def auth_NULL(
            self, server, args
    ):
        return "NULL_login"

    async def auth_NONE(self, server: Server, args):
        await server.push("235 2.7.0  Authentication Succeeded")
        return None

    async def auth_DONT(
            self, server, args
    ):
        return MISSING

    async def auth_WITH_UNDERSCORE(self, server, args):
        return "250 OK"

    @auth_mechanism("with-dash")
    async def auth_WITH_DASH(self, server, args):
        return "250 OK"

    async def auth_WITH__MULTI__DASH(self, server, args):
        return "250 OK"


class PeekerAuth:
    login: bytes = None
    password: bytes = None
    mechanism: str = None

    sess: SMTPSession = None
    login_data = None

    def auth_callback(
            self, mechanism: str, login: bytes, password: bytes
    ) -> bool:
        assert login is not None
        assert password is not None
        self.mechanism = mechanism
        self.login = login
        self.password = password
        return True

    def authenticator(
            self,
            server: Server,
            session: SMTPSession,
            envelope: SMTPEnvelope,
            mechanism: str,
            login_data: Tuple[bytes, bytes],
    ) -> AuthResult:
        self.sess = session
        self.mechanism = mechanism
        self.login_data = login_data
        userb, passb = login_data
        if userb == b"failme_with454":
            return AuthResult(
                success=False,
                handled=False,
                message="454 4.7.0 Temporary authentication failure",
            )
        else:
            self.login = userb
            self.password = passb
            return AuthResult(success=True, auth_data=login_data)


auth_peeker = PeekerAuth()


class DecodingControllerPeekAuth(Controller):
    def factory(self):
        self.server_kwargs["enable_SMTPUTF8"] = True
        return Server(self.handler, decode_data=True,
                      auth_require_tls=False,
                      auth_callback=auth_peeker.auth_callback,
                      **self.server_kwargs)


class DecodingControllerPeekAuthNewSystem(Controller):
    def factory(self):
        self.server_kwargs["enable_SMTPUTF8"] = True
        return Server(self.handler, decode_data=True,
                      auth_require_tls=False,
                      authenticator=auth_peeker.authenticator,
                      **self.server_kwargs)


class NoDecodeController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=False)


class TimeoutController(Controller):
    Delay: float = 2.0

    def factory(self):
        return Server(self.handler, timeout=self.Delay)


class RequiredAuthDecodingController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True, enable_SMTPUTF8=True,
                      auth_require_tls=False, auth_callback=auth_callback,
                      auth_required=True)


class StoreEnvelopeOnVRFYHandler:
    """Saves envelope for later inspection when handling VRFY."""
    envelope = None

    async def handle_VRFY(self, server, session, envelope, addr):
        self.envelope = envelope
        return '250 OK'


class SizedController(Controller):
    def __init__(self, handler, size):
        self.size = size
        super().__init__(handler)

    def factory(self):
        return Server(self.handler, data_size_limit=self.size)


class StrictASCIIController(Controller):
    def factory(self):
        return Server(self.handler, enable_SMTPUTF8=False, decode_data=True)


class CustomHostnameController(Controller):
    def factory(self):
        return Server(self.handler, hostname='custom.localhost')


class CustomIdentController(Controller):
    def factory(self):
        server = Server(self.handler, ident='Identifying SMTP v2112')
        return server


class ErroringHandler:
    error = None

    async def handle_DATA(self, server, session, envelope):
        return '499 Could not accept the message'

    async def handle_exception(self, error):
        self.error = error
        return '500 ErroringHandler handling error'


class ErroringHandlerCustomResponse:
    error = None

    async def handle_exception(self, error):
        self.error = error
        return '554 Persistent error: ({}) {}'.format(
            error.__class__.__name__, str(error))


class ErroringErrorHandler:
    error = None

    async def handle_exception(self, error):
        self.error = error
        raise ValueError('ErroringErrorHandler test')


class ErroringHandlerConnectionLost:
    error = None

    async def handle_DATA(self, server, session, envelope):
        raise ConnectionResetError('ErroringHandlerConnectionLost test')

    async def handle_exception(self, error):
        self.error = error


class UndescribableError(Exception):
    def __str__(self):
        raise Exception()


class UndescribableErrorHandler:
    error = None

    async def handle_exception(self, error):
        self.error = error
        raise UndescribableError()


class ErrorSMTP(Server):
    async def smtp_HELO(self, hostname):
        raise ValueError('test')


class ErrorController(Controller):
    def factory(self):
        return ErrorSMTP(self.handler)


class SleepingHeloHandler:
    async def handle_HELO(self, server, session, envelope, hostname):
        await asyncio.sleep(0.01)
        session.host_name = hostname
        return '250 {}'.format(server.hostname)


class TestProtocol(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.transport.write = self._write
        self.responses = []
        self._old_loop = asyncio.get_event_loop()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()
        asyncio.set_event_loop(self._old_loop)

    def _write(self, data):
        self.responses.append(data)

    def _get_protocol(self, *args, **kwargs):
        protocol = Server(*args, loop=self.loop, **kwargs)
        protocol.connection_made(self.transport)
        return protocol

    def test_honors_mail_delimeters(self):
        handler = ReceivingHandler()
        data = b'test\r\nmail\rdelimeters\nsaved\r\n'
        protocol = self._get_protocol(handler)
        protocol.data_received(BCRLF.join([
            b'HELO example.org',
            b'MAIL FROM: <anne@example.com>',
            b'RCPT TO: <anne@example.com>',
            b'DATA',
            data + b'.',
            b'QUIT\r\n'
            ]))
        try:
            self.loop.run_until_complete(protocol._handler_coroutine)
        except asyncio.CancelledError:
            pass
        self.assertEqual(len(handler.box), 1)
        self.assertEqual(handler.box[0].content, data)

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
        self.assertEqual(handler.box[0].content, b'')


class TestSMTP(unittest.TestCase):
    def setUp(self):
        controller = DecodingController(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_binary(self):
        with SMTP(*self.address) as client:
            client.sock.send(b"\x80FAIL\r\n")
            code, response = client.getreply()
            self.assertEqual(code, 500)
            self.assertEqual(response, b'Error: bad syntax')

    def test_binary_space(self):
        with SMTP(*self.address) as client:
            client.sock.send(b"\x80 FAIL\r\n")
            code, response = client.getreply()
            self.assertEqual(code, 500)
            self.assertEqual(response, b'Error: bad syntax')

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
            self.assertEqual(code, 250)

    def test_ehlo(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            lines = response.splitlines()
            expecteds = (
                bytes(socket.getfqdn(), 'utf-8'),
                b'SIZE 33554432',
                b'SMTPUTF8',
                b'AUTH LOGIN PLAIN',
                b'HELP',
            )
            for actual, expected in zip(lines, expecteds):
                self.assertEqual(actual, expected)

    def test_ehlo_duplicate(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.ehlo('example.org')
            self.assertEqual(code, 250)

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
            self.assertEqual(code, 250)

    def test_ehlo_then_helo(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.helo('example.org')
            self.assertEqual(code, 250)

    def test_noop(self):
        with SMTP(*self.address) as client:
            code, response = client.noop()
            self.assertEqual(code, 250)

    def test_noop_with_arg(self):
        with SMTP(*self.address) as client:
            # .noop() doesn't accept arguments.
            code, response = client.docmd('NOOP', 'ok')
            self.assertEqual(code, 250)

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
            self.assertEqual(response, SUPPORTED_COMMANDS_NOTLS)

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
            self.assertEqual(response, b'Syntax: NOOP [ignored]')

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

    def test_help_auth(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('HELP', 'AUTH')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: AUTH <mechanism>')

    def test_help_bad_arg(self):
        with SMTP(*self.address) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('HELP me!')
            self.assertEqual(code, 501)
            self.assertEqual(response, SUPPORTED_COMMANDS_NOTLS)

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

    # Test the workaround http://bugs.python.org/issue27931
    @patch('email._header_value_parser.AngleAddr.addr_spec',
           side_effect=IndexError, new_callable=PropertyMock)
    def test_mail_fail_parse_email(self, addr_spec):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('MAIL FROM: <""@example.com>')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')

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

    def test_rcpt_no_to(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('RCPT <anne@example.com')
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

    # Test the workaround http://bugs.python.org/issue27931
    @patch('email._header_value_parser.AngleAddr.addr_spec',
           new_callable=PropertyMock)
    def test_rcpt_fail_parse_email(self, addr_spec):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            addr_spec.side_effect = IndexError
            code, response = client.docmd('RCPT TO: <""@example.com>')
            self.assertEqual(code, 501)
            self.assertEqual(
                response,
                b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')

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
                b'Cannot VRFY user, but will accept message and '
                b'attempt delivery'
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
            self.assertEqual(response, b'Command line too long')

    def test_way_too_long_command(self):
        with SMTP(*self.address) as client:
            # Send a very large string to ensure it is broken
            # into several packets, which hits the inner
            # LimitOverrunError code path in _handle_client.
            client.send('a' * 1000000)
            code, response = client.docmd('a' * 1001)
            self.assertEqual(code, 500)
            self.assertEqual(response, b'Command line too long')
            code, response = client.docmd('NOOP')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')

    @patch("logging.Logger.warning")
    def test_unknown_command(self, mock_warning):
        with SMTP(*self.address) as client:
            code, response = client.docmd('FOOBAR')
            self.assertEqual(code, 500)
            self.assertEqual(
                response,
                b'Error: command "FOOBAR" not recognized')

    def test_auth_no_ehlo(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('AUTH')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: send EHLO first')

    def test_auth_helo(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('AUTH')
            self.assertEqual(code, 500)
            self.assertEqual(response, b"Error: command 'AUTH' not recognized")

    def test_auth_too_many_values(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('AUTH PLAIN NONE NONE')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Too many values')

    def test_auth_not_enough_values(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('AUTH')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Not enough value')

    def test_auth_not_supported_methods(self):
        for method in ('GSSAPI', 'DIGEST-MD5', 'MD5', 'CRAM-MD5'):
            with SMTP(*self.address) as client:
                client.ehlo('example.com')
                code, response = client.docmd('AUTH ' + method)
                self.assertEqual(code, 504)
                self.assertEqual(
                    response, b'5.5.4 Unrecognized authentication type')

    def test_auth_already_authenticated(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'AUTH PLAIN ' +
                b64encode(b'\0goodlogin\0goodpasswd').decode()
                )
            assert_auth_success(self, code, response)
            code, response = client.docmd('AUTH')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Already authenticated')

    def test_auth_plain_bad_base64_encoding(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('AUTH PLAIN not-b64')
            self.assertEqual(code, 501)
            self.assertEqual(response, b"5.5.2 Can't decode base64")

    def test_auth_login_bad_base64_encoding(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('AUTH LOGIN not-b64')
            self.assertEqual(code, 501)
            self.assertEqual(response, b"5.5.2 Can't decode base64")

    def test_auth_plain_bad_base64_length(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'AUTH PLAIN ' + b64encode(b'\0onlylogin').decode())
            self.assertEqual(code, 501)
            self.assertEqual(response, b"5.5.2 Can't split auth value")

    def test_auth_bad_credentials(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'AUTH PLAIN ' +
                b64encode(b'\0badlogin\0badpasswd').decode()
                )
            assert_auth_invalid(self, code, response)

    def test_auth_two_steps_good_credentials(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('AUTH PLAIN')
            self.assertEqual(code, 334)
            self.assertEqual(response, b'')
            code, response = client.docmd(
                b64encode(b'\0goodlogin\0goodpasswd').decode()
            )
            assert_auth_success(self, code, response)

    def test_auth_two_steps_bad_credentials(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('AUTH PLAIN')
            self.assertEqual(code, 334)
            self.assertEqual(response, b'')
            code, response = client.docmd(
                b64encode(b'\0badlogin\0badpasswd').decode()
            )
            assert_auth_invalid(self, code, response)

    def test_auth_two_steps_abort(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('AUTH PLAIN')
            self.assertEqual(code, 334)
            self.assertEqual(response, b'')
            # Suppress log.warning()
            with patch("logging.Logger.warning"):
                code, response = client.docmd('*')
            self.assertEqual(code, 501)
            self.assertEqual(response, b"5.7.0 Auth aborted")

    def test_auth_two_steps_bad_base64_encoding(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('AUTH PLAIN')
            self.assertEqual(code, 334)
            code, response = client.docmd("ab@%")
            self.assertEqual(code, 501)
            self.assertEqual(response, b"5.5.2 Can't decode base64")

    def test_auth_plain_good_credentials(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'AUTH PLAIN ' +
                b64encode(b'\0goodlogin\0goodpasswd').decode()
            )
            assert_auth_success(self, code, response)

    def test_auth_login_good_credentials(self):
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, response = client.docmd("AUTH LOGIN")
            self.assertEqual(code, 334)
            self.assertEqual(response, b"VXNlciBOYW1lAA==")
            code, response = client.docmd('Z29vZGxvZ2lu')  # "goodlogin"
            self.assertEqual(code, 334)
            self.assertEqual(response, b"UGFzc3dvcmQA")
            code, response = client.docmd('Z29vZHBhc3N3ZA==')  # "goodpassword"
            assert_auth_success(self, code, response)

    def test_auth_plain_null(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            response = client.docmd('AUTH PLAIN =')
            self.assertEqual(
                (501, b"5.5.2 Can't split auth value"),
                response
            )

    def test_auth_two_steps_no_credentials(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('AUTH PLAIN')
            self.assertEqual(code, 334)
            self.assertEqual(response, b'')
            # "AAA=" is Base64 encoded "\x00\x00", representing null username and null
            # password. See https://tools.ietf.org/html/rfc4616#page-3
            code, response = client.docmd("AAA=")
            assert_auth_invalid(self, code, response)

    def test_auth_login_multisteps_no_credentials(self):
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, response = client.docmd("AUTH LOGIN")
            self.assertEqual(code, 334)
            self.assertEqual(response, b"VXNlciBOYW1lAA==")
            code, response = client.docmd('=')
            self.assertEqual(code, 334)
            self.assertEqual(response, b"UGFzc3dvcmQA")
            code, response = client.docmd('=')
            assert_auth_invalid(self, code, response)


class TestSMTPAuth(unittest.TestCase):
    def setUp(self):
        self.handler = PeekerHandler()
        controller = DecodingControllerPeekAuth(
            self.handler, server_kwargs={"auth_exclude_mechanism": ["DONT"]}
        )
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_ehlo(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            lines = response.splitlines()
            expecteds = [
                bytes(socket.getfqdn(), 'utf-8'),
                b'SIZE 33554432',
                b'SMTPUTF8',
                (
                    b'AUTH DENYFALSE DENYMISSING LOGIN NONE NULL PLAIN '
                    b'WITH-DASH WITH-MULTI-DASH WITH_UNDERSCORE'
                ),
                b'HELP',
            ]
            assert lines == expecteds

    def test_auth_byclient_plain(self):
        with SMTP(*self.address) as client:
            client.user = "gooduser"
            client.password = "goodpass"
            client.ehlo("example.com")
            client.auth("plain", client.auth_plain)
        self.assertEqual(b"gooduser", auth_peeker.login)
        self.assertEqual(b"goodpass", auth_peeker.password)
        self.assertEqual("PLAIN", auth_peeker.mechanism)

    def test_auth_byclient_plain_notinitialok(self):
        with SMTP(*self.address) as client:
            client.user = "gooduser"
            client.password = "goodpass"
            client.ehlo("example.com")
            client.auth("plain", client.auth_plain, initial_response_ok=False)
        self.assertEqual(b"gooduser", auth_peeker.login)
        self.assertEqual(b"goodpass", auth_peeker.password)
        self.assertEqual("PLAIN", auth_peeker.mechanism)

    def test_auth_byclient_login(self):
        with SMTP(*self.address) as client:
            client.user = "gooduser"
            client.password = "goodpass"
            client.ehlo("example.com")
            client.auth("login", client.auth_login)
        self.assertEqual(b"gooduser", auth_peeker.login)
        self.assertEqual(b"goodpass", auth_peeker.password)
        self.assertEqual("LOGIN", auth_peeker.mechanism)

    # Mark this as expectedFailure because smtplib.SMTP implementation in Python>=3.5
    # is buggy. See bpo-27820
    @unittest.expectedFailure
    def test_auth_byclient_login_notinitialok(self):
        with SMTP(*self.address) as client:
            client.user = "gooduser"
            client.password = "goodpass"
            client.ehlo("example.com")
            client.auth("login", client.auth_login, initial_response_ok=False)
        self.assertEqual(b"gooduser", auth_peeker.login)
        self.assertEqual(b"goodpass", auth_peeker.password)
        self.assertEqual("LOGIN", auth_peeker.mechanism)

    def test_auth_plain_null_credential(self):
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, response = client.docmd("AUTH PLAIN")
            self.assertEqual(code, 334)
            self.assertEqual(response, b"")
            # "AAA=" is Base64 encoded "\x00\x00", representing null username and
            # null password. See https://tools.ietf.org/html/rfc4616#page-3
            code, response = client.docmd("AAA=")
            assert_auth_success(self, code, response)
            self.assertEqual(auth_peeker.login, b"")
            self.assertEqual(auth_peeker.password, b"")
            response = client.mail("alice@example.com")
            assert response == (250, b"OK")

    def test_auth_login_null_credential(self):
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, response = client.docmd("AUTH LOGIN")
            self.assertEqual(code, 334)
            self.assertEqual(response, b"VXNlciBOYW1lAA==")
            code, response = client.docmd('=')
            self.assertEqual(code, 334)
            self.assertEqual(response, b"UGFzc3dvcmQA")
            code, response = client.docmd('=')
            assert_auth_success(self, code, response)
            assert auth_peeker.mechanism == "LOGIN"
            assert auth_peeker.login == b""
            assert auth_peeker.password == b""
            response = client.mail("alice@example.com")
            assert response == (250, b"OK")

    def test_auth_login_abort_login(self):
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, response = client.docmd("AUTH LOGIN")
            self.assertEqual(code, 334)
            self.assertEqual(response, b"VXNlciBOYW1lAA==")
            # Suppress log.warning()
            with patch("logging.Logger.warning"):
                code, response = client.docmd('*')
            self.assertEqual(code, 501)
            self.assertEqual(response, b"5.7.0 Auth aborted")

    def test_auth_login_abort_password(self):
        auth_peeker.return_val = False
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, response = client.docmd("AUTH LOGIN")
            self.assertEqual(code, 334)
            self.assertEqual(response, b"VXNlciBOYW1lAA==")
            code, response = client.docmd('=')
            self.assertEqual(code, 334)
            self.assertEqual(response, b"UGFzc3dvcmQA")
            # Suppress log.warning()
            with patch("logging.Logger.warning"):
                code, response = client.docmd('*')
            self.assertEqual(code, 501)
            self.assertEqual(response, b"5.7.0 Auth aborted")

    def test_auth_custom_mechanism(self):
        auth_peeker.return_val = False
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, response = client.docmd("AUTH NULL")
            assert_auth_success(self, code, response)

    def test_auth_disabled_mechanism(self):
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, response = client.docmd("AUTH DONT")
            self.assertEqual(code, 504)
            self.assertEqual(response,
                             b"5.5.4 Unrecognized authentication type")

    def test_rset_maintain_authenticated(self):
        """RSET resets only Envelope not Session"""
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, mesg = client.docmd("AUTH PLAIN")
            self.assertEqual(code, 334)
            self.assertEqual(mesg, b"")
            # "AAA=" is Base64 encoded "\x00\x00", representing null username and
            # null password. See https://tools.ietf.org/html/rfc4616#page-3
            code, mesg = client.docmd("AAA=")
            assert_auth_success(self, code, mesg)
            self.assertEqual(auth_peeker.login, b"")
            self.assertEqual(auth_peeker.password, b"")
            code, mesg = client.mail("alice@example.com")
            sess: SMTPSession = self.handler.sess
            self.assertEqual(sess.login_data, b"")
            code, mesg = client.rset()
            self.assertEqual(code, 250)
            code, mesg = client.docmd("AUTH PLAIN")
            self.assertEqual(503, code)
            self.assertEqual(b'Already authenticated', mesg)

    def test_auth_individually(self):
        """AUTH state of different clients must be independent"""
        with SMTP(*self.address) as client1, SMTP(*self.address) as client2:
            for client in client1, client2:
                client.ehlo("example.com")
                code, mesg = client.docmd("AUTH PLAIN")
                self.assertEqual(code, 334)
                self.assertEqual(mesg, b"")
                # "AAA=" is Base64 encoded "\x00\x00", representing null username and
                # null password. See https://tools.ietf.org/html/rfc4616#page-3
                code, mesg = client.docmd("AAA=")
                assert_auth_success(self, code, mesg)

    def test_auth_NONE(self):
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, mesg = client.docmd("AUTH NONE")
            self.assertEqual(code, 235)
            self.assertEqual(mesg, b"2.7.0  Authentication Succeeded")

    def test_auth_DENYFALSE(self):
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, mesg = client.docmd("AUTH DENYFALSE")
            self.assertEqual(code, 535)

    def test_auth_DENYMISSING(self):
        with SMTP(*self.address) as client:
            client.ehlo("example.com")
            code, mesg = client.docmd("AUTH DENYMISSING")
            self.assertEqual(code, 535)


class TestSMTPAuthNew(unittest.TestCase):
    def setUp(self):
        self.handler = PeekerHandler()
        controller = DecodingControllerPeekAuthNewSystem(
            self.handler, server_kwargs={"auth_exclude_mechanism": ["DONT"]}
        )
        self.addCleanup(controller.stop)
        controller.start()
        self.address = (controller.hostname, controller.port)

    def test_newauth_success(self):
        with SMTP(*self.address) as client:
            client.user = "gooduser"
            client.password = "goodpass"
            client.ehlo("example.com")
            client.auth("plain", client.auth_plain)
        peer = auth_peeker.sess.peer
        self.assertIn(peer[0], {"::1", "127.0.0.1", "localhost"})
        self.assertGreater(peer[1], 0)
        assert auth_peeker.sess.authenticated
        assert auth_peeker.sess.login_data
        assert auth_peeker.sess.auth_data == (b"gooduser", b"goodpass")
        assert auth_peeker.login_data == (b"gooduser", b"goodpass")

    def test_newauth_fail_withmessage(self):
        with SMTP(*self.address) as client:
            client.user = "failme_with454"
            client.password = "anypass"
            client.ehlo("example.com")
            with self.assertRaises(SMTPAuthenticationError) as cm:
                client.auth("plain", client.auth_plain)
        self.assertEqual(cm.exception.smtp_code, 454)
        self.assertEqual(cm.exception.smtp_error,
                         b"4.7.0 Temporary authentication failure")
        peer = auth_peeker.sess.peer
        self.assertIn(peer[0], {"::1", "127.0.0.1", "localhost"})
        self.assertGreater(peer[1], 0)
        self.assertIsNone(auth_peeker.sess.login_data)
        self.assertEqual((b"failme_with454", b"anypass"), auth_peeker.login_data)


class TestRequiredAuthentication(unittest.TestCase):
    def setUp(self):
        self.resource = ExitStack()
        self.addCleanup(self.resource.close)

        # Suppress auth_req_but_no_tls warning
        self.resource.enter_context(cast(ContextManager, warnings.catch_warnings()))
        warnings.simplefilter("ignore", category=UserWarning)

        self.resource.enter_context(
            cast(ContextManager, patch("logging.Logger.warning"))
        )

        controller = RequiredAuthDecodingController(Sink)
        self.addCleanup(controller.stop)
        controller.start()
        self.address = (controller.hostname, controller.port)

    def test_help_unauthenticated(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('HELP')
            assert_auth_required(self, code, response)

    def test_vrfy_unauthenticated(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('VRFY <anne@example.com>')
            assert_auth_required(self, code, response)

    def test_mail_unauthenticated(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            assert_auth_required(self, code, response)

    def test_rcpt_unauthenticated(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('RCPT TO: <anne@example.com>')
            assert_auth_required(self, code, response)

    def test_data_unauthenticated(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('DATA')
            assert_auth_required(self, code, response)

    def test_help_authenticated(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'AUTH PLAIN ' +
                b64encode(b'\0goodlogin\0goodpasswd').decode()
            )
            assert_auth_success(self, code, response)
            code, response = client.docmd('HELP')
            self.assertEqual(code, 250)
            self.assertEqual(response, SUPPORTED_COMMANDS_NOTLS)

    def test_vrfy_authenticated(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'AUTH PLAIN ' +
                b64encode(b'\0goodlogin\0goodpasswd').decode()
            )
            assert_auth_success(self, 235, response)
            code, response = client.docmd('VRFY <anne@example.com>')
            self.assertEqual(code, 252)
            self.assertEqual(
                response,
                b'Cannot VRFY user, but will accept message and '
                b'attempt delivery'
            )

    def test_mail_authenticated(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'AUTH PLAIN ' +
                b64encode(b'\0goodlogin\0goodpasswd').decode()
            )
            assert_auth_success(self, code, response)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')

    def test_rcpt_authenticated(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'AUTH PLAIN ' +
                b64encode(b'\0goodlogin\0goodpasswd').decode()
            )
            assert_auth_success(self, code, response)
            code, response = client.docmd('RCPT TO: <anne@example.com>')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: need MAIL command')

    def test_data_authenticated(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('DATA')
            assert_auth_required(self, code, response)


class TestResetCommands(unittest.TestCase):
    """Test that sender and recipients are reset on RSET, HELO, and EHLO.

    The tests below issue each command twice with different addresses and
    verify that mail_from and rcpt_tos have been replacecd.
    """
    expected_envelope_data = [
        # Pre-RSET/HELO/EHLO envelope data.
        dict(
            mail_from='anne@example.com',
            rcpt_tos=['bart@example.com', 'cate@example.com'],
            ),
        dict(
            mail_from='dave@example.com',
            rcpt_tos=['elle@example.com', 'fred@example.com'],
            ),
        ]

    def setUp(self):
        self._handler = StoreEnvelopeOnVRFYHandler()
        self._controller = DecodingController(self._handler)
        self._controller.start()
        self._address = (self._controller.hostname, self._controller.port)
        self.addCleanup(self._controller.stop)

    def _send_envelope_data(self, client, mail_from, rcpt_tos):
        client.mail(mail_from)
        for rcpt in rcpt_tos:
            client.rcpt(rcpt)

    def test_helo(self):
        with SMTP(*self._address) as client:
            # Each time through the loop, the HELO will reset the envelope.
            for data in self.expected_envelope_data:
                client.helo('example.com')
                # Save the envelope in the handler.
                client.vrfy('zuzu@example.com')
                self.assertIsNone(self._handler.envelope.mail_from)
                self.assertEqual(len(self._handler.envelope.rcpt_tos), 0)
                self._send_envelope_data(client, **data)
                client.vrfy('zuzu@example.com')
                self.assertEqual(
                    self._handler.envelope.mail_from, data['mail_from'])
                self.assertEqual(
                    self._handler.envelope.rcpt_tos, data['rcpt_tos'])

    def test_ehlo(self):
        with SMTP(*self._address) as client:
            # Each time through the loop, the EHLO will reset the envelope.
            for data in self.expected_envelope_data:
                client.ehlo('example.com')
                # Save the envelope in the handler.
                client.vrfy('zuzu@example.com')
                self.assertIsNone(self._handler.envelope.mail_from)
                self.assertEqual(len(self._handler.envelope.rcpt_tos), 0)
                self._send_envelope_data(client, **data)
                client.vrfy('zuzu@example.com')
                self.assertEqual(
                    self._handler.envelope.mail_from, data['mail_from'])
                self.assertEqual(
                    self._handler.envelope.rcpt_tos, data['rcpt_tos'])

    def test_rset(self):
        with SMTP(*self._address) as client:
            client.helo('example.com')
            # Each time through the loop, the RSET will reset the envelope.
            for data in self.expected_envelope_data:
                self._send_envelope_data(client, **data)
                # Save the envelope in the handler.
                client.vrfy('zuzu@example.com')
                self.assertEqual(
                    self._handler.envelope.mail_from, data['mail_from'])
                self.assertEqual(
                    self._handler.envelope.rcpt_tos, data['rcpt_tos'])
                # Reset the envelope explicitly.
                client.rset()
                client.vrfy('zuzu@example.com')
                self.assertIsNone(self._handler.envelope.mail_from)
                self.assertEqual(len(self._handler.envelope.rcpt_tos), 0)


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
        handler = ReceivingHandler()
        controller = Controller(handler)
        controller.start()
        self.addCleanup(controller.stop)
        recipient = 'bart\xCB@example.com'
        sender = 'anne\xCB@example.com'
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            client.send(bytes(
                'MAIL FROM: <' + sender + '> SMTPUTF8\r\n',
                encoding='utf-8'))
            code, response = client.getreply()
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')
            client.send(bytes(
                'RCPT TO: <' + recipient + '>\r\n',
                encoding='utf-8'))
            code, response = client.getreply()
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')
            code, response = client.data('')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')
        self.assertEqual(handler.box[0].rcpt_tos[0], recipient)
        self.assertEqual(handler.box[0].mail_from, sender)
        peer = handler.boxed_sess[0].peer
        self.assertIn(peer[0], {"::1", "127.0.0.1", "localhost"})
        self.assertNotEqual(peer[1], 0)

    def test_mail_with_unrequited_smtputf8(self):
        controller = Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')

    def test_mail_with_incompatible_smtputf8(self):
        controller = Controller(Sink())
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
            self.assertEqual(cm.exception.smtp_code, 499)
            self.assertEqual(cm.exception.smtp_error,
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

    def test_too_long_body_delay_error(self):
        size, sock = 20, None

        cont = Controller(Sink(), hostname="localhost",
                          server_kwargs={"data_size_limit": size})
        self.addCleanup(cont.stop)
        cont.start()

        with socket.socket() as sock:
            sock.connect((cont.hostname, cont.port))
            rslt = send_recv(sock, b"EHLO example.com")
            self.assertTrue(rslt.startswith(b"220"))
            rslt = send_recv(sock, b"MAIL FROM: <anne@example.com>")
            self.assertTrue(rslt.startswith(b"250"))
            rslt = send_recv(sock, b"RCPT TO: <bruce@example.com>")
            self.assertTrue(rslt.startswith(b"250"))
            rslt = send_recv(sock, b"DATA")
            self.assertTrue(rslt.startswith(b"354"))
            rslt = send_recv(sock, b"a" * (size + 3))
            # Must NOT receive status code here even if data is too much
            self.assertEqual(b"", rslt)
            rslt = send_recv(sock, b"\r\n.")
            # *NOW* we must receive status code
            self.assertEqual(b"552 Error: Too much mail data\r\n", rslt)

    def test_data_line_too_long(self):
        handler = ReceivingHandler()
        controller = NoDecodeController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.helo('example.com')
            mail = b'\r\n'.join([b'a' * 5555] * 3)
            with self.assertRaises(SMTPDataError) as cm:
                client.sendmail('anne@example.com', ['bart@example.com'], mail)
        self.assertEqual(cm.exception.smtp_code, 500)
        self.assertEqual(cm.exception.smtp_error,
                         b'Line too long (see RFC5321 4.5.3.1.6)')

    def test_too_long_line_delay_error(self):
        sock = None

        cont = Controller(Sink(), hostname="localhost")
        self.addCleanup(cont.stop)
        cont.start()

        with socket.socket() as sock:
            sock.connect((cont.hostname, cont.port))
            rslt = send_recv(sock, b"EHLO example.com")
            self.assertTrue(rslt.startswith(b"220"))
            rslt = send_recv(sock, b"MAIL FROM: <anne@example.com>")
            self.assertTrue(rslt.startswith(b"250"))
            rslt = send_recv(sock, b"RCPT TO: <bruce@example.com>")
            self.assertTrue(rslt.startswith(b"250"))
            rslt = send_recv(sock, b"DATA")
            self.assertTrue(rslt.startswith(b"354"))
            rslt = send_recv(sock, b"a" * (Server.line_length_limit + 3))
            # Must NOT receive status code here even if data is too much
            self.assertEqual(b"", rslt)
            rslt = send_recv(sock, b"\r\n.")
            # *NOW* we must receive status code
            self.assertEqual(b"500 Line too long (see RFC5321 4.5.3.1.6)\r\n", rslt)

    def test_too_long_lines_then_too_long_body(self):
        # If "too long line" state was reached before "too much data" happens,
        # SMTP should respond with '500' instead of '552'
        size = 2000
        controller = SizedController(Sink(), size=size)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.helo('example.com')
            mail = '\r\n'.join(['z' * (size - 1)] * 2)
            with self.assertRaises(SMTPResponseException) as cm:
                client.sendmail('anne@example.com', ['bart@example.com'], mail)
        self.assertEqual(cm.exception.smtp_code, 500)
        self.assertEqual(cm.exception.smtp_error,
                         b'Line too long (see RFC5321 4.5.3.1.6)')

    def test_too_long_body_then_too_long_lines(self):
        # If "too much mail" state was reached before "too long line" gets received,
        # SMTP should respond with '552' instead of '500'
        controller = SizedController(Sink(), size=700)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.helo('example.com')
            mail = '\r\n'.join(['z' * 76] * 10 + ["a" * 1100] * 2)
            with self.assertRaises(SMTPResponseException) as cm:
                client.sendmail('anne@example.com', ['bart@example.com'], mail)
            self.assertEqual(cm.exception.smtp_code, 552)
            self.assertEqual(cm.exception.smtp_error,
                             b'Error: Too much mail data')

    def test_long_line_double_count(self):
        controller = SizedController(Sink(), size=10000)
        # With a read limit of 1001 bytes in aiosmtp.SMTP, asyncio.StreamReader
        # returns too-long lines of length up to 2002 bytes.
        # This test ensures that bytes in partial lines are only counted once.
        # If the implementation has a double-counting bug, then a message of
        # 9998 bytes + CRLF will raise SMTPResponseException.
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.helo('example.com')
            mail = 'z' * 9998
            with self.assertRaises(SMTPDataError) as cm:
                client.sendmail('anne@example.com', ['bart@example.com'], mail)
        self.assertEqual(cm.exception.smtp_code, 500)
        self.assertEqual(cm.exception.smtp_error,
                         b'Line too long (see RFC5321 4.5.3.1.6)')

    @patch("aiosmtpd.smtp.EMPTY_BARR")
    def test_long_line_leak(self, mock_ebarr):
        # Simulates situation where readuntil() does not raise LimitOverrunError,
        # but somehow the line_fragments when join()ed resulted in a too-long line

        # Hijack EMPTY_BARR.join() to return a bytes object that's definitely too long
        mock_ebarr.join.return_value = (b"a" * 1010)

        controller = Controller(Sink())
        self.addCleanup(controller.stop)
        controller.start()
        with SMTP(controller.hostname, controller.port) as client:
            client.helo('example.com')
            mail = 'z' * 72  # Make sure this is small and definitely within limits
            with self.assertRaises(SMTPDataError) as cm:
                client.sendmail('anne@example.com', ['bart@example.com'], mail)
        self.assertEqual(cm.exception.smtp_code, 500)
        self.assertEqual(cm.exception.smtp_error,
                         b'Line too long (see RFC5321 4.5.3.1.6)')

    def test_dots_escaped(self):
        handler = ReceivingHandler()
        controller = DecodingController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.helo('example.com')
            mail = CRLF.join(['Test', '.', 'mail'])
            client.sendmail('anne@example.com', ['bart@example.com'], mail)
            self.assertEqual(len(handler.box), 1)
            self.assertEqual(handler.box[0].content, 'Test\r\n.\r\nmail\r\n')

    # Suppress logging to the console during the tests.  Depending on
    # timing, the exception may or may not be logged.
    @patch("logging.Logger.exception")
    def test_unexpected_errors(self, mock_logex):
        handler = ErroringHandler()
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.helo('example.com')
        self.assertEqual(code, 500)
        self.assertEqual(mesg, b'ErroringHandler handling error')
        self.assertIsInstance(handler.error, ValueError)

    # Suppress logging to the console during the tests.  Depending on
    # timing, the exception may or may not be logged.
    @patch("logging.Logger.exception")
    def test_unexpected_errors_unhandled(self, mock_logex):
        handler = Sink()
        handler.error = None
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.helo('example.com')
        self.assertEqual(code, 500)
        self.assertEqual(mesg, b'Error: (ValueError) test')
        # handler.error did not change because the handler does not have a
        # handle_exception() method.
        self.assertIsNone(handler.error)

    # Suppress logging to the console during the tests.  Depending on
    # timing, the exception may or may not be logged.
    @patch("logging.Logger.exception")
    def test_unexpected_errors_custom_response(self, mock_logex):
        handler = ErroringHandlerCustomResponse()
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.helo('example.com')
        self.assertEqual(code, 554)
        self.assertEqual(mesg, b'Persistent error: (ValueError) test')
        self.assertIsInstance(handler.error, ValueError)

    # Suppress logging to the console during the tests.  Depending on
    # timing, the exception may or may not be logged.
    @patch("logging.Logger.exception")
    def test_exception_handler_exception(self, mock_logex):
        handler = ErroringErrorHandler()
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.helo('example.com')
        self.assertEqual(code, 500)
        self.assertEqual(mesg, b'Error: (ValueError) ErroringErrorHandler test')
        self.assertIsInstance(handler.error, ValueError)

    def test_exception_handler_multiple_connections_lost(self):
        handler = ErroringHandlerConnectionLost()
        controller = Controller(handler)
        self.addCleanup(controller.stop)
        controller.start()
        with SMTP(controller.hostname, controller.port) as client1:
            code, mesg = client1.ehlo('example.com')
            self.assertEqual(code, 250)
            with SMTP(controller.hostname, controller.port) as client2:
                code, mesg = client2.ehlo('example.com')
                self.assertEqual(code, 250)
                with self.assertRaises(SMTPServerDisconnected) as cm:
                    mail = CRLF.join(['Test', '.', 'mail'])
                    client2.sendmail(
                        'anne@example.com',
                        ['bart@example.com'],
                        mail)
                self.assertIsInstance(cm.exception, SMTPServerDisconnected)
                self.assertEqual(handler.error, None)
                # At this point connection should be down
                with self.assertRaises(SMTPServerDisconnected) as cm:
                    client2.mail("alice@example.com")
                self.assertEqual("please run connect() first", str(cm.exception))
            # client1 shouldn't be affected.
            code, mesg = client1.mail("alice@example.com")
            self.assertEqual(code, 250)

    # Suppress logging to the console during the tests.  Depending on
    # timing, the exception may or may not be logged.
    @patch("logging.Logger.exception")
    def test_exception_handler_undescribable(self, mock_logex):
        handler = UndescribableErrorHandler()
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.helo('example.com')
        self.assertEqual(code, 500)
        self.assertEqual(mesg, b'Error: Cannot describe error')
        self.assertIsInstance(handler.error, ValueError)

    def test_bad_encodings(self):
        handler = ReceivingHandler()
        controller = DecodingController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.helo('example.com')
            mail_from = b'anne\xFF@example.com'
            mail_to = b'bart\xFF@example.com'
            client.ehlo('test')
            client.send(b'MAIL FROM:' + mail_from + b'\r\n')
            code, response = client.getreply()
            self.assertEqual(code, 250)
            client.send(b'RCPT TO:' + mail_to + b'\r\n')
            code, response = client.getreply()
            self.assertEqual(code, 250)
            client.data('Test mail')
            self.assertEqual(len(handler.box), 1)
            envelope = handler.box[0]
            mail_from2 = envelope.mail_from.encode(
                'utf-8', errors='surrogateescape')
            self.assertEqual(mail_from2, mail_from)
            mail_to2 = envelope.rcpt_tos[0].encode(
                'utf-8', errors='surrogateescape')
            self.assertEqual(mail_to2, mail_to)


class TestCustomizations(unittest.TestCase):
    def test_custom_hostname(self):
        controller = CustomHostnameController(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            self.assertEqual(response, bytes('custom.localhost', 'utf-8'))

    def test_custom_greeting(self):
        controller = CustomIdentController(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP() as client:
            code, msg = client.connect(controller.hostname, controller.port)
            self.assertEqual(code, 220)
            # The hostname prefix is unpredictable.
            self.assertEqual(msg[-22:], b'Identifying SMTP v2112')

    def test_default_greeting(self):
        controller = Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP() as client:
            code, msg = client.connect(controller.hostname, controller.port)
            self.assertEqual(code, 220)
            # The hostname prefix is unpredictable.
            self.assertEqual(msg[-len(GREETING):], bytes(GREETING, 'utf-8'))

    def test_mail_invalid_body_param(self):
        controller = NoDecodeController(Sink())
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


class TestClientCrash(unittest.TestCase):
    # GH#62 - if the client crashes during the SMTP dialog we want to make
    # sure we don't get tracebacks where we call readline().
    def setUp(self):
        controller = Controller(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_connection_reset_during_DATA(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('RCPT TO: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('DATA')
            self.assertEqual(code, 354)
            # Start sending the DATA but reset the connection before that
            # completes, i.e. before the .\r\n
            client.send(b'From: <anne@example.com>')
            reset_connection(client)
            # The connection should be disconnected, so trying to do another
            # command from here will give us an exception.  In GH#62, the
            # server just hung.
            self.assertRaises(SMTPServerDisconnected, client.noop)

    def test_connection_reset_during_command(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            # Start sending a command but reset the connection before that
            # completes, i.e. before the \r\n
            client.send('MAIL FROM: <anne')
            reset_connection(client)
            # The connection should be disconnected, so trying to do another
            # command from here will give us an exception.  In GH#62, the
            # server just hung.
            self.assertRaises(SMTPServerDisconnected, client.noop)

    def test_connection_reset_in_long_command(self):
        with SMTP(*self.address) as client:
            client.send('F' + 5555 * 'O')  # without CRLF
            reset_connection(client)

    def test_close_in_command(self):
        with SMTP(*self.address) as client:
            # Don't include the CRLF.
            client.send('FOO')
            client.close()

    def test_close_in_long_command(self):
        with SMTP(*self.address) as client:
            client.send('F' + 5555 * 'O')  # without CRLF
            client.close()

    def test_close_in_data(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('RCPT TO: <bart@example.com>')
            self.assertEqual(code, 250)
            code, response = client.docmd('DATA')
            self.assertEqual(code, 354)
            # Don't include the CRLF.
            client.send('FOO')
            client.close()


class TestStrictASCII(unittest.TestCase):
    def setUp(self):
        controller = StrictASCIIController(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_ehlo(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            lines = response.splitlines()
            self.assertNotIn(b'SMTPUTF8', lines)

    def test_bad_encoded_param(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            client.send(b'MAIL FROM: <anne\xFF@example.com>\r\n')
            code, response = client.getreply()
            self.assertEqual(code, 500)
            self.assertIn(b'Error: strict ASCII mode', response)

    def test_mail_param(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> SMTPUTF8')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Error: SMTPUTF8 disabled')

    def test_data(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            with self.assertRaises(SMTPDataError) as cm:
                client.sendmail('anne@example.com', ['bart@example.com'], b"""\
From: anne@example.com
To: bart@example.com
Subject: A test

Testing\xFF
""")
            self.assertEqual(cm.exception.smtp_code, 500)
            self.assertIn(b'Error: strict ASCII mode', cm.exception.smtp_error)


class TestSleepingHandler(unittest.TestCase):
    def setUp(self):
        controller = NoDecodeController(SleepingHeloHandler())
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_close_after_helo(self):
        with SMTP(*self.address) as client:
            client.send('HELO example.com\r\n')
            client.sock.shutdown(socket.SHUT_WR)
            self.assertRaises(SMTPServerDisconnected, client.getreply)


class TestTimeout(unittest.TestCase):
    def setUp(self):
        controller = TimeoutController(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_timeout(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            time.sleep(0.1 + TimeoutController.Delay)
            self.assertRaises(SMTPServerDisconnected, client.getreply)


class TestAuthArgs(unittest.TestCase):
    @patch("logging.Logger.warning")
    @patch("aiosmtpd.smtp.warn")
    def test_warn_authreqnotls(self, mock_warn: Mock, mock_warning: Mock):
        """If auth_required=True while auth_require_tls=False, emit warning"""
        _ = Server(Sink(), auth_required=True, auth_require_tls=False)
        mock_warn.assert_any_call(
            "Requiring AUTH while not requiring TLS "
            "can lead to security vulnerabilities!"
        )
        mock_warning.assert_any_call(
            "auth_required == True but auth_require_tls == False"
        )

    @patch("logging.Logger.info")
    def test_log_authmechanisms(self, mock_info: Mock):
        """At __init__ list of AUTH mechanisms must be logged"""
        server = Server(Sink())
        auth_mechs = sorted(
            m.replace("auth_", "") + "(builtin)"
            for m in dir(server)
            if m.startswith("auth_")
        )
        mock_info.assert_any_call(
            f"Available AUTH mechanisms: {' '.join(auth_mechs)}"
        )

    def test_authmechname_decorator_badname(self):
        self.assertRaises(ValueError, auth_mechanism, "has space")
        self.assertRaises(ValueError, auth_mechanism, "has.dot")
        self.assertRaises(ValueError, auth_mechanism, "has/slash")


class TestLimits(unittest.TestCase):
    @patch("logging.Logger.warning")
    def test_all_limit_15(self, mock_warning):
        kwargs = dict(
            command_call_limit=15,
        )
        controller = Controller(Sink(), server_kwargs=kwargs)
        self.addCleanup(controller.stop)
        controller.start()
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.ehlo('example.com')
            self.assertEqual(250, code)
            for _ in range(0, 15):
                code, mesg = client.noop()
                self.assertEqual(250, code)
            code, mesg = client.noop()
            self.assertEqual(421, code)
            self.assertEqual(b"4.7.0 NOOP sent too many times", mesg)
            with self.assertRaises(SMTPServerDisconnected):
                client.noop()

    @patch("logging.Logger.warning")
    def test_different_limits(self, mock_warning):
        noop_max, expn_max = 15, 5
        kwargs = dict(
            command_call_limit={"NOOP": noop_max, "EXPN": expn_max},
        )
        controller = Controller(Sink(), server_kwargs=kwargs)
        self.addCleanup(controller.stop)
        controller.start()
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.ehlo('example.com')
            self.assertEqual(250, code)
            for _ in range(0, noop_max):
                code, mesg = client.noop()
                self.assertEqual(250, code)
            code, mesg = client.noop()
            self.assertEqual(421, code)
            self.assertEqual(b"4.7.0 NOOP sent too many times", mesg)
            with self.assertRaises(SMTPServerDisconnected):
                client.noop()
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.ehlo('example.com')
            self.assertEqual(250, code)
            for _ in range(0, expn_max):
                code, mesg = client.expn("alice@example.com")
                self.assertEqual(502, code)
            code, mesg = client.expn("alice@example.com")
            self.assertEqual(421, code)
            self.assertEqual(b"4.7.0 EXPN sent too many times", mesg)
            with self.assertRaises(SMTPServerDisconnected):
                client.noop()
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.ehlo('example.com')
            self.assertEqual(250, code)
            for _ in range(0, CALL_LIMIT_DEFAULT):
                code, mesg = client.vrfy("alice@example.com")
                self.assertEqual(252, code)
            code, mesg = client.vrfy("alice@example.com")
            self.assertEqual(421, code)
            self.assertEqual(b"4.7.0 VRFY sent too many times", mesg)
            with self.assertRaises(SMTPServerDisconnected):
                client.noop()

    @patch("logging.Logger.warning")
    def test_different_limits_custom_default(self, mock_warning):
        # Important: make sure default_max > CALL_LIMIT_DEFAULT
        # Others can be set small to cut down on testing time, but must be different
        noop_max, expn_max, default_max = 7, 5, 25
        self.assertGreater(default_max, CALL_LIMIT_DEFAULT)
        self.assertNotEqual(noop_max, expn_max)
        kwargs = dict(
            command_call_limit={"NOOP": noop_max, "EXPN": expn_max, "*": default_max},
        )
        controller = Controller(Sink(), server_kwargs=kwargs)
        self.addCleanup(controller.stop)
        controller.start()
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.ehlo('example.com')
            self.assertEqual(250, code)
            for _ in range(0, noop_max):
                code, mesg = client.noop()
                self.assertEqual(250, code)
            code, mesg = client.noop()
            self.assertEqual(421, code)
            self.assertEqual(b"4.7.0 NOOP sent too many times", mesg)
            with self.assertRaises(SMTPServerDisconnected):
                client.noop()
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.ehlo('example.com')
            self.assertEqual(250, code)
            for _ in range(0, expn_max):
                code, mesg = client.expn("alice@example.com")
                self.assertEqual(502, code)
            code, mesg = client.expn("alice@example.com")
            self.assertEqual(421, code)
            self.assertEqual(b"4.7.0 EXPN sent too many times", mesg)
            with self.assertRaises(SMTPServerDisconnected):
                client.noop()
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.ehlo('example.com')
            self.assertEqual(250, code)
            for _ in range(0, default_max):
                code, mesg = client.vrfy("alice@example.com")
                self.assertEqual(252, code)
            code, mesg = client.vrfy("alice@example.com")
            self.assertEqual(421, code)
            self.assertEqual(b"4.7.0 VRFY sent too many times", mesg)
            with self.assertRaises(SMTPServerDisconnected):
                client.noop()

    def test_limit_wrong_type(self):
        kwargs = dict(
            command_call_limit="invalid",
        )
        controller = Controller(Sink(), server_kwargs=kwargs)
        self.addCleanup(controller.stop)
        with self.assertRaises(TypeError):
            controller.start()

    def test_limit_wrong_value_type(self):
        kwargs = dict(
            command_call_limit={"NOOP": "invalid"},
        )
        controller = Controller(Sink(), server_kwargs=kwargs)
        self.addCleanup(controller.stop)
        with self.assertRaises(TypeError):
            controller.start()

    @patch("logging.Logger.warning")
    def test_limit_bogus(self, mock_warning):
        # Extreme limit.
        kwargs = dict(
            command_call_limit=1,
        )
        controller = Controller(Sink(), server_kwargs=kwargs)
        self.addCleanup(controller.stop)
        controller.start()
        with SMTP(controller.hostname, controller.port) as client:
            code, mesg = client.ehlo('example.com')
            self.assertEqual(250, code)
            for i in range(0, 4):
                code, mesg = client.docmd(f"BOGUS{i}")
                self.assertEqual(500, code)
                expected = f"Error: command \"BOGUS{i}\" not recognized"
                self.assertEqual(expected, mesg.decode("ascii"))
            code, mesg = client.docmd("LASTBOGUS")
            self.assertEqual(502, code)
            self.assertEqual(
                b"5.5.1 Too many unrecognized commands, goodbye.", mesg
            )
            with self.assertRaises(SMTPServerDisconnected):
                client.noop()
