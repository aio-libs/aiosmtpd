"""Test the SMTP protocol."""

import time
import socket
import asyncio
import unittest
import warnings

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import MISSING, SMTP as Server, __ident__ as GREETING
from aiosmtpd.testing.helpers import (
    SMTP_with_asserts,
    ReceivingHandler,
    SUPPORTED_COMMANDS_NOTLS,
    reset_connection,
)
from base64 import b64encode
from contextlib import ExitStack
from smtplib import (
    SMTP, SMTPDataError, SMTPResponseException, SMTPServerDisconnected)
from typing import List
from unittest.mock import Mock, PropertyMock, patch

CRLF = '\r\n'
BCRLF = b'\r\n'


def authenticator(mechanism, login, password):
    if login and login.decode() == 'goodlogin':
        return True
    else:
        return False


class DecodingController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True, enable_SMTPUTF8=True,
                      auth_require_tls=False, auth_callback=authenticator)


class PeekerHandler:
    def __init__(self):
        self.session = None

    async def handle_MAIL(
            self, server, session, envelope, address, mail_options
    ):
        self.session = session
        return "250 OK"

    async def auth_NULL(
            self, server, args
    ):
        return "NULL_login"

    async def auth_DONT(
            self, server, args
    ):
        return MISSING


class PeekerAuth:
    def __init__(self):
        self.login = None
        self.password = None

    def authenticate(
            self, mechanism: str, login: bytes, password: bytes
    ) -> bool:
        self.login = login
        self.password = password
        return True


auth_peeker = PeekerAuth()


class DecodingControllerPeekAuth(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True, enable_SMTPUTF8=True,
                      auth_require_tls=False,
                      auth_callback=auth_peeker.authenticate,
                      **self.server_kwargs)


class NoDecodeController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=False)


class TimeoutController(Controller):
    Delay: float = 1.0

    def factory(self):
        return Server(self.handler, timeout=self.Delay)


class RequiredAuthDecodingController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True, enable_SMTPUTF8=True,
                      auth_require_tls=False, auth_callback=authenticator,
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
        return '451 Temporary error: ({}) {}'.format(
            error.__class__.__name__, str(error))


