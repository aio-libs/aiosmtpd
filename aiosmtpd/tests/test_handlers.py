__all__ = [
    'TestCLI',
    'TestDebugging',
    'TestMailbox',
    'TestMessage',
    ]


import os
import sys
import unittest

from aiosmtpd.smtp import SMTP as Server
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Debugging, Mailbox, Message, Sink
from io import StringIO
from mailbox import Maildir
from operator import itemgetter
from smtplib import SMTP
from tempfile import TemporaryDirectory


class UTF8Controller(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True)


class TestDebugging(unittest.TestCase):
    def setUp(self):
        self.stream = StringIO()
        handler = Debugging(self.stream)
        controller = UTF8Controller(handler)
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


class TestMessage(unittest.TestCase):
    def setUp(self):
        self.handled_message = None

        class MessageHandler(Message):
            def handle_message(handler_self, message):
                self.handled_message = message

        self.handler = MessageHandler()

    def test_message(self):
        # In this test, the message data comes in as bytes.
        controller = Controller(self.handler)
        controller.start()
        self.addCleanup(controller.stop)

        with SMTP(controller.hostname, controller.port) as client:
            client.sendmail('anne@example.com', ['bart@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
Message-ID: <ant>

Testing
""")
        self.assertEqual(self.handled_message['subject'], 'A test')
        self.assertEqual(self.handled_message['message-id'], '<ant>')
        self.assertIsNotNone(self.handled_message['X-Peer'])
        self.assertEqual(
            self.handled_message['X-MailFrom'], 'anne@example.com')
        self.assertEqual(self.handled_message['X-RcptTos'], 'bart@example.com')

    def test_message_decoded(self):
        # With a server that decodes the data, the messages come in as
        # strings.  There's no difference in the message seen by the
        # handler's handle_message() method, but internally this gives full
        # coverage.
        controller = UTF8Controller(self.handler)
        controller.start()
        self.addCleanup(controller.stop)

        with SMTP(controller.hostname, controller.port) as client:
            client.sendmail('anne@example.com', ['bart@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
Message-ID: <ant>

Testing
""")
        self.assertEqual(self.handled_message['subject'], 'A test')
        self.assertEqual(self.handled_message['message-id'], '<ant>')
        self.assertIsNotNone(self.handled_message['X-Peer'])
        self.assertEqual(
            self.handled_message['X-MailFrom'], 'anne@example.com')
        self.assertEqual(self.handled_message['X-RcptTos'], 'bart@example.com')


class TestMailbox(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.maildir_path = os.path.join(self.tempdir.name, 'maildir')
        handler = Mailbox(self.maildir_path)
        controller = Controller(handler)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_mailbox(self):
        with SMTP(*self.address) as client:
            client.sendmail(
                'aperson@example.com', ['bperson@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
Message-ID: <ant>

Hi Bart, this is Anne.
""")
            client.sendmail(
                'cperson@example.com', ['dperson@example.com'], """\
From: Cate Person <cate@example.com>
To: Dave Person <dave@example.com>
Subject: A test
Message-ID: <bee>

Hi Dave, this is Cate.
""")
            client.sendmail(
                'eperson@example.com', ['fperson@example.com'], """\
From: Elle Person <elle@example.com>
To: Fred Person <fred@example.com>
Subject: A test
Message-ID: <cat>

Hi Fred, this is Elle.
""")
        # Check the messages in the mailbox.
        mailbox = Maildir(self.maildir_path)
        messages = sorted(mailbox, key=itemgetter('message-id'))
        self.assertEqual(
            list(message['message-id'] for message in messages),
            ['<ant>', '<bee>', '<cat>'])


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
