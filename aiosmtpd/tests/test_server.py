"""Test other aspects of the server implementation."""

import gc
import os
import socket
import unittest

from aiosmtpd.controller import asyncio, Controller, _FakeServer
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server
from contextlib import ExitStack
from functools import wraps
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


# Silence the "Exception ignored ... RuntimeError: Event loop is closed" message.
# This goes hand-in-hand with TestFactory.setUpClass() below.
# Source: https://github.com/aio-libs/aiohttp/issues/4324#issuecomment-733884349
def silence_event_loop_closed(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except RuntimeError as e:
            if str(e) != "Event loop is closed":
                raise
    return wrapper


class TestFactory(unittest.TestCase):
    Proactor = None
    olddel = None

    @classmethod
    def setUpClass(cls) -> None:
        # See silence_event_loop_closed() above
        # noinspection PyUnresolvedReferences
        cls.Proactor = asyncio.proactor_events._ProactorBasePipeTransport
        cls.olddel = cls.Proactor.__del__
        cls.Proactor.__del__ = silence_event_loop_closed(
            silence_event_loop_closed(cls.olddel)
            )

    @classmethod
    def tearDownClass(cls) -> None:
        # gc.collect() hinted in https://stackoverflow.com/a/25067818/149900
        # Probably to remove leftover "Exception ignored"?
        gc.collect()
        if cls.olddel is not None:
            cls.Proactor.__del__ = cls.olddel

    def test_normal_situation(self):
        cont = Controller(Sink())
        try:
            cont.start()
            self.assertIsNotNone(cont.smtpd)
            self.assertIsNone(cont._thread_exception)
        finally:
            cont.stop()

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

    def test_noexc_smtpd_missing(self):
        # Hypothetical situation where factory() failed but no
        # Exception was generated.
        with ExitStack() as stk:
            cont = Controller(Sink())
            stk.callback(cont.stop)

            def hijacker(*args, **kwargs):
                cont._thread_exception = None
                # Must still return an (unmocked) _FakeServer to prevent a whole bunch
                # of messy exceptions, although they doesn't affect the test at all.
                return _FakeServer(cont.loop)

            stk.enter_context(
                patch("aiosmtpd.controller._FakeServer",
                      side_effect=hijacker)
            )

            stk.enter_context(
                patch("aiosmtpd.controller.SMTP",
                      side_effect=RuntimeError("Simulated Failure"))
            )

            with self.assertRaises(RuntimeError) as cm:
                cont.start()
            self.assertIsNone(cont.smtpd)
            self.assertIsNone(cont._thread_exception)
            excm = str(cm.exception)
            self.assertEqual("Unknown Error, failed to init SMTP server",
                             excm)
