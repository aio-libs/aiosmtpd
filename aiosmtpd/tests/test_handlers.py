import os
import sys
import asyncio
import unittest

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import (
    AsyncMessage, Debugging, Mailbox, Message, Proxy, Sink)
from aiosmtpd.smtp import SMTP as Server
from contextlib import ExitStack
from io import StringIO
from mailbox import Maildir
from operator import itemgetter
from smtplib import SMTP, SMTPRecipientsRefused
from tempfile import TemporaryDirectory
from unittest.mock import call, patch

CRLF = '\r\n'


class UTF8Controller(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True)


class TestDebugging(unittest.TestCase):
    def setUp(self):
        self.stream = StringIO()
        handler = Debugging(self.stream)
        controller = UTF8Controller(handler)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_debugging(self):
        with ExitStack() as resources:
            client = resources.enter_context(SMTP(*self.address))
            peer = client.sock.getsockname()
            client.sendmail('anne@example.com', ['bart@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test

Testing
""")
        text = self.stream.getvalue()
        self.assertMultiLineEqual(text, """\
---------- MESSAGE FOLLOWS ----------
mail options: ['SIZE=102']

From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
X-Peer: {!r}

Testing
------------ END MESSAGE ------------
""".format(peer))


class TestDebuggingBytes(unittest.TestCase):
    def setUp(self):
        self.stream = StringIO()
        handler = Debugging(self.stream)
        controller = Controller(handler)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_debugging(self):
        with ExitStack() as resources:
            client = resources.enter_context(SMTP(*self.address))
            peer = client.sock.getsockname()
            client.sendmail('anne@example.com', ['bart@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test

Testing
""")
        text = self.stream.getvalue()
        self.assertMultiLineEqual(text, """\
---------- MESSAGE FOLLOWS ----------
mail options: ['SIZE=102']

From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
X-Peer: {!r}

Testing
------------ END MESSAGE ------------
""".format(peer))


class TestDebuggingOptions(unittest.TestCase):
    def setUp(self):
        self.stream = StringIO()
        handler = Debugging(self.stream)
        controller = Controller(handler)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_debugging_without_options(self):
        with SMTP(*self.address) as client:
            # Prevent ESMTP options.
            client.helo()
            peer = client.sock.getsockname()
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
X-Peer: {!r}

Testing
------------ END MESSAGE ------------
""".format(peer))

    def test_debugging_with_options(self):
        with SMTP(*self.address) as client:
            peer = client.sock.getsockname()
            client.sendmail('anne@example.com', ['bart@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test

Testing
""", mail_options=['BODY=7BIT'])
        text = self.stream.getvalue()
        self.assertMultiLineEqual(text, """\
---------- MESSAGE FOLLOWS ----------
mail options: ['SIZE=102', 'BODY=7BIT']

From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
X-Peer: {!r}

Testing
------------ END MESSAGE ------------
""".format(peer))


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
        self.assertEqual(self.handled_message['X-RcptTo'], 'bart@example.com')

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
        self.assertEqual(self.handled_message['X-RcptTo'], 'bart@example.com')


class TestAsyncMessage(unittest.TestCase):
    def setUp(self):
        self.handled_message = None

        class MessageHandler(AsyncMessage):
            @asyncio.coroutine
            def handle_message(handler_self, message, loop):
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
        self.assertEqual(self.handled_message['X-RcptTo'], 'bart@example.com')

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
        self.assertEqual(self.handled_message['X-RcptTo'], 'bart@example.com')


class TestMailbox(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.maildir_path = os.path.join(self.tempdir.name, 'maildir')
        self.handler = handler = Mailbox(self.maildir_path)
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

    def test_mailbox_reset(self):
        with SMTP(*self.address) as client:
            client.sendmail(
                'aperson@example.com', ['bperson@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
Message-ID: <ant>

Hi Bart, this is Anne.
""")
        self.handler.reset()
        mailbox = Maildir(self.maildir_path)
        self.assertEqual(list(mailbox), [])


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


class TestProxy(unittest.TestCase):
    def setUp(self):
        self.stream = StringIO()
        handler = Proxy('localhost', 9025)
        controller = UTF8Controller(handler)
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)
        self.message = """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test

Testing
"""

    def test_deliver(self):
        with ExitStack() as resources:
            mock = resources.enter_context(
                patch('aiosmtpd.handlers.smtplib.SMTP'))
            client = resources.enter_context(SMTP(*self.address))
            client.sendmail(
                'anne@example.com', ['bart@example.com'], self.message)
            client.quit()
            mock().connect.assert_called_once_with('localhost', 9025)
            # SMTP always fixes eols, so it must be always CRLF as delimiter
            msg = CRLF.join([
                'From: Anne Person <anne@example.com>',
                'To: Bart Person <bart@example.com>',
                'Subject: A test',
                'X-Peer: ::1',
                '',
                'Testing'])
            mock().sendmail.assert_called_once_with(
                'anne@example.com', ['bart@example.com'], msg)
            mock().quit.assert_called_once_with()

    def test_recipients_refused(self):
        with ExitStack() as resources:
            log_mock = resources.enter_context(patch('aiosmtpd.handlers.log'))
            mock = resources.enter_context(
                patch('aiosmtpd.handlers.smtplib.SMTP'))
            mock().sendmail.side_effect = SMTPRecipientsRefused({
                'bart@example.com': (500, 'Bad Bart'),
                })
            client = resources.enter_context(SMTP(*self.address))
            client.sendmail(
                'anne@example.com', ['bart@example.com'], self.message)
            client.quit()
            # The log contains information about what happened in the proxy.
            self.assertEqual(
                log_mock.info.call_args_list, [
                    call('got SMTPRecipientsRefused'),
                    call('we got some refusals: %s',
                         {'bart@example.com': (500, 'Bad Bart')})]
                )

    def test_oserror(self):
        with ExitStack() as resources:
            log_mock = resources.enter_context(patch('aiosmtpd.handlers.log'))
            mock = resources.enter_context(
                patch('aiosmtpd.handlers.smtplib.SMTP'))
            mock().sendmail.side_effect = OSError
            client = resources.enter_context(SMTP(*self.address))
            client.sendmail(
                'anne@example.com', ['bart@example.com'], self.message)
            client.quit()
            # The log contains information about what happened in the proxy.
            self.assertEqual(
                log_mock.info.call_args_list, [
                    call('we got some refusals: %s',
                         {'bart@example.com': (-1, 'ignore')}),
                    ]
                )
