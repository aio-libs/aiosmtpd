import ssl
import unittest

from aiosmtpd.controller import Controller as BaseController
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import Session as Sess_, SMTP as SMTPProtocol
from aiosmtpd.testing.helpers import (
    ReceivingHandler,
    SUPPORTED_COMMANDS_TLS,
    assert_auth_invalid,
    get_server_context,
)
from email.mime.text import MIMEText
from smtplib import SMTP
from unittest.mock import Mock, patch


class Controller(BaseController):
    def factory(self):
        return SMTPProtocol(self.handler)


class TLSRequiredController(Controller):
    def factory(self):
        return SMTPProtocol(
            self.handler,
            decode_data=True,
            require_starttls=True,
            tls_context=get_server_context())


class TLSController(Controller):
    def factory(self):
        return SMTPProtocol(
            self.handler,
            decode_data=True,
            require_starttls=False,
            tls_context=get_server_context())


class RequireTLSAuthDecodingController(Controller):
    def factory(self):
        return SMTPProtocol(
            self.handler,
            decode_data=True,
            auth_require_tls=True,
            tls_context=get_server_context())


class HandshakeFailingHandler:
    def handle_STARTTLS(self, server, session, envelope):
        return False


class EOFingHandler:
    sess: Sess_ = None
    ssl_existed: bool = None
    result = None

    async def handle_NOOP(self, server: SMTPProtocol, session: Sess_,
                          envelope, arg):
        # First NOOP records the session, second NOOP triggers eof_received()
        if self.sess is None:
            self.sess = session
        else:
            self.ssl_existed = session.ssl is not None
            self.result = server.eof_received()
        return "250 OK"


class TestTLSEnding(unittest.TestCase):
    def test_eof_received(self):
        # Adapted from 54ff1fa9 + fc65a84e of PR #202
        #
        # I don't like this. It's too intimately involved with the innards of
        # the SMTP class. But for the life of me, I can't figure out why
        # coverage there fail intermittently.
        #
        # I suspect it's a race condition, but with what, and how to prevent
        # that from happening, that's ... a mystery.
        #
        handler = EOFingHandler()
        controller = TLSController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, response = client.ehlo("example.com")
            self.assertEqual(code, 250)
            self.assertIn("starttls", client.esmtp_features)
            code, response = client.starttls()
            self.assertEqual(code, 220)
            # Ensure that Server object 'realizes' it's in TLS mode
            code, response = client.ehlo("example.com")
            self.assertEqual(code, 250)
            client.noop()
            self.assertIsNotNone(handler.sess.ssl)
            client.noop()
            self.assertTrue(handler.ssl_existed)
            self.assertFalse(handler.result)


class TestStartTLS(unittest.TestCase):
    def test_starttls(self):
        handler = ReceivingHandler()
        controller = TLSController(handler)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            self.assertIn('starttls', client.esmtp_features)
            code, response = client.starttls()
            self.assertEqual(code, 220)
            client.send_message(
                MIMEText('hi'),
                'sender@example.com',
                'rcpt1@example.com')
        self.assertEqual(len(handler.box), 1)

    def test_failed_handshake(self):
        controller = TLSController(HandshakeFailingHandler())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            code, response = client.starttls()
            self.assertEqual(code, 220)
            code, response = client.mail('sender@example.com')
            self.assertEqual(code, 554)
            code, response = client.rcpt('rcpt@example.com')
            self.assertEqual(code, 554)

    def test_disabled_tls(self):
        controller = Controller(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            code, response = client.docmd('STARTTLS')
            self.assertEqual(code, 454)

    def test_tls_bad_syntax(self):
        controller = TLSController(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            client.ehlo('example.com')
            code, response = client.docmd('STARTTLS', 'TRUE')
            self.assertEqual(code, 501)

    def test_help_after_starttls(self):
        controller = TLSController(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('HELP')
            self.assertEqual(code, 250)
            self.assertEqual(response, SUPPORTED_COMMANDS_TLS)


class TestTLSForgetsSessionData(unittest.TestCase):
    def setUp(self):
        controller = TLSController(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_forget_ehlo(self):
        with SMTP(*self.address) as client:
            client.starttls()
            code, response = client.mail('sender@example.com')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: send HELO first')

    def test_forget_mail(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            client.mail('sender@example.com')
            client.starttls()
            client.ehlo('example.com')
            code, response = client.rcpt('rcpt@example.com')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: need MAIL command')

    def test_forget_rcpt(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            client.mail('sender@example.com')
            client.rcpt('rcpt@example.com')
            client.starttls()
            client.ehlo('example.com')
            client.mail('sender@example.com')
            code, response = client.docmd('DATA')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: need RCPT command')


class TestRequireTLS(unittest.TestCase):
    def setUp(self):
        controller = TLSRequiredController(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_hello_fails(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 530)

    def test_help_fails(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('HELP', 'HELO')
            self.assertEqual(code, 530)

    def test_ehlo(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            self.assertIn('starttls', client.esmtp_features)

    def test_mail_fails(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.mail('sender@exapmle.com')
            self.assertEqual(code, 530)

    def test_rcpt_fails(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.rcpt('sender@exapmle.com')
            self.assertEqual(code, 530)

    def test_vrfy_fails(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.vrfy('sender@exapmle.com')
            self.assertEqual(code, 530)

    def test_data_fails(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd('DATA')
            self.assertEqual(code, 530)


class TestRequireTLSAUTH(unittest.TestCase):
    def setUp(self):
        controller = RequireTLSAuthDecodingController(Sink)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_auth_notls(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd("AUTH ")
            self.assertEqual(code, 538)
            self.assertEqual(response,
                             b"5.7.11 Encryption required for requested "
                             b"authentication mechanism")

    def test_auth_tls(self):
        with SMTP(*self.address) as client:
            client.starttls()
            client.ehlo('example.com')
            code, response = client.docmd('AUTH PLAIN AHRlc3QAdGVzdA==')
            assert_auth_invalid(self, code, response)


class TestTLSContext(unittest.TestCase):
    def test_verify_mode_nochange(self):
        context = get_server_context()
        for mode in (ssl.CERT_NONE, ssl.CERT_OPTIONAL):
            context.verify_mode = mode
            server = SMTPProtocol(Sink(), tls_context=context)
            self.assertEqual(mode, context.verify_mode)

    @patch("logging.Logger.warning")
    def test_certreq_warn(self, mock_warn: Mock):
        context = get_server_context()
        context.verify_mode = ssl.CERT_REQUIRED
        server = SMTPProtocol(Sink(), tls_context=context)
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        mock_warn.assert_called_once()
        warn_msg = mock_warn.call_args[0][0]
        self.assertIn("tls_context.verify_mode", warn_msg)
        self.assertIn("might cause client connection problems", warn_msg)

    @patch("logging.Logger.warning")
    def test_nocertreq_chkhost_warn(self, mock_warn: Mock):
        context = get_server_context()
        context.verify_mode = ssl.CERT_OPTIONAL
        context.check_hostname = True
        server = SMTPProtocol(Sink(), tls_context=context)
        self.assertEqual(ssl.CERT_OPTIONAL, context.verify_mode)
        mock_warn.assert_called_once()
        warn_msg = mock_warn.call_args[0][0]
        self.assertIn("tls_context.check_hostname", warn_msg)
        self.assertIn("might cause client connection problems", warn_msg)
