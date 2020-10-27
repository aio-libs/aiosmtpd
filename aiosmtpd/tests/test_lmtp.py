"""Test the LMTP protocol."""

import socket
import unittest

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.lmtp import LMTP
from aiosmtpd.testing.helpers import SMTP_with_asserts


class LMTPController(Controller):
    def factory(self):
        return LMTP(self.handler)


class TestLMTP(unittest.TestCase):
    def setUp(self):
        controller = LMTPController(Sink)
        controller.start()
        self.addCleanup(controller.stop)

        self.client = SMTP_with_asserts(self, from_=controller)
        self.addCleanup(self.client.quit)

    def test_lhlo(self):
        self.client.assert_cmd_resp(
            "LHLO example.com",
            (250, bytes(socket.getfqdn(), 'utf-8'))
        )

    def test_helo(self):
        # HELO and EHLO are not valid LMTP commands.
        resp = self.client.helo('example.com')
        self.assertEqual(
            (500, b'Error: command "HELO" not recognized'),
            resp,
        )

    def test_ehlo(self):
        # HELO and EHLO are not valid LMTP commands.
        resp = self.client.ehlo('example.com')
        self.assertEqual(
            (500, b'Error: command "EHLO" not recognized'),
            resp,
        )

    def test_help(self):
        # https://github.com/aio-libs/aiosmtpd/issues/113
        self.client.assert_cmd_resp(
            "HELP",
            (250,
             b'Supported commands: AUTH DATA HELP LHLO MAIL '
             b'NOOP QUIT RCPT RSET VRFY'
             )
        )
