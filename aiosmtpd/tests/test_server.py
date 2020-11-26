"""Test other aspects of the server implementation."""

import os
import socket
import unittest

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server
from contextlib import ExitStack
from smtplib import SMTP
from unittest.mock import patch


def in_wsl():
    # WSL 1.0 somehow allows more than one listener on one port.
    # So when testing on WSL, we must set PLATFORM=wsl and skip the
    # "test_socket_error" test.
    return os.environ.get("PLATFORM") == "wsl"


class TestServer(unittest.TestCase):
    def test_smtp_utf8(self):
        controller = Controller(Sink())
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

    @unittest.skipIf(in_wsl(), "WSL prevents socket collisions")
    # See explanation in the in_wsl() function
    def test_socket_error(self):
        # Testing starting a server with a port already in use
        s1 = Controller(Sink(), port=8025)
        s2 = Controller(Sink(), port=8025)
        self.addCleanup(s1.stop)
        self.addCleanup(s2.stop)
        s1.start()
        self.assertRaises(socket.error, s2.start)

    def test_server_attribute(self):
        controller = Controller(Sink())
        self.assertIsNone(controller.server)
        try:
            controller.start()
            self.assertIsNotNone(controller.server)
        finally:
            controller.stop()
            self.assertIsNone(controller.server)


class TestFactory(unittest.TestCase):
    def test_unknown_args(self):
        unknown = "this_is_an_unknown_kwarg"
        cont = Controller(Sink(), server_kwargs={unknown: True})
        try:
            with self.assertRaises(TypeError) as cm:
                cont.start()
            self.assertIsNone(cont.smtpd)
            excm = str(cm.exception)
            self.assertIn("unexpected keyword", excm)
            self.assertIn(unknown, excm)
        finally:
            cont.stop()

    def test_conflict_smtputf8(self):
        kwargs = dict(enable_SMTPUTF8=True)
        cont = Controller(Sink(), server_kwargs=kwargs)
        try:
            with self.assertRaises(TypeError) as cm:
                cont.start()
            self.assertIsNone(cont.smtpd)
            excm = str(cm.exception)
            self.assertIn("multiple values", excm)
            self.assertIn("enable_SMTPUTF8", excm)
        finally:
            cont.stop()

    def test_factory_none(self):
        # Hypothetical situation where factory() did not raise an Exception
        # but returned None instead
        with ExitStack() as stk:
            cont = Controller(Sink())
            stk.callback(cont.stop)

            stk.enter_context(
                patch("aiosmtpd.controller.SMTP",
                      return_value=None)
            )

            with self.assertRaises(RuntimeError) as cm:
                cont.start()
            self.assertIsNone(cont.smtpd)
            excm = str(cm.exception)
            self.assertEqual("factory() returned None", excm)
