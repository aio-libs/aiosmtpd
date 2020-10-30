import sys
import pytest
import logging

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import AsyncMessage, Debugging, Mailbox, Proxy, Sink
from aiosmtpd.smtp import SMTP as Server
from io import StringIO
from mailbox import Maildir
from operator import itemgetter
from pathlib import Path
from smtplib import SMTP, SMTPDataError, SMTPRecipientsRefused
from textwrap import dedent
from typing import Any, NamedTuple


CRLF = '\r\n'
SERVER_ADDRESS = ("localhost", 8025)


class ControllerStream(NamedTuple):
    controller: Controller
    stream: StringIO


class ControllerHandler(NamedTuple):
    controller: Controller
    handler: Any


# region ##### Support Classes ###############################################

class DecodingController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True)


class AUTHDecodingController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True, auth_require_tls=False)


class FakeParser:
    def __init__(self):
        self.message = None

    def error(self, message):
        self.message = message
        raise SystemExit


class DataHandler:
    def __init__(self):
        self.content = None
        self.original_content = None

    async def handle_DATA(self, server, session, envelope):
        self.content = envelope.content
        self.original_content = envelope.original_content
        return '250 OK'


class AsyncMessageHandler(AsyncMessage):
    handled_message = None

    async def handle_message(self, message):
        self.handled_message = message


class HELOHandler:
    async def handle_HELO(self, server, session, envelope, hostname):
        return '250 geddy.example.com'


class EHLOHandler:
    async def handle_EHLO(self, server, session, envelope, hostname):
        return '250 alex.example.com'


class MAILHandler:
    async def handle_MAIL(self, server, session, envelope, address, options):
        envelope.mail_options.extend(options)
        return '250 Yeah, sure'


class RCPTHandler:
    async def handle_RCPT(self, server, session, envelope, address, options):
        envelope.rcpt_options.extend(options)
        if address == 'bart@example.com':
            return '550 Rejected'
        envelope.rcpt_tos.append(address)
        return '250 OK'


class DATAHandler:
    async def handle_DATA(self, server, session, envelope):
        return '599 Not today'


class AUTHHandler:
    async def handle_AUTH(self, server, session, envelope, args):
        server.authenticates = True
        return '235 Authentication successful'


class NoHooksHandler:
    pass


class DeprecatedHookController(Controller):

    smtpd: "DeprecatedHookController.DeprecatedHookServer" = None

    class DeprecatedHookServer(Server):

        warnings: list = None

        def __init__(self, *args, **kws):
            super().__init__(*args, **kws)

        async def ehlo_hook(self):
            pass

        async def rset_hook(self):
            pass

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def factory(self):
        self.smtpd = \
            DeprecatedHookController.DeprecatedHookServer(self.handler)
        return self.smtpd


class DeprecatedHandler:
    def process_message(self, peer, mailfrom, rcpttos, data, **kws):
        pass


class AsyncDeprecatedHandler:
    async def process_message(self, peer, mailfrom, rcpttos, data, **kws):
        pass

# endregion


# region ##### Fixtures #######################################################

@pytest.fixture
def client() -> SMTP:
    with SMTP(*SERVER_ADDRESS) as client:
        yield client


@pytest.fixture
def contrs_debugging() -> ControllerStream:
    stream = StringIO()
    handler = Debugging(stream)
    controller = Controller(handler)
    controller.start()
    #
    yield ControllerStream(controller, stream)
    #
    controller.stop()
    stream.close()


@pytest.fixture
def contrs_debugging_decoding() -> ControllerStream:
    stream = StringIO()
    handler = Debugging(stream)
    controller = DecodingController(handler)
    controller.start()
    #
    yield ControllerStream(controller, stream)
    #
    controller.stop()
    stream.close()


@pytest.fixture
def temp_maildir(tmp_path: Path) -> Path:
    maildir_path = tmp_path / "maildir"
    yield maildir_path


@pytest.fixture
def contrh_mailbox(temp_maildir) -> ControllerHandler:
    handler = Mailbox(temp_maildir)
    controller = Controller(handler)
    controller.start()
    #
    yield ControllerHandler(controller, handler)
    #
    controller.stop()


@pytest.fixture
def fake_parser() -> FakeParser:
    yield FakeParser()


@pytest.fixture
def contrh_upstream() -> ControllerHandler:
    upstream_handler = DataHandler()
    upstream_controller = Controller(upstream_handler, port=9025)
    upstream_controller.start()
    #
    yield ControllerHandler(upstream_controller, upstream_handler)
    #
    upstream_controller.stop()


