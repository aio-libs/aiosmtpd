import sys
import pytest
import logging

from .conftest import ExposingController, Global
from aiosmtpd.handlers import AsyncMessage, Debugging, Mailbox, Proxy, Sink
from aiosmtpd.smtp import SMTP as Server, Session as ServerSession
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S, StatusCode
from io import StringIO
from mailbox import Maildir
from operator import itemgetter
from pathlib import Path
from smtplib import SMTPDataError, SMTPRecipientsRefused
from textwrap import dedent


CRLF = "\r\n"


# region ##### Support Classes ###############################################


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
        return S.S250_OK.to_str()


class AsyncMessageHandler(AsyncMessage):
    handled_message = None

    async def handle_message(self, message):
        self.handled_message = message


class HELOHandler:
    async def handle_HELO(self, server, session, envelope, hostname):
        return "250 geddy.example.com"


class EHLOHandler:
    async def handle_EHLO(self, server, session, envelope, hostname):
        return "250 alex.example.com"


class MAILHandler:
    ReplacementOptions = ["WAS_HANDLED"]
    ReturnCode = StatusCode(250, b"Yeah, sure")

    async def handle_MAIL(self, server, session, envelope, address, options):
        envelope.mail_options = self.ReplacementOptions
        return self.ReturnCode.to_str()


class RCPTHandler:
    RejectCode = StatusCode(550, b"Rejected")

    async def handle_RCPT(self, server, session, envelope, address, options):
        envelope.rcpt_options.extend(options)
        if address == "bart@example.com":
            return self.RejectCode.to_str()
        envelope.rcpt_tos.append(address)
        return S.S250_OK.to_str()


class ErroringDataHandler:
    async def handle_DATA(self, server, session, envelope):
        return "599 Not today"


class AUTHHandler:
    async def handle_AUTH(self, server, session, envelope, args):
        server.authenticates = True
        return S.S235_AUTH_SUCCESS.to_str()


class NoHooksHandler:
    pass


class DeprecatedHookController(ExposingController):
    class DeprecatedHookServer(Server):

        warnings: list = None

        def __init__(self, *args, **kws):
            super().__init__(*args, **kws)

        async def ehlo_hook(self):
            pass

        async def rset_hook(self):
            pass

    def factory(self):
        self.smtpd = self.DeprecatedHookServer(self.handler)
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
def debugging_controller(get_controller) -> ExposingController:
    stream = StringIO()
    handler = Debugging(stream)
    controller = get_controller(handler)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()
    stream.close()


@pytest.fixture
def debugging_decoding_controller(get_controller) -> ExposingController:
    # Cannot use decoding_controller fixture because we need to first create the
    # Debugging handler before creating the controller.
    stream = StringIO()
    handler = Debugging(stream)
    controller = get_controller(handler, decode_data=True)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()
    stream.close()


@pytest.fixture
def temp_maildir(tmp_path: Path) -> Path:
    maildir_path = tmp_path / "maildir"
    yield maildir_path


@pytest.fixture
def mailbox_controller(temp_maildir, get_controller) -> ExposingController:
    handler = Mailbox(temp_maildir)
    controller = get_controller(handler)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def fake_parser() -> FakeParser:
    yield FakeParser()


@pytest.fixture
def upstream_controller(get_controller) -> ExposingController:
    upstream_handler = DataHandler()
    upstream_controller = get_controller(upstream_handler, port=9025)
    upstream_controller.start()
    # Notice that we do NOT invoke Global.set_addr_from() here
    #
    yield upstream_controller
    #
    upstream_controller.stop()


@pytest.fixture
def proxy_controller(upstream_controller, get_controller) -> ExposingController:
    proxy_handler = Proxy(upstream_controller.hostname, upstream_controller.port)
    proxy_controller = get_controller(proxy_handler)
    proxy_controller.start()
    Global.set_addr_from(proxy_controller)
    #
    yield proxy_controller
    #
    proxy_controller.stop()


@pytest.fixture
def proxy_decoding_controller(
    upstream_controller, get_controller
) -> ExposingController:
    proxy_handler = Proxy(upstream_controller.hostname, upstream_controller.port)
    proxy_controller = get_controller(proxy_handler, decode_data=True)
    proxy_controller.start()
    Global.set_addr_from(proxy_controller)
    #
    yield proxy_controller
    #
    proxy_controller.stop()


