__all__ = [
    'TestSMTP',
    ]


import socket
import unittest

from aiosmtpd.events import Sink
from aiosmtpd.testing.helpers import Controller
from smtplib import SMTP


class TestSMTP(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(Sink)
        self.controller.start()
        self.addCleanup(self.controller.stop)

    def test_helo(self):
        with SMTP(self.controller.hostname, self.controller.port) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            self.assertEqual(response, bytes(socket.getfqdn(), 'utf-8'))

    def test_helo_no_hostname(self):
        with SMTP(self.controller.hostname, self.controller.port) as client:
            # smtplib substitutes .local_hostname if the argument is falsey.
            client.local_hostname = ''
            code, response = client.helo('')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: HELO hostname')

    def test_helo_duplicate(self):
        with SMTP(self.controller.hostname, self.controller.port) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.helo('example.org')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Duplicate HELO/EHLO')
