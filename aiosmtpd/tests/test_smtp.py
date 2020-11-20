"""Test the SMTP protocol."""

import time
import pytest
import socket
import asyncio
import logging
import itertools

from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import (
    MISSING,
    Session as SMTPSession,
    SMTP as Server,
    __ident__ as GREETING,
)
from aiosmtpd.testing.helpers import (
    ReceivingHandler,
    reset_connection,
)
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S
from .conftest import ASYNCIO_CATCHUP_DELAY, ExposingController, Global
from base64 import b64encode
from contextlib import suppress
from smtplib import SMTP, SMTPDataError, SMTPResponseException, SMTPServerDisconnected
from textwrap import dedent
from typing import AnyStr, Callable, List, Tuple
from unittest.mock import MagicMock


CRLF = "\r\n"
BCRLF = b"\r\n"
MAIL_LOG = logging.getLogger("mail.log")


# region #### Test Helpers ############################################################


def authenticator(mechanism, login, password) -> bool:
    if login and login.decode() == "goodlogin":
        return True
    else:
        return False


class UndescribableError(Exception):
    def __str__(self):
        raise Exception()


class ErrorSMTP(Server):
    exception_type = ValueError

    async def smtp_HELO(self, hostname):
        raise self.exception_type("test")


# endregion


# region #### Special-Purpose Handlers ################################################


class PeekerHandler:
    _sess: SMTPSession = None
    login: AnyStr = None
    password: AnyStr = None

    def authenticate(self, mechanism: str, login: bytes, password: bytes) -> bool:
        self.login = login
        self.password = password
        return True

    async def handle_MAIL(
        self, server, session: SMTPSession, envelope, address, mail_options
    ):
        self._sess = session
        return S.S250_OK.to_str()

    async def auth_NULL(self, server, args):
        return "NULL_login"

    async def auth_DONT(self, server, args):
        return MISSING


class StoreEnvelopeOnVRFYHandler:
    """Saves envelope for later inspection when handling VRFY."""

    envelope = None

    async def handle_VRFY(self, server, session, envelope, addr):
        self.envelope = envelope
        return S.S250_OK.to_str()


class ErroringHandler:
    error = None
    custom_response = False

    async def handle_DATA(self, server, session, envelope):
        return "499 Could not accept the message"

    async def handle_exception(self, error):
        self.error = error
        if not self.custom_response:
            return "500 ErroringHandler handling error"
        else:
            return "451 Temporary error: ({}) {}".format(
                error.__class__.__name__, str(error)
            )


class ErroringErrorHandler:
    error = None

    async def handle_exception(self, error):
        self.error = error
        raise ValueError("ErroringErrorHandler test")


class UndescribableErrorHandler:
    error = None

    async def handle_exception(self, error):
        self.error = error
        raise UndescribableError()


class SleepingHeloHandler:
    async def handle_HELO(self, server, session, envelope, hostname):
        await asyncio.sleep(0.01)
        session.host_name = hostname
        return "250 {}".format(server.hostname)


# endregion


# region #### Special-Purpose Controllers #############################################


# These are either impractical or impossible to implement using ExposingController

class TimeoutController(ExposingController):
    Delay: float = 1.0

    def factory(self):
        return Server(self.handler, timeout=self.Delay)


class ErrorController(ExposingController):
    def factory(self):
        return ErrorSMTP(self.handler)


class CustomHostnameController(ExposingController):
    custom_name = "custom.localhost"

    def factory(self):
        return Server(self.handler, hostname=self.custom_name)


class CustomIdentController(ExposingController):
    ident: bytes = b"Identifying SMTP v2112"

    def factory(self):
        server = Server(self.handler, ident=self.ident.decode())
        return server


# endregion


# region ##### Fixtures ###############################################################


@pytest.fixture
def transport_resp(mocker) -> Tuple[MagicMock, List]:
    responses = []
    mocked = mocker.Mock()
    mocked.write = responses.append
    #
    yield mocked, responses


@pytest.fixture
def get_protocol(temp_event_loop, transport_resp) -> Callable[..., Server]:
    transport, _ = transport_resp

    def getter(*args, **kwargs) -> Server:
        proto = Server(*args, loop=temp_event_loop, **kwargs)
        proto.connection_made(transport)
        return proto

    yield getter


@pytest.fixture
def suppress_userwarning():
    with pytest.warns(UserWarning):
        yield


# region #### Fixtures: Controllers ##################################################


@pytest.fixture
def auth_peeker_controller(get_controller) -> ExposingController:
    handler = PeekerHandler()
    controller = get_controller(
        handler,
        decode_data=True,
        enable_SMTPUTF8=True,
        auth_require_tls=False,
        auth_callback=handler.authenticate,
        auth_exclude_mechanism=["DONT"],
    )
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def decoding_authnotls_controller(get_handler, get_controller) -> ExposingController:
    handler = get_handler()
    controller = get_controller(
        handler,
        decode_data=True,
        enable_SMTPUTF8=True,
        auth_require_tls=False,
        auth_callback=authenticator,
    )
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    # Some test cases need to .stop() the controller inside themselves
    # in such cases, we must suppress Controller's raise of AssertionError
    # because Controller doesn't like .stop() to be invoked more than once
    with suppress(AssertionError):
        controller.stop()


@pytest.fixture
def error_controller(get_handler) -> ErrorController:
    handler = get_handler()
    controller = ErrorController(handler)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def nodecode_controller(get_handler, get_controller) -> ExposingController:
    handler = get_handler()
    controller = get_controller(handler, decode_data=False)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def require_auth_controller(get_controller) -> ExposingController:
    handler = Sink()
    controller = get_controller(
        handler,
        decode_data=True,
        enable_SMTPUTF8=True,
        auth_require_tls=False,
        auth_callback=authenticator,
        auth_required=True,
    )
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def sized_controller(request, get_controller) -> ExposingController:
    marker = request.node.get_closest_marker("controller_data")
    if marker:
        markerdata = marker.kwargs or {}
    else:
        markerdata = {}
    size = markerdata.get("size", None)
    handler = Sink()
    controller = get_controller(handler, data_size_limit=size)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def strictascii_controller(get_handler, get_controller) -> ExposingController:
    handler = get_handler()
    controller = get_controller(
        handler,
        enable_SMTPUTF8=False,
        decode_data=True,
    )
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


# endregion

# endregion