@pytest.fixture
def contr_proxy(contrh_upstream) -> Controller:
    proxy_handler = Proxy(
        contrh_upstream.controller.hostname,
        contrh_upstream.controller.port
    )
    proxy_controller = Controller(proxy_handler)
    proxy_controller.start()
    #
    yield proxy_controller
    #
    proxy_controller.stop()


@pytest.fixture
def contr_proxy_decoding(contrh_upstream) -> Controller:
    proxy_handler = Proxy(
        contrh_upstream.controller.hostname,
        contrh_upstream.controller.port
    )
    proxy_controller = DecodingController(proxy_handler)
    proxy_controller.start()
    #
    yield proxy_controller
    #
    proxy_controller.stop()


def _get_handler(request):
    testname: str = request.node.name
    command = testname.split("_")[-1]
    handler_name = command + "Handler"
    handler_class = globals()[handler_name]
    return handler_class()


@pytest.fixture
def paramzd_contrh_handled(request) -> ControllerHandler:
    handler = _get_handler(request)
    controller = Controller(handler)
    controller.start()
    #
    yield ControllerHandler(controller, handler)
    #
    controller.stop()


@pytest.fixture
def paramzd_contrh_handled_decoding(request) -> ControllerHandler:
    handler = _get_handler(request)
    controller = DecodingController(handler)
    controller.start()
    #
    yield ControllerHandler(controller, handler)
    #
    controller.stop()


@pytest.fixture
def contrh_auth_decoding() -> ControllerHandler:
    handler = AUTHHandler()
    controller = AUTHDecodingController(handler)
    controller.start()
    #
    yield ControllerHandler(controller, handler)
    #
    controller.stop()


@pytest.fixture
def contr_deprecated_hook() -> DeprecatedHookController:
    controller = DeprecatedHookController(Sink())
    controller.start()
    #
    yield controller
    #
    controller.stop()


# endregion


class TestDebugging:

    def test_debugging(self, contrs_debugging_decoding, client):
        peer = client.sock.getsockname()
        client.sendmail('anne@example.com', ['bart@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test

            Testing
            """))
        text = contrs_debugging_decoding.stream.getvalue()
        assert text == dedent("""\
            ---------- MESSAGE FOLLOWS ----------
            mail options: ['SIZE=102']

            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            X-Peer: {!r}

            Testing
            ------------ END MESSAGE ------------
            """.format(peer))

    def test_debugging_bytes(self, contrs_debugging_decoding, client):
        peer = client.sock.getsockname()
        client.sendmail('anne@example.com', ['bart@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test

            Testing
            """))
        text = contrs_debugging_decoding.stream.getvalue()
        assert text == dedent("""\
            ---------- MESSAGE FOLLOWS ----------
            mail options: ['SIZE=102']

            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            X-Peer: {!r}

            Testing
            ------------ END MESSAGE ------------
            """.format(peer))

    def test_debugging_without_options(self, contrs_debugging, client):
        # Prevent ESMTP options.
        client.helo()
        peer = client.sock.getsockname()
        client.sendmail('anne@example.com', ['bart@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test

            Testing
            """))
        text = contrs_debugging.stream.getvalue()
        assert text == dedent("""\
            ---------- MESSAGE FOLLOWS ----------
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            X-Peer: {!r}

            Testing
            ------------ END MESSAGE ------------
            """.format(peer))

    def test_debugging_with_options(self, contrs_debugging, client):
        peer = client.sock.getsockname()
        client.sendmail(
            'anne@example.com', ['bart@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test

            Testing
            """),
            mail_options=['BODY=7BIT'])
        text = contrs_debugging.stream.getvalue()
        assert text == dedent("""\
            ---------- MESSAGE FOLLOWS ----------
            mail options: ['SIZE=102', 'BODY=7BIT']

            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            X-Peer: {!r}

            Testing
            ------------ END MESSAGE ------------
            """.format(peer))


