__all__ = [
    'TestCLI',
    'TestHandlers',
    ]


import sys
import unittest

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Debugging, Sink
from io import StringIO
from smtplib import SMTP


class TestHandlers(unittest.TestCase):
    def setUp(self):
        self.stream = StringIO()
        handler = Debugging(self.stream)
        controller = Controller(handler)
        controller.start()
        self.address = (controller.hostname, controller.port)
        self.addCleanup(controller.stop)

    def test_debugging(self):
        with SMTP(*self.address) as client:
            client.sendmail('anne@example.com', ['bart@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test

Testing
""")
        text = self.stream.getvalue()
        self.assertMultiLineEqual(text, """\
---------- MESSAGE FOLLOWS ----------
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
X-Peer: ::1

Testing
------------ END MESSAGE ------------
""")


class FakeParser:
    def __init__(self):
        self.message = None

    def error(self, message):
        self.message = message
        raise SystemExit


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.parser = FakeParser()

    def test_debugging_cli_no_args(self):
        handler = Debugging.from_cli(self.parser)
        self.assertIsNone(self.parser.message)
        self.assertEqual(handler.stream, sys.stdout)

    def test_debugging_cli_two_args(self):
        self.assertRaises(
            SystemExit,
            Debugging.from_cli, self.parser, 'foo', 'bar')
        self.assertEqual(
            self.parser.message, 'Debugging usage: [stdout|stderr]')

    def test_debugging_cli_stdout(self):
        handler = Debugging.from_cli(self.parser, 'stdout')
        self.assertIsNone(self.parser.message)
        self.assertEqual(handler.stream, sys.stdout)

    def test_debugging_cli_stderr(self):
        handler = Debugging.from_cli(self.parser, 'stderr')
        self.assertIsNone(self.parser.message)
        self.assertEqual(handler.stream, sys.stderr)

    def test_debugging_cli_bad_argument(self):
        self.assertRaises(
            SystemExit,
            Debugging.from_cli, self.parser, 'stdfoo')
        self.assertEqual(
            self.parser.message, 'Debugging usage: [stdout|stderr]')

    def test_sink_cli_no_args(self):
        handler = Sink.from_cli(self.parser)
        self.assertIsNone(self.parser.message)
        self.assertIsInstance(handler, Sink)

    def test_sink_cli_any_args(self):
        self.assertRaises(
            SystemExit,
            Sink.from_cli, self.parser, 'foo')
        self.assertEqual(
            self.parser.message, 'Sink handler does not accept arguments')
