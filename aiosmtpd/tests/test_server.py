"""Test other aspects of the server implementation."""


import unittest

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server
from smtplib import SMTP


class UTF8Controller(Controller):
    def factory(self):
        return Server(self.handler, enable_SMTPUTF8=True)


class TestServer(unittest.TestCase):
    def test_constructor_contraints(self):
        # These two arguments cannot both be set.
        self.assertRaises(ValueError, Server, Sink(),
                          enable_SMTPUTF8=True,
                          decode_data=True)

    def test_smtp_utf8(self):
        controller = UTF8Controller(Sink())
        controller.start()
        self.addCleanup(controller.stop)
        with SMTP(controller.hostname, controller.port) as client:
            code, response = client.ehlo('example.com')
        self.assertEqual(code, 250)
        self.assertIn(b'SMTPUTF8', response.splitlines())

    def test_default_max_command_size_limit(self):
        server = Server(Sink())
        self.assertEqual(server.max_command_size_limit, 512)

    def test_special_max_command_size_limit(self):
        server = Server(Sink())
        server.command_size_limits['DATA'] = 1024
        self.assertEqual(server.max_command_size_limit, 1024)