@pytest.fixture
def auth_decoding_controller(get_controller) -> ExposingController:
    handler = AUTHHandler()
    controller = get_controller(handler, decode_data=True, auth_require_tls=False)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def deprecated_hook_controller() -> DeprecatedHookController:
    controller = DeprecatedHookController(Sink())
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


# endregion


class TestDebugging:
    def test_debugging(self, debugging_decoding_controller, client):
        peer = client.sock.getsockname()
        client.sendmail(
            "anne@example.com",
            ["bart@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
    
                Testing
                """
            ),
        )
        handler = debugging_decoding_controller.handler
        assert isinstance(handler, Debugging)
        text = handler.stream.getvalue()
        assert text == dedent(
            f"""\
            ---------- MESSAGE FOLLOWS ----------
            mail options: ['SIZE=102']

            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            X-Peer: {peer!r}

            Testing
            ------------ END MESSAGE ------------
            """
        )

    def test_debugging_bytes(self, debugging_controller, client):
        peer = client.sock.getsockname()
        client.sendmail(
            "anne@example.com",
            ["bart@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
    
                Testing
                """
            ),
        )
        handler = debugging_controller.handler
        assert isinstance(handler, Debugging)
        text = handler.stream.getvalue()
        assert text == dedent(
            f"""\
            ---------- MESSAGE FOLLOWS ----------
            mail options: ['SIZE=102']

            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            X-Peer: {peer!r}

            Testing
            ------------ END MESSAGE ------------
            """
        )

    def test_debugging_without_options(self, debugging_controller, client):
        # Prevent ESMTP options.
        client.helo()
        peer = client.sock.getsockname()
        client.sendmail(
            "anne@example.com",
            ["bart@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
    
                Testing
                """
            ),
        )
        handler = debugging_controller.handler
        assert isinstance(handler, Debugging)
        text = handler.stream.getvalue()
        assert text == dedent(
            f"""\
            ---------- MESSAGE FOLLOWS ----------
            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            X-Peer: {peer!r}

            Testing
            ------------ END MESSAGE ------------
            """
        )

    def test_debugging_with_options(self, debugging_controller, client):
        peer = client.sock.getsockname()
        client.sendmail(
            "anne@example.com",
            ["bart@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
    
                Testing
                """
            ),
            mail_options=["BODY=7BIT"],
        )
        handler = debugging_controller.handler
        assert isinstance(handler, Debugging)
        text = handler.stream.getvalue()
        assert text == dedent(
            f"""\
            ---------- MESSAGE FOLLOWS ----------
            mail options: ['SIZE=102', 'BODY=7BIT']

            From: Anne Person <anne@example.com>
            To: Bart Person <bart@example.com>
            Subject: A test
            X-Peer: {peer!r}

            Testing
            ------------ END MESSAGE ------------
            """
        )


class TestMessage:
    @pytest.mark.handler_data(class_=DataHandler)
    def test_message_Data(self, plain_controller, client):
        handler = plain_controller.handler
        assert isinstance(handler, DataHandler)
        # In this test, the message content comes in as a bytes.
        client.sendmail(
            "anne@example.com",
            ["bart@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
                Message-ID: <ant>
    
                Testing
                """
            ),
        )
        # The content is not converted, so it's bytes.
        assert handler.content == handler.original_content
        assert isinstance(handler.content, bytes)
        assert isinstance(handler.original_content, bytes)

    @pytest.mark.handler_data(class_=DataHandler)
    def test_message_decoded_Data(self, decoding_controller, client):
        handler = decoding_controller.handler
        assert isinstance(handler, DataHandler)
        # In this test, the message content comes in as a string.
        client.sendmail(
            "anne@example.com",
            ["bart@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
                Message-ID: <ant>
    
                Testing
                """
            ),
        )
        assert handler.content != handler.original_content
        assert isinstance(handler.content, str)
        assert isinstance(handler.original_content, bytes)

    @pytest.mark.handler_data(class_=AsyncMessageHandler)
    def test_message_AsyncMessage(self, plain_controller, client):
        handler = plain_controller.handler
        assert isinstance(handler, AsyncMessageHandler)
        # In this test, the message data comes in as bytes.
        client.sendmail(
            "anne@example.com",
            ["bart@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
                Message-ID: <ant>
    
                Testing
                """
            ),
        )
        handled_message = handler.handled_message
        assert handled_message["subject"] == "A test"
        assert handled_message["message-id"] == "<ant>"
        assert handled_message["X-Peer"] is not None
        assert handled_message["X-MailFrom"] == "anne@example.com"
        assert handled_message["X-RcptTo"] == "bart@example.com"

    @pytest.mark.handler_data(class_=AsyncMessageHandler)
    def test_message_decoded_AsyncMessage(self, decoding_controller, client):
        handler = decoding_controller.handler
        assert isinstance(handler, AsyncMessageHandler)
        # With a server that decodes the data, the messages come in as
        # strings.  There's no difference in the message seen by the
        # handler's handle_message() method, but internally this gives full
        # coverage.
        client.sendmail(
            "anne@example.com",
            ["bart@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
                Message-ID: <ant>
    
                Testing
                """
            ),
        )
        handled_message = handler.handled_message
        assert handled_message["subject"] == "A test"
        assert handled_message["message-id"] == "<ant>"
        assert handled_message["X-Peer"] is not None
        assert handled_message["X-MailFrom"] == "anne@example.com"
        assert handled_message["X-RcptTo"] == "bart@example.com"