class _CommonMethods:
    """Contain snippets that keep being performed again and again and again..."""

    def _helo(self, client: SMTP, domain: str = "example.org") -> bytes:
        code, mesg = client.helo(domain)
        assert code == 250
        return mesg

    def _ehlo(self, client: SMTP, domain: str = "example.com") -> bytes:
        code, mesg = client.ehlo(domain)
        assert code == 250
        return mesg

    def _auth_login_noarg(self, client: SMTP):
        self._ehlo(client)
        resp = client.docmd("AUTH LOGIN")
        assert resp == S.S334_AUTH_USERNAME


class TestProtocol:
    def test_honors_mail_delimiters(
        self, temp_event_loop, transport_resp, get_protocol
    ):
        handler = ReceivingHandler()
        protocol = get_protocol(handler)
        data = b"test\r\nmail\rdelimeters\nsaved\r\n"
        protocol.data_received(
            BCRLF.join(
                [
                    b"HELO example.org",
                    b"MAIL FROM: <anne@example.com>",
                    b"RCPT TO: <anne@example.com>",
                    b"DATA",
                    data + b".",
                    b"QUIT\r\n",
                ]
            )
        )
        try:
            temp_event_loop.run_until_complete(protocol._handler_coroutine)
        except asyncio.CancelledError:
            pass
        _, responses = transport_resp
        assert responses[5] == S.S250_OK.to_bytes() + b"\r\n"
        assert len(handler.box) == 1
        assert handler.box[0].content == data

    def test_empty_email(self, temp_event_loop, transport_resp, get_protocol):
        handler = ReceivingHandler()
        protocol = get_protocol(handler)
        protocol.data_received(
            BCRLF.join(
                [
                    b"HELO example.org",
                    b"MAIL FROM: <anne@example.com>",
                    b"RCPT TO: <anne@example.com>",
                    b"DATA",
                    b".",
                    b"QUIT\r\n",
                ]
            )
        )
        try:
            temp_event_loop.run_until_complete(protocol._handler_coroutine)
        except asyncio.CancelledError:
            pass
        _, responses = transport_resp
        assert responses[5] == S.S250_OK.to_bytes() + b"\r\n"
        assert len(handler.box) == 1
        assert handler.box[0].content == b""


