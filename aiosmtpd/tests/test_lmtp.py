"""Test the LMTP protocol."""

__all__ = [
    'TestLMTP',
    ]


import socket
import asyncio
import unittest

from aiosmtpd.handlers import Sink
from aiosmtpd.lmtp import LMTP
from aiosmtpd.testing.helpers import Controller
from smtplib import SMTP


class ExitableLMTP(LMTP):
    @asyncio.coroutine
    def smtp_EXIT(self, arg):
        if arg:
            yield from self.push('501 Syntax: NOOP')
        else:
            yield from self.push('250 OK')
            self.loop.stop()
            self._connection_closed = True
            self._handler_coroutine.cancel()


class LMTPController(Controller):
    def factory(self):
        return ExitableLMTP(self.handler)


class TestLMTP(unittest.TestCase):
    def setUp(self):
        self.controller = LMTPController(Sink)
        self.controller.start()
        self.addCleanup(self.controller.stop)

    def test_lhlo(self):
        with SMTP(self.controller.hostname, self.controller.port) as client:
            code, response = client.docmd('LHLO', 'example.com')
            self.assertEqual(code, 250)
            self.assertEqual(response, bytes(socket.getfqdn(), 'utf-8'))

    def test_helo(self):
        # HELO and EHLO are not valid LMTP commands.
        with SMTP(self.controller.hostname, self.controller.port) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 500)
            self.assertEqual(response, b'Error: command "HELO" not recognized')

    def test_ehlo(self):
        # HELO and EHLO are not valid LMTP commands.
        with SMTP(self.controller.hostname, self.controller.port) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 500)
            self.assertEqual(response, b'Error: command "EHLO" not recognized')