class TestMailbox:
    def test_mailbox(self, temp_maildir, mailbox_controller, client):
        client.sendmail(
            "aperson@example.com",
            ["bperson@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
                Message-ID: <ant>
    
                Hi Bart, this is Anne.
                """
            ),
        )
        client.sendmail(
            "cperson@example.com",
            ["dperson@example.com"],
            dedent(
                """\
                From: Cate Person <cate@example.com>
                To: Dave Person <dave@example.com>
                Subject: A test
                Message-ID: <bee>
    
                Hi Dave, this is Cate.
                """
            ),
        )
        client.sendmail(
            "eperson@example.com",
            ["fperson@example.com"],
            dedent(
                """\
                From: Elle Person <elle@example.com>
                To: Fred Person <fred@example.com>
                Subject: A test
                Message-ID: <cat>
    
                Hi Fred, this is Elle.
                """
            ),
        )
        # Check the messages in the mailbox.
        mailbox = Maildir(temp_maildir)
        messages = sorted(mailbox, key=itemgetter("message-id"))
        assert list(message["message-id"] for message in messages) == [
            "<ant>",
            "<bee>",
            "<cat>",
        ]

    def test_mailbox_reset(self, temp_maildir, mailbox_controller, client):
        client.sendmail(
            "aperson@example.com",
            ["bperson@example.com"],
            dedent(
                """\
                From: Anne Person <anne@example.com>
                To: Bart Person <bart@example.com>
                Subject: A test
                Message-ID: <ant>
    
                Hi Bart, this is Anne.
                """
            ),
        )
        mailbox_controller.handler.reset()
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
        assert fake_parser.message == "Debugging usage: [stdout|stderr]"

    def test_stdout(self, fake_parser):
        handler = Debugging.from_cli(fake_parser, "stdout")
        assert fake_parser.message is None
        assert handler.stream == sys.stdout

    def test_stderr(self, fake_parser):
        handler = Debugging.from_cli(fake_parser, "stderr")
        assert fake_parser.message is None
        assert handler.stream == sys.stderr

    def test_bad_argument(self, fake_parser):
        with pytest.raises(SystemExit):
            Debugging.from_cli(fake_parser, "stdfoo")
        assert fake_parser.message == "Debugging usage: [stdout|stderr]"

    def test_sink_no_args(self, fake_parser):
        handler = Sink.from_cli(fake_parser)
        assert isinstance(handler, Sink)
        assert fake_parser.message is None

    def test_sink_any_args(self, fake_parser):
        with pytest.raises(SystemExit):
            Sink.from_cli(fake_parser, "foo")
        assert fake_parser.message, "Sink handler does not accept arguments"

    def test_mailbox_no_args(self, fake_parser):
        with pytest.raises(SystemExit):
            Mailbox.from_cli(fake_parser)
        assert fake_parser.message == "The directory for the maildir is required"

    def test_mailbox_too_many_args(self, fake_parser):
        with pytest.raises(SystemExit):
            Mailbox.from_cli(fake_parser, "foo", "bar", "baz")
        assert fake_parser.message == "Too many arguments for Mailbox handler"

    def test_mailbox(self, fake_parser, temp_maildir):
        handler = Mailbox.from_cli(fake_parser, temp_maildir)
        assert isinstance(handler.mailbox, Maildir)
        assert handler.mail_dir == temp_maildir


class TestProxy:
    sender_addr = "anne@example.com"
    receiver_addr = "bart@example.com"

    source_lines = [
        f"From: Anne Person <{sender_addr}>",
        f"To: Bart Person <{receiver_addr}>",
        "Subject: A test",
        "%s",  # Insertion point; see below
        "Testing",
        "",
    ]

    # For "source" we insert an empty string
    source = "\n".join(source_lines) % ""

    # For "expected" we insert X-Peer with yet another template
    expected_template = (
        b"\r\n".join(ln.encode("ascii") for ln in source_lines)
        % b"X-Peer: %s\r\n"
    )

    # There are two controllers and two SMTPd's running here.  The
    # "upstream" one listens on port 9025 and is connected to a "data
    # handler" which captures the messages it receives.  The second -and
    # the one under test here- listens on port 9024 and proxies to the one
    # on port 9025.

    def test_deliver_bytes(self, upstream_controller, proxy_controller, client):
        client.sendmail(self.sender_addr, [self.receiver_addr], self.source)
        upstream = upstream_controller.handler
        proxysess: ServerSession = proxy_controller.smtpd.session
        expected = self.expected_template % proxysess.peer[0].encode("ascii")
        assert upstream.content == expected
        assert upstream.original_content == expected

    def test_deliver_str(self, upstream_controller, proxy_decoding_controller, client):
        client.sendmail(self.sender_addr, [self.receiver_addr], self.source)
        upstream = upstream_controller.handler
        proxysess: ServerSession = proxy_decoding_controller.smtpd.session
        expected = self.expected_template % proxysess.peer[0].encode("ascii")
        assert upstream.content == expected
        assert upstream.original_content == expected


class TestProxyMocked:
    BAD_BART = {"bart@example.com": (500, "Bad Bart")}
    SOURCE = dedent(
        """\
        From: Anne Person <anne@example.com>
        To: Bart Person <bart@example.com>
        Subject: A test

        Testing
        """
    )

    @pytest.fixture
    def patch_smtp_refused(self, mocker):
        mock = mocker.patch("aiosmtpd.handlers.smtplib.SMTP")
        mock().sendmail.side_effect = SMTPRecipientsRefused(self.BAD_BART)

    def test_recipients_refused(
        self, caplog, patch_smtp_refused, proxy_decoding_controller, client
    ):
        logger_name = "mail.debug"
        caplog.set_level(logging.INFO, logger=logger_name)
        client.sendmail("anne@example.com", ["bart@example.com"], self.SOURCE)
        # The log contains information about what happened in the proxy.
        # Ideally it would be the newest 2 log records. However, sometimes asyncio
        # will emit a log entry right afterwards or inbetween causing test fail if we
        # just checked [-1] and [-2]. Therefore we need to scan backwards and simply
        # note the two log entries' relative position
        l1 = l2 = -1
        for l1, rt in enumerate(reversed(caplog.record_tuples)):
            if rt == (logger_name, logging.INFO, "got SMTPRecipientsRefused"):
                break
        else:
            pytest.fail("Can't find first log entry")
        for l2, rt in enumerate(reversed(caplog.record_tuples)):
            if rt == (
                logger_name,
                logging.INFO,
                f"we got some refusals: {self.BAD_BART}",
            ):
                break
        else:
            pytest.fail("Can't find second log entry")
        assert l2 < l1, "Log entries in wrong order"

    @pytest.fixture
    def patch_smtp_oserror(self, mocker):
        mock = mocker.patch("aiosmtpd.handlers.smtplib.SMTP")
        mock().sendmail.side_effect = OSError
        yield

    def test_oserror(
        self, caplog, patch_smtp_oserror, proxy_decoding_controller, client
    ):
        logger_name = "mail.debug"
        caplog.set_level(logging.INFO, logger=logger_name)
        client.sendmail("anne@example.com", ["bart@example.com"], self.SOURCE)
        l1 = -1
        for l1, rt in enumerate(reversed(caplog.record_tuples)):
            if rt == (
                logger_name,
                logging.INFO,
                "we got some refusals: {'bart@example.com': (-1, 'ignore')}",
            ):
                break
        else:
            pytest.fail("Can't find log entry")


class TestHooks:
    @pytest.mark.handler_data(class_=HELOHandler)
    def test_hook_HELO(self, plain_controller, client):
        assert isinstance(plain_controller.handler, HELOHandler)
        resp = client.helo("me")
        assert resp == (250, b"geddy.example.com")

    @pytest.mark.handler_data(class_=EHLOHandler)
    def test_hook_EHLO(self, plain_controller, client):
        assert isinstance(plain_controller.handler, EHLOHandler)
        code, mesg = client.ehlo("me")
        lines = mesg.decode("utf-8").splitlines()
        assert code == 250
        assert lines[-1] == "alex.example.com"

    @pytest.mark.handler_data(class_=MAILHandler)
    def test_hook_MAIL(self, plain_controller, client):
        assert isinstance(plain_controller, ExposingController)
        handler = plain_controller.handler
        assert isinstance(handler, MAILHandler)
        client.ehlo("me")
        resp = client.mail("anne@example.com", ("BODY=7BIT", "SIZE=2000"))
        assert resp == MAILHandler.ReturnCode
        smtpd = plain_controller.smtpd
        assert smtpd.envelope.mail_options == MAILHandler.ReplacementOptions

    @pytest.mark.handler_data(class_=RCPTHandler)
    def test_hook_RCPT(self, plain_controller, client):
        assert isinstance(plain_controller.handler, RCPTHandler)
        client.helo("me")
        with pytest.raises(SMTPRecipientsRefused) as excinfo:
            client.sendmail(
                "anne@example.com",
                ["bart@example.com"],
                dedent(
                    """\
                    From: anne@example.com
                    To: bart@example.com
                    Subject: Test

                    """
                ),
            )
        assert excinfo.value.recipients == {
            "bart@example.com": RCPTHandler.RejectCode,
        }

    @pytest.mark.handler_data(class_=ErroringDataHandler)
    def test_hook_DATA(self, plain_controller, client):
        assert isinstance(plain_controller.handler, ErroringDataHandler)
        with pytest.raises(SMTPDataError) as excinfo:
            client.sendmail(
                "anne@example.com",
                ["bart@example.com"],
                dedent(
                    """\
                    From: anne@example.com
                    To: bart@example.com
                    Subject: Test

                    Yikes
                    """
                ),
            )
        assert excinfo.value.smtp_code == 599
        assert excinfo.value.smtp_error == b"Not today"

    def test_hook_AUTH(self, auth_decoding_controller, client):
        assert isinstance(auth_decoding_controller.handler, AUTHHandler)
        client.ehlo("me")
        resp = client.login("test", "test")
        assert resp == S.S235_AUTH_SUCCESS

    @pytest.mark.handler_data(class_=NoHooksHandler)
    def test_hook_NoHooks(self, plain_controller, client):
        assert isinstance(plain_controller.handler, NoHooksHandler)
        client.helo("me")
        client.mail("anne@example.com")
        client.rcpt(["bart@example.cm"])
        code, _ = client.data(
            dedent(
                """\
                From: anne@example.com
                To: bart@example.com
                Subject: Test
    
                """
            )
        )
        assert code == 250


class TestDeprecation:
    def _process_message_testing(self, controller, client):
        assert isinstance(controller, ExposingController)
        with pytest.warns(DeprecationWarning) as record:
            client.sendmail(
                "anne@example.com",
                ["bart@example.com"],
                dedent(
                    """
                    From: Anne Person <anne@example.com>
                    To: Bart Person <bart@example.com>
                    Subject: A test
    
                    Testing
                    """
                ),
            )
        assert len(record) == 1
        assert (
            record[0].message.args[0]
            == "Use handler.handle_DATA() instead of .process_message()"
        )

    @pytest.mark.handler_data(class_=DeprecatedHandler)
    def test_process_message_Deprecated(self, plain_controller, client):
        """handler.process_message is Deprecated"""
        handler = plain_controller.handler
        assert isinstance(handler, DeprecatedHandler)
        controller = plain_controller
        self._process_message_testing(controller, client)

    @pytest.mark.handler_data(class_=AsyncDeprecatedHandler)
    def test_process_message_AsyncDeprecated(self, plain_controller, client):
        """handler.process_message is Deprecated"""
        handler = plain_controller.handler
        assert isinstance(handler, AsyncDeprecatedHandler)
        controller = plain_controller
        self._process_message_testing(controller, client)

    def test_ehlo_hook_warn(self, deprecated_hook_controller, client):
        """SMTP.ehlo_hook is Deprecated"""
        with pytest.warns(DeprecationWarning) as record:
            client.ehlo("example.com")
        assert len(record) == 1
        assert (
            record[0].message.args[0]
            == "Use handler.handle_EHLO() instead of .ehlo_hook()"
        )

    def test_rset_hook(self, deprecated_hook_controller, client):
        """SMTP.rset_hook is Deprecated"""
        with pytest.warns(DeprecationWarning) as record:
            client.rset()
        assert len(record) == 1
        assert (
            record[0].message.args[0]
            == "Use handler.handle_RSET() instead of .rset_hook()"
        )