class ErroringErrorHandler:
    error = None

    async def handle_exception(self, error):
        self.error = error
        raise ValueError('ErroringErrorHandler test')


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

        resources = ExitStack()
        self.client: SMTP_with_asserts = resources.enter_context(
            SMTP_with_asserts(self, from_=controller)
        )
        self.addCleanup(resources.close)

    def test_binary(self):
        self.client.sock.send(b"\x80FAIL\r\n")
        self.assertEqual(
            (500, b'Error: bad syntax'),
            self.client.getreply()
        )

    def test_binary_space(self):
        self.client.sock.send(b"\x80 FAIL\r\n")
        self.assertEqual(
            (500, b'Error: bad syntax'),
            self.client.getreply()
        )

    def test_helo(self):
        self.assertEqual(
            (250, bytes(socket.getfqdn(), 'utf-8')),
            self.client.helo('example.com')
        )

    def test_close_then_continue(self):
        """if client voluntarily breaks connection, SMTP state must reset"""
        self.client.assert_helo_ok('example.com')
        self.client.close()
        self.client.connect(*self.client._addr)
        self.client.assert_cmd_resp(
            'MAIL FROM: <anne@example.com>',
            (503, b'Error: send HELO first')
        )

    def test_helo_no_hostname(self):
        # smtplib substitutes .local_hostname if the argument is falsey.
        self.client.local_hostname = ''
        self.assertEqual(
            (501, b'Syntax: HELO hostname'),
            self.client.helo('')
        )

    def test_helo_duplicate(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_helo_ok('example.org')

    def test_ehlo(self):
        code, response = self.client.ehlo('example.com')
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
            self.assertEqual(expected, actual)

    def test_ehlo_duplicate(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_ehlo_ok('example.org')

    def test_ehlo_no_hostname(self):
        # smtplib substitutes .local_hostname if the argument is falsey.
        self.client.local_hostname = ''
        self.assertEqual(
            (501, b'Syntax: EHLO hostname'),
            self.client.ehlo('')
        )

    def test_helo_then_ehlo(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_ehlo_ok('example.org')

    def test_ehlo_then_helo(self):
        self.client.assert_ehlo_ok('example.org')
        self.client.assert_helo_ok('example.com')

    def test_noop(self):
        code, _ = self.client.noop()
        self.assertEqual(250, code)

    def test_noop_with_arg(self):
        # .noop() doesn't accept arguments.
        self.client.assert_cmd_ok('NOOP ok')

    def test_quit(self):
        resp = self.client.quit()
        self.assertEqual((221, b'Bye'), resp)

    def test_quit_with_arg(self):
        self.client.assert_cmd_resp(
            "QUIT oops",
            (501, b'Syntax: QUIT')
        )

    def test_help(self):
        # Don't get tricked by smtplib processing of the response.
        self.client.assert_cmd_resp(
            'HELP',
            (250, SUPPORTED_COMMANDS_NOTLS)
        )

    def test_help_helo(self):
        # Don't get tricked by smtplib processing of the response.
        self.client.assert_cmd_resp(
            'HELP HELO',
            (250, b'Syntax: HELO hostname')
        )

    def test_help_ehlo(self):
        # Don't get tricked by smtplib processing of the response.
        self.client.assert_cmd_resp(
            'HELP EHLO',
            (250, b'Syntax: EHLO hostname')
        )

    def test_help_mail(self):
        # Don't get tricked by smtplib processing of the response.
        self.client.assert_cmd_resp(
            'HELP MAIL',
            (250, b'Syntax: MAIL FROM: <address>')
        )

    def test_help_mail_esmtp(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'HELP MAIL',
            (250, b'Syntax: MAIL FROM: <address> [SP <mail-parameters>]')
        )

    def test_help_rcpt(self):
        # Don't get tricked by smtplib processing of the response.
        self.client.assert_cmd_resp(
            'HELP RCPT',
            (250, b'Syntax: RCPT TO: <address>')
        )

    def test_help_rcpt_esmtp(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'HELP RCPT',
            (250, b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')
        )

    def test_help_data(self):
        self.client.assert_cmd_resp(
            'HELP DATA',
            (250, b'Syntax: DATA')
        )

    def test_help_rset(self):
        self.client.assert_cmd_resp(
            'HELP RSET',
            (250, b'Syntax: RSET')
        )

    def test_help_noop(self):
        self.client.assert_cmd_resp(
            'HELP NOOP',
            (250, b'Syntax: NOOP [ignored]')
        )

    def test_help_quit(self):
        self.client.assert_cmd_resp(
            'HELP QUIT',
            (250, b'Syntax: QUIT')
        )

    def test_help_vrfy(self):
        self.client.assert_cmd_resp(
            'HELP VRFY',
            (250, b'Syntax: VRFY <address>')
        )

    def test_help_auth(self):
        self.client.assert_cmd_resp(
            'HELP AUTH',
            (250, b'Syntax: AUTH <mechanism>')
        )

    def test_help_bad_arg(self):
        # Don't get tricked by smtplib processing of the response.
        self.client.assert_cmd_resp(
            'HELP me!',
            (501, SUPPORTED_COMMANDS_NOTLS)
        )

    def test_expn(self):
        self.assertEqual(
            (502, b'EXPN not implemented'),
            self.client.expn('anne@example.com')
        )

    def test_mail_no_helo(self):
        self.client.assert_cmd_resp(
            'MAIL FROM: <anne@example.com>',
            (503, b'Error: send HELO first')
        )

    def test_mail_no_arg(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_resp(
            'MAIL',
            (501, b'Syntax: MAIL FROM: <address>')
        )

    def test_mail_no_from(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_resp(
            'MAIL <anne@example.com>',
            (501, b'Syntax: MAIL FROM: <address>')
        )

    def test_mail_params_no_esmtp(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_resp(
            'MAIL FROM: <anne@example.com> SIZE=10000',
            (501, b'Syntax: MAIL FROM: <address>')
        )

    def test_mail_params_esmtp(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_ok(
            'MAIL FROM: <anne@example.com> SIZE=10000')

    def test_mail_from_twice(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        self.client.assert_cmd_resp(
            'MAIL FROM: <anne@example.com>',
            (503, b'Error: nested MAIL command')
        )

    def test_mail_from_malformed(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_resp(
            'MAIL FROM: Anne <anne@example.com>',
            (501, b'Syntax: MAIL FROM: <address>')
        )

    def test_mail_malformed_params_esmtp(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'MAIL FROM: <anne@example.com> SIZE 10000',
            (501, b'Syntax: MAIL FROM: <address> [SP <mail-parameters>]')
        )

    def test_mail_missing_params_esmtp(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'MAIL FROM: <anne@example.com> SIZE',
            (501, b'Syntax: MAIL FROM: <address> [SP <mail-parameters>]')
        )

    def test_mail_unrecognized_params_esmtp(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'MAIL FROM: <anne@example.com> FOO=BAR',
            (555, b'MAIL FROM parameters not recognized or '
                  b'not implemented')
        )

    def test_mail_params_bad_syntax_esmtp(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'MAIL FROM: <anne@example.com> #$%=!@#',
            (501, b'Syntax: MAIL FROM: <address> [SP <mail-parameters>]')
        )

    # Test the workaround http://bugs.python.org/issue27931
    @patch('email._header_value_parser.AngleAddr.addr_spec',
           side_effect=IndexError, new_callable=PropertyMock)
    def test_mail_fail_parse_email(self, addr_spec):
        self.client.helo('example.com')
        self.client.assert_cmd_resp(
            'MAIL FROM: <""@example.com>',
            (501, b'Syntax: MAIL FROM: <address>')
        )

    def test_rcpt_no_helo(self):
        self.client.assert_cmd_resp(
            'RCPT TO: <anne@example.com>',
            (503, b'Error: send HELO first')
        )

    def test_rcpt_no_mail(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_resp(
            'RCPT TO: <anne@example.com>',
            (503, b'Error: need MAIL command')
        )

    def test_rcpt_no_arg(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        self.client.assert_cmd_resp(
            'RCPT',
            (501, b'Syntax: RCPT TO: <address>')
        )

    def test_rcpt_no_to(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        self.client.assert_cmd_resp(
            'RCPT <anne@example.com>',
            (501, b'Syntax: RCPT TO: <address>')
        )

    def test_rcpt_no_arg_esmtp(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        self.client.assert_cmd_resp(
            'RCPT',
            (501, b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')
        )

    def test_rcpt_no_address(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        self.client.assert_cmd_resp(
            'RCPT TO:',
            (501, b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')
        )

    def test_rcpt_with_params_no_esmtp(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        self.client.assert_cmd_resp(
            'RCPT TO: <bart@example.com> SIZE=1000',
            (501, b'Syntax: RCPT TO: <address>')
        )

    def test_rcpt_with_bad_params(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        self.client.assert_cmd_resp(
            'RCPT TO: <bart@example.com> #$%=!@#',
            (501, b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')
        )

    def test_rcpt_with_unknown_params(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        self.client.assert_cmd_resp(
            'RCPT TO: <bart@example.com> FOOBAR',
            (555, b'RCPT TO parameters not recognized or not implemented')
        )

    # Test the workaround http://bugs.python.org/issue27931
    @patch('email._header_value_parser.AngleAddr.addr_spec',
           new_callable=PropertyMock)
    def test_rcpt_fail_parse_email(self, addr_spec):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        addr_spec.side_effect = IndexError
        self.client.assert_cmd_resp(
            'RCPT TO: <""@example.com>',
            (501, b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')
        )

    def test_rset(self):
        self.assertEqual((250, b'OK'), self.client.rset())

    def test_rset_with_arg(self):
        self.client.assert_cmd_resp(
            'RSET FOO',
            (501, b'Syntax: RSET')
        )

    def test_vrfy(self):
        self.client.assert_cmd_resp(
            'VRFY <anne@example.com>',
            (252, b'Cannot VRFY user, but will accept message and '
                  b'attempt delivery')
        )

    def test_vrfy_no_arg(self):
        self.client.assert_cmd_resp(
            'VRFY',
            (501, b'Syntax: VRFY <address>')
        )

    def test_vrfy_not_an_address(self):
        self.client.assert_cmd_resp(
            'VRFY @@',
            (502, b'Could not VRFY @@')
        )

    def test_data_no_helo(self):
        self.client.assert_cmd_resp(
            'DATA',
            (503, b'Error: send HELO first')
        )

    def test_data_no_rcpt(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_resp(
            'DATA',
            (503, b'Error: need RCPT command')
        )

    def test_data_invalid_params(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
        self.client.assert_cmd_ok('RCPT TO: <anne@example.com>')
        self.client.assert_cmd_resp(
            'DATA FOOBAR',
            (501, b'Syntax: DATA')
        )

    def test_empty_command(self):
        self.client.assert_cmd_resp(
            '',
            (500, b'Error: bad syntax')
        )

    def test_too_long_command(self):
        self.client.assert_cmd_resp(
            'a' * 513,
            (500, b'Error: line too long')
        )

    def test_unknown_command(self):
        self.client.assert_cmd_resp(
            'FOOBAR',
            (500, b'Error: command "FOOBAR" not recognized')
        )

    def test_auth_no_ehlo(self):
        self.client.assert_cmd_resp(
            'AUTH',
            (503, b'Error: send EHLO first')
        )

    def test_auth_helo(self):
        self.client.assert_helo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH',
            (500, b"Error: command 'AUTH' not recognized")
        )

    def test_auth_too_many_values(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH PLAIN NONE NONE',
            (501, b'Too many values')
        )

    def test_auth_not_enough_values(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH',
            (501, b'Not enough value')
        )

    def test_auth_not_supported_methods(self):
        for method in ('GSSAPI', 'DIGEST-MD5', 'MD5', 'CRAM-MD5'):
            self.client.assert_ehlo_ok('example.com')
            self.client.assert_cmd_resp(
                'AUTH ' + method,
                (504, b'5.5.4 Unrecognized authentication type')
            )

    def test_auth_already_authenticated(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_auth_success(
            'AUTH PLAIN ' +
            b64encode(b'\0goodlogin\0goodpasswd').decode()
        )
        self.client.assert_cmd_resp(
            'AUTH',
            (503, b'Already authenticated')
        )
        self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')

    def test_auth_bad_base64_encoding(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH PLAIN not-b64',
            (501, b"5.5.2 Can't decode base64")
        )

    def test_auth_bad_base64_length(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH PLAIN ' + b64encode(b'\0onlylogin').decode(),
            (501, b"5.5.2 Can't split auth value")
        )

    def test_auth_bad_credentials(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_auth_invalid(
            'AUTH PLAIN ' + b64encode(b'\0badlogin\0badpasswd').decode()
        )

    def test_auth_two_steps_good_credentials(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH PLAIN',
            (334, b'')
        )
        self.client.assert_auth_success(
            b64encode(b'\0goodlogin\0goodpasswd').decode()
        )

    def test_auth_two_steps_bad_credentials(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH PLAIN',
            (334, b'')
        )
        self.client.assert_auth_invalid(
            b64encode(b'\0badlogin\0badpasswd').decode()
        )

    def test_auth_two_steps_abort(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH PLAIN',
            (334, b'')
        )
        self.client.assert_cmd_resp(
            '*',
            (501, b'Auth aborted')
        )

    def test_auth_two_steps_bad_base64_encoding(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH PLAIN',
            (334, b"")
        )
        self.client.assert_cmd_resp(
            "ab@%",
            (501, b"5.5.2 Can't decode base64")
        )

    def test_auth_good_credentials(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_auth_success(
            'AUTH PLAIN ' +
            b64encode(b'\0goodlogin\0goodpasswd').decode()
        )

    def test_auth_no_credentials(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_auth_invalid('AUTH PLAIN =')

    def test_auth_two_steps_no_credentials(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            'AUTH PLAIN',
            (334, b'')
        )
        self.client.assert_auth_invalid('=')

    def test_auth_login_multisteps_no_credentials(self):
        self.client.assert_ehlo_ok('example.com')
        self.client.assert_cmd_resp(
            "AUTH LOGIN",
            (334, b"VXNlciBOYW1lAA==")
        )
        self.client.assert_cmd_resp(
            '=',
            (334, b"UGFzc3dvcmQA")
        )
        self.client.assert_auth_invalid('=')


class TestSMTPAuth(unittest.TestCase):
    def setUp(self):
        self.handler = PeekerHandler()
        controller = DecodingControllerPeekAuth(
            self.handler, server_kwargs={"auth_exclude_mechanism": ["DONT"]}
        )
        controller.start()
        self.addCleanup(controller.stop)

        resource = ExitStack()

        self.client: SMTP_with_asserts = resource.enter_context(
            SMTP_with_asserts(self, from_=controller)
        )

        self.addCleanup(resource.close)

    def test_ehlo(self):
        code, response = self.client.ehlo('example.com')
        self.assertEqual(code, 250)
        lines = response.splitlines()
        expecteds = (
            bytes(socket.getfqdn(), 'utf-8'),
            b'SIZE 33554432',
            b'SMTPUTF8',
            b'AUTH LOGIN NULL PLAIN',
            b'HELP',
        )
        for actual, expected in zip(lines, expecteds):
            self.assertEqual(expected, actual)

    def test_auth_plain_null_credential(self):
        self.client.assert_ehlo_ok("example.com")
        self.client.assert_cmd_resp(
            "AUTH PLAIN",
            (334, b"")
        )
        self.client.assert_auth_success('=')
        self.assertEqual(auth_peeker.login, None)
        self.assertEqual(auth_peeker.password, None)
        resp = self.client.mail("alice@example.com")
        self.assertEqual((250, b"OK"), resp)
        self.assertEqual(self.handler.session.login_data, b"")

    def test_auth_login_null_credential(self):
        self.client.assert_ehlo_ok("example.com")
        self.client.assert_cmd_resp(
            "AUTH LOGIN",
            (334, b"VXNlciBOYW1lAA==")
        )
        self.client.assert_cmd_resp(
            '=',
            (334, b"UGFzc3dvcmQA")
        )
        self.client.assert_auth_success('=')
        self.assertEqual(auth_peeker.login, None)
        self.assertEqual(auth_peeker.password, None)
        resp = self.client.mail("alice@example.com")
        self.assertEqual((250, b"OK"), resp)
        self.assertEqual(self.handler.session.login_data, b"")

    def test_auth_login_abort_login(self):
        self.client.assert_ehlo_ok("example.com")
        self.client.assert_cmd_resp(
            "AUTH LOGIN",
            (334, b"VXNlciBOYW1lAA==")
        )
        self.client.assert_cmd_resp(
            '*',
            (501, b"Auth aborted")
        )

    def test_auth_login_abort_password(self):
        auth_peeker.return_val = False
        self.client.assert_ehlo_ok("example.com")
        self.client.assert_cmd_resp(
            "AUTH LOGIN",
            (334, b"VXNlciBOYW1lAA==")
        )
        self.client.assert_cmd_resp(
            '=',
            (334, b"UGFzc3dvcmQA")
        )
        self.client.assert_cmd_resp(
            '*',
            (501, b"Auth aborted")
        )

    def test_auth_custom_mechanism(self):
        auth_peeker.return_val = False
        self.client.assert_ehlo_ok("example.com")
        self.client.assert_auth_success("AUTH NULL")

    def test_auth_disabled_mechanism(self):
        self.client.assert_ehlo_ok("example.com")
        self.client.assert_cmd_resp(
            "AUTH DONT",
            (504, b"5.5.4 Unrecognized authentication type")
        )


class TestSMTPLowLevel(unittest.TestCase):
    def setUp(self):
        self.controller = RequiredAuthDecodingController(Sink)
        self.addCleanup(self.controller.stop)
        self.address = (self.controller.hostname, self.controller.port)

    def test_warn_auth(self):
        self.controller.start()
        with ExitStack() as stack:
            w: List[warnings.WarningMessage]
            w = stack.enter_context(warnings.catch_warnings(record=True))
            stack.enter_context(SMTP(*self.address))
            warnings.simplefilter("always")
            self.assertEqual(
                "Requiring AUTH while not requiring TLS "
                "can lead to security vulnerabilities!",
                str(w[0].message)
            )


class TestSMTPRequiredAuthentication(unittest.TestCase):
    def setUp(self):
        controller = RequiredAuthDecodingController(Sink)
        controller.start()
        self.addCleanup(controller.stop)

        resources = ExitStack()
        self.w = resources.enter_context(
            warnings.catch_warnings(record=True))
        warnings.filterwarnings("ignore", category=UserWarning)
        self.client: SMTP_with_asserts = resources.enter_context(
            SMTP_with_asserts(self, from_=controller)
        )
        self.addCleanup(resources.close)

    def test_help_unauthenticated(self):
        self.client.assert_auth_required("HELP")

    def test_vrfy_unauthenticated(self):
        self.client.assert_auth_required('VRFY <anne@example.com>')

    def test_mail_unauthenticated(self):
        self.client.ehlo('example.com')
        self.client.assert_auth_required('MAIL FROM: <anne@example.com>')

    def test_rcpt_unauthenticated(self):
        self.client.ehlo('example.com')
        self.client.assert_auth_required('RCPT TO: <anne@example.com>')

    def test_data_unauthenticated(self):
        self.client.ehlo('example.com')
        self.client.assert_auth_required('DATA')

    def test_help_authenticated(self):
        self.client.ehlo('example.com')
        self.client.assert_auth_success(
            'AUTH PLAIN ' +
            b64encode(b'\0goodlogin\0goodpasswd').decode()
        )
        code, response = self.client.docmd('HELP')
        self.assertEqual(code, 250)
        self.assertEqual(response, SUPPORTED_COMMANDS_NOTLS)

    def test_vrfy_authenticated(self):
        self.client.ehlo('example.com')
        self.client.assert_auth_success(
            'AUTH PLAIN ' +
            b64encode(b'\0goodlogin\0goodpasswd').decode()
        )
        code, response = self.client.docmd('VRFY <anne@example.com>')
        self.assertEqual(code, 252)
        self.assertEqual(
            response,
            b'Cannot VRFY user, but will accept message and '
            b'attempt delivery'
        )

    def test_mail_authenticated(self):
        self.client.ehlo('example.com')
        self.client.assert_auth_success(
            'AUTH PLAIN ' +
            b64encode(b'\0goodlogin\0goodpasswd').decode()
        )
        code, response = self.client.docmd('MAIL FROM: <anne@example.com>')
        self.assertEqual(code, 250)
        self.assertEqual(response, b'OK')

    def test_rcpt_authenticated(self):
        self.client.ehlo('example.com')
        self.client.assert_auth_success(
            'AUTH PLAIN ' +
            b64encode(b'\0goodlogin\0goodpasswd').decode()
        )
        code, response = self.client.docmd('RCPT TO: <anne@example.com>')
        self.assertEqual(code, 503)
        self.assertEqual(response, b'Error: need MAIL command')

    def test_data_authenticated(self):
        self.client.ehlo('example.com')
        self.client.assert_auth_required('DATA')


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
        with SMTP_with_asserts(self, from_=controller) as client:
            client.assert_ehlo_ok('example.com')
            client.assert_cmd_resp(
                'MAIL FROM: <anne@example.com> SIZE=10000',
                (552, b'Error: message size exceeds fixed maximum '
                      b'message size')
            )

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
            self.assertEqual((250, b'OK'), client.getreply())
            client.send(bytes(
                'RCPT TO: <' + recipient + '>\r\n',
                encoding='utf-8'))
            self.assertEqual((250, b'OK'), client.getreply())
            self.assertEqual((250, b'OK'), client.data(''))
        self.assertEqual(handler.box[0].rcpt_tos[0], recipient)
        self.assertEqual(handler.box[0].mail_from, sender)

    def test_mail_with_unrequited_smtputf8(self):
        controller = Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP_with_asserts(self, from_=controller) as client:
            client.assert_ehlo_ok('example.com')
            client.assert_cmd_resp(
                'MAIL FROM: <anne@example.com>',
                (250, b'OK')
            )

    def test_mail_with_incompatible_smtputf8(self):
        controller = Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP_with_asserts(self, from_=controller) as client:
            client.assert_ehlo_ok('example.com')
            client.assert_cmd_resp(
                'MAIL FROM: <anne@example.com> SMTPUTF8=YES',
                (501, b'Error: SMTPUTF8 takes no arguments')
            )

    def test_mail_invalid_body(self):
        controller = Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP_with_asserts(self, from_=controller) as client:
            client.assert_ehlo_ok('example.com')
            client.assert_cmd_resp(
                'MAIL FROM: <anne@example.com> BODY 9BIT',
                (501, b'Error: BODY can only be one of 7BIT, 8BITMIME')
            )

    def test_esmtp_no_size_limit(self):
        controller = SizedController(Sink(), size=None)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP_with_asserts(self, from_=controller) as client:
            resp_text = client.assert_ehlo_ok('example.com')
            for line in resp_text.splitlines():
                self.assertNotEqual(line[:4], b'SIZE')

    def test_process_message_error(self):
        controller = Controller(ErroringHandler())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP_with_asserts(self, from_=controller) as client:
            client.assert_ehlo_ok('example.com')
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
        with SMTP_with_asserts(self, from_=controller) as client:
            client.assert_helo_ok('example.com')
            mail = '\r\n'.join(['z' * 20] * 10)
            with self.assertRaises(SMTPResponseException) as cm:
                client.sendmail('anne@example.com', ['bart@example.com'], mail)
            self.assertEqual(cm.exception.smtp_code, 552)
            self.assertEqual(cm.exception.smtp_error,
                             b'Error: Too much mail data')

    def test_dots_escaped(self):
        handler = ReceivingHandler()
        controller = DecodingController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP_with_asserts(self, from_=controller) as client:
            client.assert_helo_ok('example.com')
            mail = CRLF.join(['Test', '.', 'mail'])
            client.sendmail('anne@example.com', ['bart@example.com'], mail)
            self.assertEqual(len(handler.box), 1)
            self.assertEqual(handler.box[0].content, 'Test\r\n.\r\nmail\r\n')

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
            self.assertEqual(
                (500, b'ErroringHandler handling error'),
                client.helo('example.com')
            )
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
            self.assertEqual(
                (500, b'Error: (ValueError) test'),
                client.helo('example.com')
            )
        # handler.error did not change because the handler does not have a
        # handle_exception() method.
        self.assertIsNone(handler.error)

    def test_unexpected_errors_custom_response(self):
        handler = ErroringHandlerCustomResponse()
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with ExitStack() as resources:
            # Suppress logging to the console during the tests.  Depending on
            # timing, the exception may or may not be logged.
            resources.enter_context(patch('aiosmtpd.smtp.log.exception'))
            client = resources.enter_context(
                SMTP(controller.hostname, controller.port))
            self.assertEqual(
                (451, b'Temporary error: (ValueError) test'),
                client.helo('example.com')
            )
        self.assertIsInstance(handler.error, ValueError)

    def test_exception_handler_exception(self):
        handler = ErroringErrorHandler()
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with ExitStack() as resources:
            # Suppress logging to the console during the tests.  Depending on
            # timing, the exception may or may not be logged.
            resources.enter_context(patch('aiosmtpd.smtp.log.exception'))
            client = resources.enter_context(
                SMTP(controller.hostname, controller.port))
            self.assertEqual(
                (500, b'Error: (ValueError) ErroringErrorHandler test'),
                client.helo('example.com')
            )
        self.assertIsInstance(handler.error, ValueError)

    def test_exception_handler_undescribable(self):
        handler = UndescribableErrorHandler()
        controller = ErrorController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with ExitStack() as resources:
            # Suppress logging to the console during the tests.  Depending on
            # timing, the exception may or may not be logged.
            resources.enter_context(patch('aiosmtpd.smtp.log.exception'))
            client = resources.enter_context(
                SMTP(controller.hostname, controller.port))
            self.assertEqual(
                (500, b'Error: Cannot describe error'),
                client.helo('example.com')
            )
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
            self.assertEqual(
                (250, bytes('custom.localhost', 'utf-8')),
                client.helo('example.com')
            )

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
        with SMTP_with_asserts(self) as client:
            client.connect(controller.hostname, controller.port)
            client.assert_ehlo_ok('example.com')
            client.assert_cmd_resp(
                'MAIL FROM: <anne@example.com> BODY=FOOBAR',
                (501, b'Error: BODY can only be one of 7BIT, 8BITMIME')
            )


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
            client.helo('example.com')
            client.docmd('MAIL FROM: <anne@example.com>')
            client.docmd('RCPT TO: <bart@example.com>')
            client.docmd('DATA')
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

    def test_close_in_command(self):
        with SMTP(*self.address) as client:
            # Don't include the CRLF.
            client.send('FOO')
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
        with SMTP_with_asserts(self, *self.address) as client:
            client.assert_ehlo_ok('example.com')
            client.send(b'MAIL FROM: <anne\xFF@example.com>\r\n')
            self.assertEqual(
                (500, b'Error: strict ASCII mode'),
                client.getreply()
            )

    def test_mail_param(self):
        with SMTP_with_asserts(self, *self.address) as client:
            client.assert_ehlo_ok('example.com')
            client.assert_cmd_resp(
                'MAIL FROM: <anne@example.com> SMTPUTF8',
                (501, b'Error: SMTPUTF8 disabled'),
            )

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