@pytest.mark.usefixtures("decoding_authnotls_controller")
class TestSMTP(_CommonMethods):
    valid_mailfrom_addresses = [
        # no space between colon and address
        "anne@example.com",
        "<anne@example.com>",
        # one space between colon and address
        " anne@example.com",
        " <anne@example.com>",
        # multiple spaces between colon and address
        "  anne@example.com",
        "  <anne@example.com>",
        # non alphanums in local part
        "anne.arthur@example.com",
        "anne+promo@example.com",
        "anne-arthur@example.com",
        "anne_arthur@example.com",
        "_@example.com",
        # IP address in domain part
        "anne@127.0.0.1",
        "anne@[127.0.0.1]",
        "anne@[IPv6:2001:db8::1]",
        "anne@[IPv6::1]",
        # email with comments -- obsolete, but still valid
        "anne(comment)@example.com",
        "(comment)anne@example.com",
        "anne@example.com(comment)",
        "anne@machine(comment).  example",  # RFC5322 § A.6.3
        # source route -- RFC5321 § 4.1.2 "MUST BE accepted"
        "<@example.org:anne@example.com>",
        "<@example.net,@example.org:anne@example.com>",
        # strange -- but valid -- addresses
        "anne@mail",
        '""@example.com',
        '<""@example.com>',
        '" "@example.com',
        '"anne..arthur"@example.com',
        "mailhost!anne@example.com",
        "anne%example.org@example.com",
        'much."more\\ unusual"@example.com',
        'very."(),:;<>[]".VERY."very@\\ "very.unusual@strange.example.com',
        # more from RFC3696 § 3
        # 'Abc\\@def@example.com', -- get_addr_spec does not support this
        "Fred\\ Bloggs@example.com",
        "Joe.\\\\Blow@example.com",
        '"Abc@def"@example.com',
        '"Fred Bloggs"@example.com',
        "customer/department=shipping@example.com",
        "$A12345@example.com",
        "!def!xyz%abc@example.com",
    ]

    valid_rcptto_addresses = valid_mailfrom_addresses + [
        # Postmaster -- RFC5321 § 4.1.1.3
        "<Postmaster>",
    ]

    invalid_email_addresses = [
        "<@example.com>",  # no local part
        "a" * 65 + "@example.com",  # local-part > 64 chars
    ]

    @pytest.mark.parametrize("data", [b"\x80FAIL\r\n", b"\x80 FAIL\r\n"])
    def test_binary(self, client, data):
        client.sock.send(data)
        assert client.getreply() == S.S500_BAD_SYNTAX

    def test_helo(self, client):
        resp = client.helo("example.com")
        assert resp == S.S250_FQDN

    def test_close_then_continue(self, client):
        self._helo(client)
        client.close()
        client.connect(*Global.SrvAddr)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S503_HELO_FIRST

    def test_helo_no_hostname(self, client):
        client.local_hostname = ""
        resp = client.helo("")
        assert resp == S.S501_SYNTAX_HELO

    def test_helo_duplicate_ok(self, client):
        self._helo(client, "example.org")
        self._helo(client, "example.com")

    def test_ehlo(self, client):
        code, mesg = client.ehlo("example.com")
        lines = mesg.splitlines()
        assert lines == [
            bytes(socket.getfqdn(), "utf-8"),
            b"SIZE 33554432",
            b"SMTPUTF8",
            b"AUTH LOGIN PLAIN",
            b"HELP",
        ]

    def test_ehlo_duplicate_ok(self, client):
        self._ehlo(client, "example.com")
        self._ehlo(client, "example.org")

    def test_ehlo_no_hostname(self, client):
        client.local_hostname = ""
        resp = client.ehlo("")
        assert resp == S.S501_SYNTAX_EHLO

    def test_helo_then_ehlo(self, client):
        self._helo(client, "example.com")
        self._ehlo(client, "example.org")

    def test_ehlo_then_helo(self, client):
        self._ehlo(client, "example.org")
        self._helo(client, "example.com")

    def test_noop(self, client):
        resp = client.noop()
        assert resp == S.S250_OK

    def test_noop_with_arg(self, decoding_authnotls_controller, client):
        # smtplib.SMTP.noop() doesn't accept args
        resp = client.docmd("NOOP ok")
        assert resp == S.S250_OK

    def test_quit(self, client):
        resp = client.quit()
        assert resp == S.S221_BYE

    def test_quit_with_args(self, client):
        resp = client.docmd("QUIT oops")
        assert resp == S.S501_SYNTAX_QUIT

    def test_help(self, client):
        resp = client.docmd("HELP")
        assert resp == S.S250_SUPPCMD_NOTLS

    @pytest.mark.parametrize(
        "command",
        [
            "HELO",
            "EHLO",
            "MAIL",
            "RCPT",
            "DATA",
            "RSET",
            "NOOP",
            "QUIT",
            "VRFY",
            "AUTH",
        ],
    )
    def test_help_command(self, client, command):
        resp = client.docmd(f"HELP {command}")
        syntax = getattr(S, f"S250_SYNTAX_{command}")
        assert resp == syntax

    @pytest.mark.parametrize(
        "command",
        [
            "MAIL",
            "RCPT",
        ],
    )
    def test_help_command_esmtp(self, client, command):
        self._ehlo(client)
        resp = client.docmd(f"HELP {command}")
        syntax = getattr(S, f"S250_SYNTAX_{command}_E")
        assert resp == syntax

    def test_help_bad_arg(self, client):
        resp = client.docmd("HELP me!")
        assert resp == S.S501_SUPPCMD_NOTLS

    def test_expn(self, client):
        resp = client.expn("anne@example.com")
        assert resp == S.S502_EXPN_NOTIMPL

    @pytest.mark.parametrize(
        "command",
        ["MAIL FROM: <anne@example.com>", "RCPT TO: <anne@example.com>", "DATA"],
        ids=lambda x: x.split()[0],
    )
    def test_no_helo(self, client, command):
        resp = client.docmd(command)
        assert resp == S.S503_HELO_FIRST

    @pytest.mark.parametrize(
        "address",
        valid_mailfrom_addresses,
        ids=itertools.count(),
    )
    def test_mail_valid_addresses(self, client, address):
        self._ehlo(client)
        resp = client.docmd(f"MAIL FROM:{address}")
        assert resp == S.S250_OK

    @pytest.mark.parametrize(
        "command",
        [
            "MAIL",
            "MAIL <anne@example.com>",
            "MAIL FROM:",
            "MAIL FROM: <anne@example.com> SIZE=10000",
            "MAIL FROM: Anne <anne@example.com>",
        ],
        ids=["noarg", "nofrom", "noaddr", "params_noesmtp", "malformed"],
    )
    def test_mail_smtp_errsyntax(self, client, command):
        self._helo(client)
        resp = client.docmd(command)
        assert resp == S.S501_SYNTAX_MAIL

    @pytest.mark.parametrize(
        "param",
        [
            "SIZE=10000",
            " SIZE=10000",
            "SIZE=10000 ",
        ],
        ids=["norm", "extralead", "extratail"],
    )
    def test_mail_params_esmtp(self, client, param):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com> " + param)
        assert resp == S.S250_OK

    def test_mail_from_twice(self, client):
        self._helo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S503_MAIL_NESTED

    @pytest.mark.parametrize(
        "command",
        [
            "MAIL FROM: <anne@example.com> SIZE 10000",
            "MAIL FROM: <anne@example.com> SIZE",
            "MAIL FROM: <anne@example.com> #$%=!@#",
            "MAIL FROM: <anne@example.com> SIZE = 10000",
        ],
        ids=["malformed", "missing", "badsyntax", "space"],
    )
    def test_mail_esmtp_errsyntax(self, client, command):
        self._ehlo(client)
        resp = client.docmd(command)
        assert resp == S.S501_SYNTAX_MAIL_E

    def test_mail_esmtp_params_unrecognized(self, client):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com> FOO=BAR")
        assert resp == S.S555_MAIL_PARAMS_UNRECOG

    # This was a bug, and it's already fixed since 3.6 (see bpo below)
    # Since we now only support >=3.6, there is no point emulating this bug.
    # Rather, we test that bug is fixed.
    #
    # # Test the workaround http://bugs.python.org/issue27931
    # @patch('email._header_value_parser.AngleAddr.addr_spec',
    #        side_effect=IndexError, new_callable=PropertyMock)
    # def test_mail_fail_parse_email(self, addr_spec):
    #     self.client.helo('example.com')
    #     self.client.assert_cmd_resp(
    #         'MAIL FROM: <""@example.com>',
    #         (501, b'Syntax: MAIL FROM: <address>')
    #     )
    def test_27931fix_smtp(self, client):
        self._helo(client)
        resp = client.docmd('MAIL FROM: <""@example.com>')
        assert resp == S.S250_OK
        resp = client.docmd('RCPT TO: <""@example.org>')
        assert resp == S.S250_OK

    @pytest.mark.parametrize(
        "address",
        invalid_email_addresses,
        ids=itertools.count(),
    )
    def test_mail_smtp_malformed(self, client, address):
        self._helo(client)
        resp = client.docmd(f"MAIL FROM: {address}")
        assert resp == S.S553_MALFORMED

    def test_rcpt_no_mail(self, client):
        self._helo(client)
        resp = client.docmd("RCPT TO: <anne@example.com>")
        assert resp == S.S503_MAIL_NEEDED

    @pytest.mark.parametrize(
        "command",
        [
            "RCPT",
            "RCPT <anne@example.com>",
            "RCPT TO:",
            "RCPT TO: <bart@example.com> SIZE=1000",
            "RCPT TO: bart <bart@example.com>",
        ],
        ids=["noarg", "noto", "noaddr", "params", "malformed"],
    )
    def test_rcpt_smtp_errsyntax(self, client, command):
        self._helo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd(command)
        assert resp == S.S501_SYNTAX_RCPT

    @pytest.mark.parametrize(
        "command",
        [
            "RCPT",
            "RCPT <anne@example.com>",
            "RCPT TO:",
            "RCPT TO: <bart@example.com> #$%=!@#",
            "RCPT TO: bart <bart@example.com>",
        ],
        ids=["noarg", "noto", "noaddr", "badparams", "malformed"],
    )
    def test_rcpt_esmtp_errsyntax(self, client, command):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd(command)
        assert resp == S.S501_SYNTAX_RCPT_E

    def test_rcpt_unknown_params(self, client):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd("RCPT TO: <bart@example.com> FOOBAR")
        assert resp == S.S555_RCPT_PARAMS_UNRECOG

    @pytest.mark.parametrize("address", valid_rcptto_addresses, ids=itertools.count())
    def test_rcpt_valid(self, client, address):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd(f"RCPT TO: {address}")
        assert resp == S.S250_OK

    @pytest.mark.parametrize("address", invalid_email_addresses, ids=itertools.count())
    def test_rcpt_malformed(self, client, address):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd(f"RCPT TO: {address}")
        assert resp == S.S553_MALFORMED

    # This was a bug, and it's already fixed since 3.6 (see bpo below)
    # Since we now only support >=3.6, there is no point emulating this bug
    # Rather, we test that bug is fixed.
    #
    # # Test the workaround http://bugs.python.org/issue27931
    # @patch('email._header_value_parser.AngleAddr.addr_spec',
    #        new_callable=PropertyMock)
    # def test_rcpt_fail_parse_email(self, addr_spec):
    #     self.client.assert_ehlo_ok('example.com')
    #     self.client.assert_cmd_ok('MAIL FROM: <anne@example.com>')
    #     addr_spec.side_effect = IndexError
    #     self.client.assert_cmd_resp(
    #         'RCPT TO: <""@example.com>',
    #         (501, b'Syntax: RCPT TO: <address> [SP <mail-parameters>]')
    #     )
    def test_27931fix_esmtp(self, client):
        self._ehlo(client)
        resp = client.docmd('MAIL FROM: <""@example.com> SIZE=28113')
        assert resp == S.S250_OK
        resp = client.docmd('RCPT TO: <""@example.org>')
        assert resp == S.S250_OK

    @pytest.mark.parametrize("address", invalid_email_addresses, ids=itertools.count())
    def test_mail_esmtp_malformed(self, client, address):
        self._ehlo(client)
        resp = client.docmd(f"MAIL FROM: {address} SIZE=28113")
        assert resp == S.S553_MALFORMED

    def test_rset(self, client):
        resp = client.rset()
        assert resp == S.S250_OK

    def test_rset_with_arg(self, client):
        resp = client.docmd("RSET FOO")
        assert resp == S.S501_SYNTAX_RSET

    def test_vrfy(self, client):
        resp = client.docmd("VRFY <anne@example.com>")
        assert resp == S.S252_CANNOT_VRFY

    def test_vrfy_no_arg(self, client):
        resp = client.docmd("VRFY")
        assert resp == S.S501_SYNTAX_VRFY

    def test_vrfy_not_address(self, client):
        resp = client.docmd("VRFY @@")
        assert resp == S.S502_VRFY_COULDNT(b"@@")

    def test_data_no_rcpt(self, client):
        self._helo(client)
        resp = client.docmd("DATA")
        assert resp == S.S503_RCPT_NEEDED

    def test_data_354(self, decoding_authnotls_controller, client):
        self._helo(client)
        resp = client.docmd("MAIL FROM: <alice@example.org>")
        assert resp == S.S250_OK
        resp = client.docmd("RCPT TO: <bob@example.org>")
        assert resp == S.S250_OK
        # Note: We NEED to manually stop the controller if we must abort while
        # in DATA phase. For reasons unclear, if we don't do that we'll hang
        # the test case should the assertion fail
        try:
            resp = client.docmd("DATA")
            assert resp == S.S354_DATA_ENDWITH
        finally:
            decoding_authnotls_controller.stop()

    def test_data_invalid_params(self, client):
        self._helo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd("RCPT TO: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd("DATA FOOBAR")
        assert resp == S.S501_SYNTAX_DATA

    def test_empty_command(self, client):
        resp = client.docmd("")
        assert resp == S.S500_BAD_SYNTAX

    def test_too_long_command(self, client):
        resp = client.docmd("a" * 513)
        assert resp == S.S500_LINE_TOO_LONG

    def test_unknown_command(self, client):
        resp = client.docmd("FOOBAR")
        assert resp == S.S500_CMD_UNRECOG(b"FOOBAR")


class TestSMTPNonDecoding(_CommonMethods):
    def test_mail_invalid_body_param(self, nodecode_controller, client):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com> BODY=FOOBAR")
        assert resp == S.S501_MAIL_BODY


@pytest.mark.usefixtures("decoding_authnotls_controller")
class TestSMTPAuth(_CommonMethods):
    def test_auth_no_ehlo(self, client):
        resp = client.docmd("AUTH")
        assert resp == S.S503_EHLO_FIRST

    def test_auth_helo(self, client):
        self._helo(client)
        resp = client.docmd("AUTH")
        assert resp == S.S500_AUTH_UNRECOG

    def test_auth_too_many_values(self, client):
        self._ehlo(client)
        resp = client.docmd("AUTH PLAIN NONE NONE")
        assert resp == S.S501_TOO_MANY

    def test_auth_not_enough_values(self, client):
        self._ehlo(client)
        resp = client.docmd("AUTH")
        assert resp == S.S501_TOO_FEW

    @pytest.mark.parametrize("mechanism", ["GSSAPI", "DIGEST-MD5", "MD5", "CRAM-MD5"])
    def test_auth_not_supported_mechanisms(self, client, mechanism):
        self._ehlo(client)
        resp = client.docmd("AUTH " + mechanism)
        assert resp == S.S504_AUTH_UNRECOG

    def test_auth_success(self, client):
        self._ehlo(client)
        resp = client.login("goodlogin", "goodpasswd", initial_response_ok=False)
        assert resp == S.S235_AUTH_SUCCESS

    def test_auth_good_credentials(self, client):
        self._ehlo(client)
        resp = client.docmd(
            "AUTH PLAIN " + b64encode(b"\0goodlogin\0goodpasswd").decode()
        )
        assert resp == S.S235_AUTH_SUCCESS

    def test_auth_already_authenticated(self, client):
        self._ehlo(client)
        resp = client.docmd(
            "AUTH PLAIN " + b64encode(b"\0goodlogin\0goodpasswd").decode()
        )
        assert resp == S.S235_AUTH_SUCCESS
        resp = client.docmd("AUTH")
        assert resp == S.S503_ALREADY_AUTH
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK

    def test_auth_bad_base64_encoding(self, client):
        self._ehlo(client)
        resp = client.docmd("AUTH PLAIN not-b64")
        assert resp == S.S501_AUTH_NOTB64

    def test_auth_bad_base64_length(self, client):
        self._ehlo(client)
        resp = client.docmd("AUTH PLAIN " + b64encode(b"\0onlylogin").decode())
        assert resp == S.S501_AUTH_CANTSPLIT

    def test_auth_bad_credentials(self, client):
        self._ehlo(client)
        resp = client.docmd(
            "AUTH PLAIN " + b64encode(b"\0badlogin\0badpasswd").decode()
        )
        assert resp == S.S535_AUTH_INVALID

    def _auth_two_steps(self, client):
        self._ehlo(client)
        resp = client.docmd("AUTH PLAIN")
        assert resp == S.S334_AUTH_EMPTYPROMPT

    def test_auth_two_steps_good_credentials(self, client):
        self._auth_two_steps(client)
        resp = client.docmd(b64encode(b"\0goodlogin\0goodpasswd").decode())
        assert resp == S.S235_AUTH_SUCCESS

    def test_auth_two_steps_bad_credentials(self, client):
        self._auth_two_steps(client)
        resp = client.docmd(b64encode(b"\0badlogin\0badpasswd").decode())
        assert resp == S.S535_AUTH_INVALID

    def test_auth_two_steps_abort(self, client):
        self._auth_two_steps(client)
        resp = client.docmd("*")
        assert resp == S.S501_AUTH_ABORTED

    def test_auth_two_steps_bad_base64_encoding(self, client):
        self._auth_two_steps(client)
        resp = client.docmd("ab@%")
        assert resp == S.S501_AUTH_NOTB64

    def test_auth_no_credentials(self, client):
        self._ehlo(client)
        resp = client.docmd("AUTH PLAIN =")
        assert resp == S.S535_AUTH_INVALID

    def test_auth_two_steps_no_credentials(self, client):
        self._auth_two_steps(client)
        resp = client.docmd("=")
        assert resp == S.S535_AUTH_INVALID

    def test_auth_login_no_credentials(self, client):
        self._auth_login_noarg(client)
        resp = client.docmd("=")
        assert resp == S.S334_AUTH_PASSWORD
        resp = client.docmd("=")
        assert resp == S.S535_AUTH_INVALID


@pytest.mark.usefixtures("auth_peeker_controller")
class TestSMTPAuthMechanisms(_CommonMethods):
    def test_ehlo(self, client):
        code, mesg = client.ehlo("example.com")
        assert code == 250
        lines = mesg.splitlines()
        assert lines == [
            bytes(socket.getfqdn(), "utf-8"),
            b"SIZE 33554432",
            b"SMTPUTF8",
            b"AUTH LOGIN NULL PLAIN",
            b"HELP",
        ]

    def test_auth_custom_mechanism(self, client):
        self._ehlo(client)
        resp = client.docmd("AUTH NULL")
        assert resp == S.S235_AUTH_SUCCESS

    def test_auth_plain_null_credential(self, auth_peeker_controller, client):
        self._ehlo(client)
        resp = client.docmd("AUTH PLAIN")
        assert resp == S.S334_AUTH_EMPTYPROMPT
        resp = client.docmd("=")
        assert resp == S.S235_AUTH_SUCCESS
        peeker = auth_peeker_controller.handler
        assert isinstance(peeker, PeekerHandler)
        assert peeker.login is None
        assert peeker.password is None
        resp = client.mail("alice@example.com")
        assert resp == S.S250_OK
        assert peeker._sess.login_data == b""

    def test_auth_login_null_credential(self, auth_peeker_controller, client):
        self._auth_login_noarg(client)
        resp = client.docmd("=")
        assert resp == S.S334_AUTH_PASSWORD
        resp = client.docmd("=")
        assert resp == S.S235_AUTH_SUCCESS
        peeker = auth_peeker_controller.handler
        assert isinstance(peeker, PeekerHandler)
        assert peeker.login is None
        assert peeker.password is None
        resp = client.mail("alice@example.com")
        assert resp == S.S250_OK
        assert peeker._sess.login_data == b""

    def test_auth_login_abort_login(self, client):
        self._auth_login_noarg(client)
        resp = client.docmd("*")
        assert resp == S.S501_AUTH_ABORTED

    def test_auth_login_abort_password(self, client):
        # self.auth_peeker.return_val = False
        self._auth_login_noarg(client)
        resp = client.docmd("=")
        assert resp == S.S334_AUTH_PASSWORD
        resp = client.docmd("*")
        assert resp == S.S501_AUTH_ABORTED

    def test_auth_disabled_mechanism(self, client):
        self._ehlo(client)
        resp = client.docmd("AUTH DONT")
        assert resp == S.S504_AUTH_UNRECOG


@pytest.mark.usefixtures("require_auth_controller", "suppress_userwarning")
class TestSMTPRequiredAuthentication(_CommonMethods):
    def _login(self, client: SMTP):
        self._ehlo(client)
        resp = client.login("goodlogin", "goodpasswd")
        assert resp == S.S235_AUTH_SUCCESS

    def test_help_unauthenticated(self, client):
        resp = client.docmd("HELP")
        assert resp == S.S530_AUTH_REQUIRED

    def test_vrfy_unauthenticated(self, client):
        resp = client.docmd("VRFY <anne@example.com>")
        assert resp == S.S530_AUTH_REQUIRED

    def test_mail_unauthenticated(self, client):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S530_AUTH_REQUIRED

    def test_rcpt_unauthenticated(self, client):
        self._ehlo(client)
        resp = client.docmd("RCPT TO: <anne@example.com>")
        assert resp == S.S530_AUTH_REQUIRED

    def test_data_unauthenticated(self, client):
        self._ehlo(client)
        resp = client.docmd("DATA")
        assert resp == S.S530_AUTH_REQUIRED

    def test_help_authenticated(self, client):
        self._login(client)
        resp = client.docmd("HELP")
        assert resp == S.S250_SUPPCMD_NOTLS

    def test_vrfy_authenticated(self, client):
        self._login(client)
        resp = client.docmd("VRFY <anne@example.com>")
        assert resp == S.S252_CANNOT_VRFY

    def test_mail_authenticated(self, client):
        self._login(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp, S.S250_OK

    def test_rcpt_nomail_authenticated(self, client):
        self._login(client)
        resp = client.docmd("RCPT TO: <anne@example.com>")
        assert resp == S.S503_MAIL_NEEDED

    def test_data_norcpt_authenticated(self, client):
        self._login(client)
        resp = client.docmd("DATA")
        assert resp == S.S503_RCPT_NEEDED


class TestResetCommands:
    """Test that sender and recipients are reset on RSET, HELO, and EHLO.

    The tests below issue each command twice with different addresses and
    verify that mail_from and rcpt_tos have been replacecd.
    """

    expected_envelope_data = [
        # Pre-RSET/HELO/EHLO envelope data.
        dict(
            mail_from="anne@example.com",
            rcpt_tos=["bart@example.com", "cate@example.com"],
        ),
        dict(
            mail_from="dave@example.com",
            rcpt_tos=["elle@example.com", "fred@example.com"],
        ),
    ]

    def _send_envelope_data(self, client: SMTP, mail_from: str, rcpt_tos: List[str]):
        client.mail(mail_from)
        for rcpt in rcpt_tos:
            client.rcpt(rcpt)

    @pytest.mark.handler_data(class_=StoreEnvelopeOnVRFYHandler)
    def test_helo(self, decoding_authnotls_controller, client):
        handler = decoding_authnotls_controller.handler
        assert isinstance(handler, StoreEnvelopeOnVRFYHandler)
        # Each time through the loop, the HELO will reset the envelope.
        for data in self.expected_envelope_data:
            client.helo("example.com")
            # Save the envelope in the handler.
            client.vrfy("zuzu@example.com")
            assert handler.envelope.mail_from is None
            assert len(handler.envelope.rcpt_tos) == 0
            self._send_envelope_data(client, **data)
            client.vrfy("zuzu@example.com")
            assert handler.envelope.mail_from == data["mail_from"]
            assert handler.envelope.rcpt_tos == data["rcpt_tos"]

    @pytest.mark.handler_data(class_=StoreEnvelopeOnVRFYHandler)
    def test_ehlo(self, decoding_authnotls_controller, client):
        handler = decoding_authnotls_controller.handler
        assert isinstance(handler, StoreEnvelopeOnVRFYHandler)
        # Each time through the loop, the EHLO will reset the envelope.
        for data in self.expected_envelope_data:
            client.ehlo("example.com")
            # Save the envelope in the handler.
            client.vrfy("zuzu@example.com")
            assert handler.envelope.mail_from is None
            assert len(handler.envelope.rcpt_tos) == 0
            self._send_envelope_data(client, **data)
            client.vrfy("zuzu@example.com")
            assert handler.envelope.mail_from == data["mail_from"]
            assert handler.envelope.rcpt_tos == data["rcpt_tos"]

    @pytest.mark.handler_data(class_=StoreEnvelopeOnVRFYHandler)
    def test_rset(self, decoding_authnotls_controller, client):
        handler = decoding_authnotls_controller.handler
        assert isinstance(handler, StoreEnvelopeOnVRFYHandler)
        client.helo("example.com")
        # Each time through the loop, the RSET will reset the envelope.
        for data in self.expected_envelope_data:
            self._send_envelope_data(client, **data)
            # Save the envelope in the handler.
            client.vrfy("zuzu@example.com")
            assert handler.envelope.mail_from == data["mail_from"]
            assert handler.envelope.rcpt_tos == data["rcpt_tos"]
            # Reset the envelope explicitly.
            client.rset()
            client.vrfy("zuzu@example.com")
            assert handler.envelope.mail_from is None
            assert len(handler.envelope.rcpt_tos) == 0


class TestSMTPWithController(_CommonMethods):
    @pytest.mark.controller_data(size=9999)
    def test_mail_with_size_too_large(self, sized_controller, client):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com> SIZE=10000")
        assert resp == S.S552_EXCEED_SIZE

    @pytest.mark.handler_data(class_=ReceivingHandler)
    def test_mail_with_compatible_smtputf8(self, plain_controller, client):
        receiving_handler = plain_controller.handler
        assert isinstance(receiving_handler, ReceivingHandler)
        sender = "anne\xCB@example.com"
        recipient = "bart\xCB@example.com"
        self._ehlo(client)
        client.send(f"MAIL FROM: <{sender}> SMTPUTF8\r\n".encode("utf-8"))
        assert client.getreply() == S.S250_OK
        client.send(f"RCPT TO: <{recipient}>\r\n".encode("utf-8"))
        assert client.getreply() == S.S250_OK
        resp = client.data("")
        assert resp == S.S250_OK
        assert receiving_handler.box[0].mail_from == sender
        assert receiving_handler.box[0].rcpt_tos == [recipient]

    def test_mail_with_unrequited_smtputf8(self, plain_controller, client):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK

    def test_mail_with_incompatible_smtputf8(self, plain_controller, client):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com> SMTPUTF8=YES")
        assert resp == S.S501_SMTPUTF8_NOARG

    def test_mail_invalid_body(self, plain_controller, client):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com> BODY 9BIT")
        assert resp == S.S501_MAIL_BODY

    @pytest.mark.controller_data(size=None)
    def test_esmtp_no_size_limit(self, sized_controller, client):
        code, mesg = client.ehlo("example.com")
        for ln in mesg.splitlines():
            assert not ln.startswith(b"SIZE")

    @pytest.mark.handler_data(class_=ErroringHandler)
    def test_process_message_error(self, error_controller, client):
        self._ehlo(client)
        with pytest.raises(SMTPDataError) as excinfo:
            client.sendmail(
                "anne@example.com",
                ["bart@example.com"],
                dedent(
                    """\
                    From: anne@example.com
                    To: bart@example.com
                    Subjebgct: A test
                    
                    Testing
                """
                ),
            )
        assert excinfo.value.smtp_code == 499
        assert excinfo.value.smtp_error == b"Could not accept the message"

    @pytest.mark.controller_data(size=100)
    def test_too_long_message_body(self, sized_controller, client):
        self._helo(client)
        mail = "\r\n".join(["z" * 20] * 10)
        with pytest.raises(SMTPResponseException) as excinfo:
            client.sendmail("anne@example.com", ["bart@example.com"], mail)
        assert excinfo.value.smtp_code == 552
        assert excinfo.value.smtp_error == S.S552_TOO_MUCH.mesg

    @pytest.mark.handler_data(class_=ReceivingHandler)
    def test_dots_escaped(self, decoding_authnotls_controller, client):
        receiving_handler = decoding_authnotls_controller.handler
        assert isinstance(receiving_handler, ReceivingHandler)
        self._helo(client)
        mail = CRLF.join(["Test", ".", "mail"])
        client.sendmail("anne@example.com", ["bart@example.com"], mail)
        assert len(receiving_handler.box) == 1
        assert receiving_handler.box[0].content == mail + CRLF

    @pytest.mark.handler_data(class_=ErroringHandler)
    def test_unexpected_errors(self, error_controller, client):
        handler = error_controller.handler
        resp = client.helo("example.com")
        assert resp == (500, b"ErroringHandler handling error")
        exception_type = ErrorSMTP.exception_type
        assert isinstance(handler.error, exception_type)

    def test_unexpected_errors_unhandled(self, error_controller, client):
        resp = client.helo("example.com")
        exception_type = ErrorSMTP.exception_type
        exception_nameb = exception_type.__name__.encode("ascii")
        assert resp == (500, b"Error: (" + exception_nameb + b") test")

    @pytest.mark.handler_data(class_=ErroringHandler)
    def test_unexpected_errors_custom_response(self, error_controller, client):
        erroring_handler = error_controller.handler
        erroring_handler.custom_response = True
        resp = client.helo("example.com")
        exception_type = ErrorSMTP.exception_type
        assert isinstance(erroring_handler.error, exception_type)
        exception_nameb = exception_type.__name__.encode("ascii")
        assert resp == (451, b"Temporary error: (" + exception_nameb + b") test")

    @pytest.mark.handler_data(class_=ErroringErrorHandler)
    def test_exception_handler_exception(self, error_controller, client):
        handler = error_controller.handler
        resp = client.helo("example.com")
        assert resp == (500, b"Error: (ValueError) ErroringErrorHandler test")
        exception_type = ErrorSMTP.exception_type
        assert isinstance(handler.error, exception_type)

    @pytest.mark.handler_data(class_=UndescribableErrorHandler)
    def test_exception_handler_undescribable(self, error_controller, client):
        handler = error_controller.handler
        resp = client.helo("example.com")
        assert resp == (500, b"Error: Cannot describe error")
        exception_type = ErrorSMTP.exception_type
        assert isinstance(handler.error, exception_type)

    @pytest.mark.handler_data(class_=ReceivingHandler)
    def test_bad_encodings(self, decoding_authnotls_controller, client):
        handler: ReceivingHandler = decoding_authnotls_controller.handler
        self._helo(client)
        mail_from = b"anne\xFF@example.com"
        mail_to = b"bart\xFF@example.com"
        self._ehlo(client, "test")
        client.send(b"MAIL FROM:" + mail_from + b"\r\n")
        assert client.getreply() == S.S250_OK
        client.send(b"RCPT TO:" + mail_to + b"\r\n")
        assert client.getreply() == S.S250_OK
        client.data("Test mail")
        assert len(handler.box) == 1
        envelope = handler.box[0]
        mail_from2 = envelope.mail_from.encode("utf-8", errors="surrogateescape")
        assert mail_from2 == mail_from
        mail_to2 = envelope.rcpt_tos[0].encode("utf-8", errors="surrogateescape")
        assert mail_to2 == mail_to


class TestCustomization(_CommonMethods):
    @pytest.mark.controller_data(class_=CustomHostnameController)
    def test_custom_hostname(self, plain_controller, client):
        code, mesg = client.helo("example.com")
        assert code == 250
        assert mesg == CustomHostnameController.custom_name.encode("ascii")

    def test_default_greeting(self, plain_controller, client):
        controller = plain_controller
        code, mesg = client.connect(controller.hostname, controller.port)
        assert code == 220
        # The hostname prefix is unpredictable
        assert mesg.endswith(bytes(GREETING, "utf-8"))

    @pytest.mark.controller_data(class_=CustomIdentController)
    def test_custom_greeting(self, plain_controller, client):
        controller = plain_controller
        code, mesg = client.connect(controller.hostname, controller.port)
        assert code == 220
        # The hostname prefix is unpredictable.
        assert mesg.endswith(CustomIdentController.ident)


class TestClientCrash(_CommonMethods):

    # test_connection_reset_* test cases seem to be testing smtplib.SMTP behavior
    # instead of aiosmtpd.smtp.SMTP behavior. Maybe we can remove these?

    def test_connection_reset_during_DATA(self, plain_controller, client):
        self._helo(client)
        client.docmd("MAIL FROM: <anne@example.com>")
        client.docmd("RCPT TO: <bart@example.com>")
        client.docmd("DATA")
        # Start sending the DATA but reset the connection before that
        # completes, i.e. before the .\r\n
        client.send(b"From: <anne@example.com>")
        reset_connection(client)
        # The connection should be disconnected, so trying to do another
        # command from here will give us an exception.  In GH#62, the
        # server just hung.
        with pytest.raises(SMTPServerDisconnected):
            client.noop()

    def test_connection_reset_during_command(self, plain_controller, client):
        self._helo(client)
        # Start sending a command but reset the connection before that
        # completes, i.e. before the \r\n
        client.send("MAIL FROM: <anne")
        reset_connection(client)
        # The connection should be disconnected, so trying to do another
        # command from here will give us an exception.  In GH#62, the
        # server just hung.
        with pytest.raises(SMTPServerDisconnected):
            client.noop()

    # test_connreset_* test cases below _actually_ test aiosmtpd.smtp.SMTP behavior
    # A bit more invasive than I like, but can't be helped.

    def test_connreset_during_DATA(self, mocker, plain_controller, client):
        # Trigger factory() to produce the smtpd server
        self._helo(client)
        # Monkeypatching
        smtpd: Server = plain_controller.smtpd
        spy: MagicMock = mocker.spy(smtpd._writer, "close")
        # Do some stuff
        client.docmd("MAIL FROM: <anne@example.com>")
        client.docmd("RCPT TO: <bart@example.com>")
        # Entering portion of code where hang is possible (upon assertion fail), so
        # we must wrap with "try..finally". See pytest-dev/pytest#7989
        try:
            resp = client.docmd("DATA")
            assert resp == S.S354_DATA_ENDWITH
            # Start sending the DATA but reset the connection before that
            # completes, i.e. before the .\r\n
            client.send(b"From: <anne@example.com>")
            reset_connection(client)
            time.sleep(ASYNCIO_CATCHUP_DELAY)
            # Apparently within that delay, ._writer.close() invoked several times
            # That is okay; we just want to ensure that it's invoked at least once.
            assert spy.call_count > 0
        finally:
            plain_controller.stop()

    def test_connreset_during_command(self, mocker, plain_controller, client):
        # Trigger factory() to produce the smtpd server
        self._helo(client)
        time.sleep(ASYNCIO_CATCHUP_DELAY)
        smtpd: Server = plain_controller.smtpd
        spy: MagicMock = mocker.spy(smtpd._writer, "close")
        # Start sending a command but reset the connection before that
        # completes, i.e. before the \r\n
        client.send("MAIL FROM: <anne")
        reset_connection(client)
        time.sleep(ASYNCIO_CATCHUP_DELAY)
        # Should be called at least once. (In practice, almost certainly just once.)
        assert spy.call_count > 0

    def test_close_in_command(self, plain_controller, client):
        #
        # What exactly are we testing in this test case, actually?
        #
        # Don't include the CRLF.
        client.send("FOO")
        client.close()

    def test_connclose_in_command(self, mocker, plain_controller, client):
        # Don't include the CRLF.
        client.send("FOO")
        client.close()
        time.sleep(ASYNCIO_CATCHUP_DELAY)
        # At this point, smtpd's StreamWriter hasn't been initialized. Prolly since
        # the call is self._reader.readline() and we abort before CRLF is sent.
        # That is why we don't need to 'spy' on writer.close()
        writer = plain_controller.smtpd._writer
        # transport.is_closing() == True if transport is in the process of closing,
        # and still == True if transport is closed.
        assert writer.transport.is_closing()

    def test_connclose_in_command_2(self, mocker, plain_controller, client):
        self._helo(client)
        time.sleep(ASYNCIO_CATCHUP_DELAY)
        smtpd: Server = plain_controller.smtpd
        writer = smtpd._writer
        spy: MagicMock = mocker.spy(writer, "close")
        # Don't include the CRLF.
        client.send("FOO")
        client.close()
        time.sleep(ASYNCIO_CATCHUP_DELAY)
        # Check that smtpd._writer.close() invoked at least once
        assert spy.call_count > 0
        # transport.is_closing() == True if transport is in the process of closing,
        # and still == True if transport is closed.
        assert writer.transport.is_closing()

    def test_close_in_data(self, mocker, plain_controller, client):
        #
        # What exactly are we testing in this test case, actually?
        #
        resp = client.helo("example.com")
        assert resp == S.S250_FQDN
        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd("RCPT TO: <bart@example.com>")
        assert resp == S.S250_OK
        # Entering portion of code where hang is possible (upon assertion fail), so
        # we must wrap with "try..finally". See pytest-dev/pytest#7989
        try:
            resp = client.docmd("DATA")
            assert resp == S.S354_DATA_ENDWITH
            # Don't include the CRLF.
            client.send("FOO")
            client.close()
        finally:
            plain_controller.stop()

    def test_connclose_in_data(self, mocker, plain_controller, client):
        self._helo(client)
        time.sleep(ASYNCIO_CATCHUP_DELAY)
        smtpd: Server = plain_controller.smtpd
        writer = smtpd._writer
        spy: MagicMock = mocker.spy(writer, "close")

        resp = client.docmd("MAIL FROM: <anne@example.com>")
        assert resp == S.S250_OK
        resp = client.docmd("RCPT TO: <bart@example.com>")
        assert resp == S.S250_OK
        # Entering portion of code where hang is possible (upon assertion fail), so
        # we must wrap with "try..finally". See pytest-dev/pytest#7989
        try:
            resp = client.docmd("DATA")
            assert resp == S.S354_DATA_ENDWITH
            # Don't include the CRLF.
            client.send("FOO")
            client.close()
            time.sleep(ASYNCIO_CATCHUP_DELAY)
            # Check that smtpd._writer.close() invoked at least once
            assert spy.call_count > 0
            # transport.is_closing() == True if transport is in the process of closing,
            # and still == True if transport is closed.
            assert writer.transport.is_closing()
        finally:
            plain_controller.stop()

    def test_sockclose_after_helo(self, mocker, plain_controller, client):
        client.send("HELO example.com\r\n")
        time.sleep(ASYNCIO_CATCHUP_DELAY)
        smtpd: Server = plain_controller.smtpd
        writer = smtpd._writer
        spy: MagicMock = mocker.spy(writer, "close")

        client.sock.shutdown(socket.SHUT_WR)
        time.sleep(ASYNCIO_CATCHUP_DELAY)
        # Check that smtpd._writer.close() invoked at least once
        assert spy.call_count > 0
        # transport.is_closing() == True if transport is in the process of closing,
        # and still == True if transport is closed.
        assert writer.transport.is_closing()


@pytest.mark.usefixtures("strictascii_controller")
class TestStrictASCII(_CommonMethods):
    def test_ehlo(self, client):
        blines = self._ehlo(client)
        assert b"SMTPUTF8" not in blines

    def test_bad_encoded_param(self, client):
        self._ehlo(client)
        client.send(b"MAIL FROM: <anne\xFF@example.com>\r\n")
        assert client.getreply() == S.S500_STRICT_ASCII

    def test_mail_param(self, client):
        self._ehlo(client)
        resp = client.docmd("MAIL FROM: <anne@example.com> SMTPUTF8")
        assert resp == S.S501_SMTPUTF8_DISABLED

    def test_data(self, client):
        self._ehlo(client)
        with pytest.raises(SMTPDataError) as excinfo:
            client.sendmail(
                "anne@example.com",
                ["bart@example.com"],
                b"From: anne@example.com\n"
                b"To: bart@example.com\n"
                b"Subject: A test\n"
                b"\n"
                b"Testing\xFF\n",
            )
        assert excinfo.value.smtp_code == 500
        assert excinfo.value.smtp_error == S.S500_STRICT_ASCII.mesg


class TestSleepingHandler(_CommonMethods):
    # What is the point here?

    @pytest.mark.handler_data(class_=SleepingHeloHandler)
    def test_close_after_helo(self, nodecode_controller, client):
        #
        # What are we actually testing?
        #
        client.send("HELO example.com\r\n")
        client.sock.shutdown(socket.SHUT_WR)
        with pytest.raises(SMTPServerDisconnected):
            client.getreply()


class TestTimeout(_CommonMethods):
    @pytest.mark.controller_data(class_=TimeoutController)
    def test_timeout(self, plain_controller, client):
        # This one is rapid, it must succeed
        self._ehlo(client)
        time.sleep(0.1 + TimeoutController.Delay)
        with pytest.raises(SMTPServerDisconnected):
            client.mail("anne@example.com")