class TestMessage:

    def test_message_Data(self, paramzd_contrh_handled, client):
        handler = paramzd_contrh_handled.handler
        assert isinstance(handler, DataHandler)
        # In this test, the message content comes in as a bytes.
        client.sendmail('anne@example.com', ['bart@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            Message-ID: <ant>

            Testing
            """))
        # The content is not converted, so it's bytes.
        assert handler.content == handler.original_content
        assert isinstance(handler.content, bytes)
        assert isinstance(handler.original_content, bytes)

    def test_message_decoded_Data(
            self, paramzd_contrh_handled_decoding, client):
        handler = paramzd_contrh_handled_decoding.handler
        assert isinstance(handler, DataHandler)
        # In this test, the message content comes in as a string.
        client.sendmail('anne@example.com', ['bart@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            Message-ID: <ant>

            Testing
            """))
        assert handler.content != handler.original_content
        assert isinstance(handler.content, str)
        assert isinstance(handler.original_content, bytes)

    def test_message_AsyncMessage(self, paramzd_contrh_handled, client):
        handler = paramzd_contrh_handled.handler
        assert isinstance(handler, AsyncMessageHandler)
        # In this test, the message data comes in as bytes.
        client.sendmail('anne@example.com', ['bart@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            Message-ID: <ant>

            Testing
            """))
        handled_message = handler.handled_message
        assert handled_message['subject'] == 'A test'
        assert handled_message['message-id'] == '<ant>'
        assert handled_message['X-Peer'] is not None
        assert handled_message['X-MailFrom'] == 'anne@example.com'
        assert handled_message['X-RcptTo'] == 'bart@example.com'

    def test_message_decoded_AsyncMessage(
            self, paramzd_contrh_handled_decoding, client):
        handler = paramzd_contrh_handled_decoding.handler
        assert isinstance(handler, AsyncMessageHandler)
        # With a server that decodes the data, the messages come in as
        # strings.  There's no difference in the message seen by the
        # handler's handle_message() method, but internally this gives full
        # coverage.
        client.sendmail('anne@example.com', ['bart@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            Message-ID: <ant>

            Testing
            """))
        handled_message = handler.handled_message
        assert handled_message['subject'] == 'A test'
        assert handled_message['message-id'] == '<ant>'
        assert handled_message['X-Peer'] is not None
        assert handled_message['X-MailFrom'] == 'anne@example.com'
        assert handled_message['X-RcptTo'] == 'bart@example.com'


class TestMailbox:

    def test_mailbox(self, temp_maildir, contrh_mailbox, client):
        client.sendmail(
            'aperson@example.com', ['bperson@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            Message-ID: <ant>

            Hi Bart, this is Anne.
            """))
        client.sendmail(
            'cperson@example.com', ['dperson@example.com'], dedent("""\
            From: Cate Person <cate@example.com>
            To: Dave Person <dave@example.com>
            Subject: A test
            Message-ID: <bee>

            Hi Dave, this is Cate.
            """))
        client.sendmail(
            'eperson@example.com', ['fperson@example.com'], dedent("""\
            From: Elle Person <elle@example.com>
            To: Fred Person <fred@example.com>
            Subject: A test
            Message-ID: <cat>

            Hi Fred, this is Elle.
            """))
        # Check the messages in the mailbox.
        mailbox = Maildir(temp_maildir)
        messages = sorted(mailbox, key=itemgetter('message-id'))
        assert (
            list(message['message-id'] for message in messages)
            == ['<ant>', '<bee>', '<cat>']
        )

    def test_mailbox_reset(self, temp_maildir, contrh_mailbox, client):
        client.sendmail(
            'aperson@example.com', ['bperson@example.com'], dedent("""\
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            Message-ID: <ant>

            Hi Bart, this is Anne.
            """))
        contrh_mailbox.handler.reset()
        mailbox = Maildir(temp_maildir)
        assert list(mailbox) == []


class TestCLI:

    def test_no_args(self, fake_parser):
        handler = Debugging.from_cli(fake_parser)
        assert fake_parser.message is None
        assert handler.stream == sys.stdout

    def test_two_args(self, fake_parser):
        with pytest.raises(SystemExit):
            Debugging.from_cli(fake_parser, "foo", "bar")
        assert fake_parser.message == 'Debugging usage: [stdout|stderr]'

    def test_stdout(self, fake_parser):
        handler = Debugging.from_cli(fake_parser, 'stdout')
        assert fake_parser.message is None
        assert handler.stream == sys.stdout

    def test_stderr(self, fake_parser):
        handler = Debugging.from_cli(fake_parser, 'stderr')
        assert fake_parser.message is None
        assert handler.stream == sys.stderr

    def test_bad_argument(self, fake_parser):
        with pytest.raises(SystemExit):
            Debugging.from_cli(fake_parser, "stdfoo")
        assert fake_parser.message == 'Debugging usage: [stdout|stderr]'

    def test_sink_no_args(self, fake_parser):
        handler = Sink.from_cli(fake_parser)
        assert isinstance(handler, Sink)
        assert fake_parser.message is None

    def test_sink_any_args(self, fake_parser):
        with pytest.raises(SystemExit):
            Sink.from_cli(fake_parser, "foo")
        assert fake_parser.message, 'Sink handler does not accept arguments'

    def test_mailbox_no_args(self, fake_parser):
        with pytest.raises(SystemExit):
            Mailbox.from_cli(fake_parser)
        assert (
            fake_parser.message == 'The directory for the maildir is required'
        )

    def test_mailbox_too_many_args(self, fake_parser):
        with pytest.raises(SystemExit):
            Mailbox.from_cli(fake_parser, "foo", "bar", "baz")
        assert fake_parser.message == 'Too many arguments for Mailbox handler'

    def test_mailbox(self, fake_parser, temp_maildir):
        handler = Mailbox.from_cli(fake_parser, temp_maildir)
        assert isinstance(handler.mailbox, Maildir)
        assert handler.mail_dir == temp_maildir


class TestProxy:
    source = dedent("""\
        From: Anne Person <anne@example.com>
        To: Bart Person <bart@example.com>
        Subject: A test

        Testing
        """)

    # The upstream SMTPd will always receive the content as bytes
    # delimited with CRLF.
    expected = CRLF.join([
        'From: Anne Person <anne@example.com>',
        'To: Bart Person <bart@example.com>',
        'Subject: A test',
        'X-Peer: ::1',
        '',
        'Testing\r\n']).encode('ascii')

    # There are two controllers and two SMTPd's running here.  The
    # "upstream" one listens on port 9025 and is connected to a "data
    # handler" which captures the messages it receives.  The second -and
    # the one under test here- listens on port 9024 and proxies to the one
    # on port 9025.  Because we need to set the decode_data flag
    # differently for each different test, the controller of the proxy is
    # created in the individual tests, not in the setup.

    def test_deliver_bytes(self, contrh_upstream, contr_proxy, client):
        client.sendmail(
            'anne@example.com', ['bart@example.com'], self.source)
        upstream = contrh_upstream.handler
        assert upstream.content == self.expected
        assert upstream.original_content == self.expected

    def test_deliver_str(self, contrh_upstream, contr_proxy_decoding, client):
        client.sendmail(
            'anne@example.com', ['bart@example.com'], self.source)
        upstream = contrh_upstream.handler
        assert upstream.content == self.expected
        assert upstream.original_content == self.expected


class TestProxyMocked:
    BAD_BART = {'bart@example.com': (500, 'Bad Bart')}
    SOURCE = dedent("""\
        From: Anne Person <anne@example.com>
        To: Bart Person <bart@example.com>
        Subject: A test

        Testing
        """)

    @pytest.fixture
    def patch_smtp_refused(self, mocker):
        mock = mocker.patch('aiosmtpd.handlers.smtplib.SMTP')
        mock().sendmail.side_effect = SMTPRecipientsRefused(self.BAD_BART)

    def test_recipients_refused(self, caplog, patch_smtp_refused,
                                contr_proxy_decoding, client):
        logger_name = "mail.debug"
        caplog.set_level(logging.INFO, logger=logger_name)
        client.sendmail(
            'anne@example.com', ['bart@example.com'], self.SOURCE)
        # The log contains information about what happened in the proxy.
        assert caplog.record_tuples[-2] == (
            logger_name, logging.INFO, 'got SMTPRecipientsRefused'
        )
        assert caplog.record_tuples[-1] == (
            logger_name, logging.INFO, f'we got some refusals: {self.BAD_BART}'
        )

    @pytest.fixture
    def patch_smtp_oserror(self, mocker):
        mock = mocker.patch('aiosmtpd.handlers.smtplib.SMTP')
        mock().sendmail.side_effect = OSError
        yield

    def test_oserror(self, caplog, patch_smtp_oserror, contr_proxy_decoding,
                     client):
        logger_name = "mail.debug"
        caplog.set_level(logging.INFO, logger=logger_name)
        client.sendmail(
            'anne@example.com', ['bart@example.com'], self.SOURCE)
        assert caplog.record_tuples[-1] == (
            logger_name,
            logging.INFO,
            "we got some refusals: {'bart@example.com': (-1, 'ignore')}",
        )


class TestHooks:

    def test_hook_HELO(self, paramzd_contrh_handled, client):
        assert isinstance(paramzd_contrh_handled.handler, HELOHandler)
        resp = client.helo("me")
        assert resp == (250, b"geddy.example.com")

    def test_hook_EHLO(self, paramzd_contrh_handled, client):
        assert isinstance(paramzd_contrh_handled.handler, EHLOHandler)
        code, mesg = client.ehlo("me")
        lines = mesg.decode("utf-8").splitlines()
        assert code == 250
        assert lines[-1] == "alex.example.com"

    def test_hook_MAIL(self, paramzd_contrh_handled, client):
        assert isinstance(paramzd_contrh_handled.handler, MAILHandler)
        client.helo("me")
        resp = client.mail("anne@example.com")
        assert resp == (250, b"Yeah, sure")

    def test_hook_RCPT(self, paramzd_contrh_handled, client):
        assert isinstance(paramzd_contrh_handled.handler, RCPTHandler)
        client.helo("me")
        with pytest.raises(SMTPRecipientsRefused) as excinfo:
            client.sendmail(
                'anne@example.com', ['bart@example.com'], dedent("""\
                    From: anne@example.com
                    To: bart@example.com
                    Subject: Test

                    """)
            )
        assert excinfo.value.recipients == {
            'bart@example.com': (550, b'Rejected'),
        }

    def test_hook_DATA(self, paramzd_contrh_handled, client):
        assert isinstance(paramzd_contrh_handled.handler, DATAHandler)
        with pytest.raises(SMTPDataError) as excinfo:
            client.sendmail(
                "anne@example.com", ["bart@example.com"], dedent("""\
                    From: anne@example.com
                    To: bart@example.com
                    Subject: Test

                    Yikes
                    """)
            )
        assert excinfo.value.smtp_code == 599
        assert excinfo.value.smtp_error == b"Not today"

    def test_hook_AUTH(self, contrh_auth_decoding, client):
        assert isinstance(contrh_auth_decoding.handler, AUTHHandler)
        client.ehlo("me")
        resp = client.login("test", "test")
        assert resp == (235, b"Authentication successful")

    def test_hook_NoHooks(self, paramzd_contrh_handled, client):
        assert isinstance(paramzd_contrh_handled.handler, NoHooksHandler)
        client.helo("me")
        client.mail("anne@example.com")
        client.rcpt(["bart@example.cm"])
        code, _ = client.data(dedent("""\
            From: anne@example.com
            To: bart@example.com
            Subject: Test

            """))
        assert code == 250


class TestDeprecation:

    def _process_message_testing(self, controller, client):
        assert isinstance(controller, Controller)
        with pytest.warns(DeprecationWarning) as record:
            client.sendmail(
                'anne@example.com', ['bart@example.com'], dedent("""
                    From: Anne Person <anne@example.com>
                    To: Bart Person <bart@example.com>
                    Subject: A test
    
                    Testing
                    """)
            )
        assert len(record) == 1
        assert record[0].message.args[0] == \
            'Use handler.handle_DATA() instead of .process_message()'

    def test_process_message_Deprecated(self, paramzd_contrh_handled, client):
        """handler.process_message is Deprecated"""
        handler = paramzd_contrh_handled.handler
        assert isinstance(handler, DeprecatedHandler)
        controller = paramzd_contrh_handled.controller
        self._process_message_testing(controller, client)

    def test_process_message_AsyncDeprecated(self, paramzd_contrh_handled,
                                             client):
        """handler.process_message is Deprecated"""
        handler = paramzd_contrh_handled.handler
        assert isinstance(handler, AsyncDeprecatedHandler)
        controller = paramzd_contrh_handled.controller
        self._process_message_testing(controller, client)

    def test_ehlo_hook_warn(self, contr_deprecated_hook, client):
        """SMTP.ehlo_hook is Deprecated"""
        controller: DeprecatedHookController = contr_deprecated_hook
        with pytest.warns(DeprecationWarning) as record:
            client.ehlo("example.com")
        assert len(record) == 1
        assert record[0].message.args[0] == \
            'Use handler.handle_EHLO() instead of .ehlo_hook()'

    def test_rset_hook(self, contr_deprecated_hook, client):
        """SMTP.rset_hook is Deprecated"""
        controller: DeprecatedHookController = contr_deprecated_hook
        with pytest.warns(DeprecationWarning) as record:
            client.rset()
        assert len(record) == 1
        assert record[0].message.args[0] == \
            'Use handler.handle_RSET() instead of .rset_hook()'
