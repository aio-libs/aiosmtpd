import pytest

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as SMTPProtocol
from aiosmtpd.testing.helpers import (
    ReceivingHandler,
    SUPPORTED_COMMANDS_TLS,
    get_server_context,
)
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S
from .conftest import Global
from contextlib import suppress
from email.mime.text import MIMEText
from smtplib import SMTP


# region #### Harness Classes & Functions #############################################


class SimpleController(Controller):
    def factory(self):
        return SMTPProtocol(self.handler)


class TLSRequiredController(SimpleController):
    def factory(self):
        return SMTPProtocol(
            self.handler,
            decode_data=True,
            require_starttls=True,
            tls_context=get_server_context(),
        )


class TLSController(SimpleController):
    def factory(self):
        return SMTPProtocol(
            self.handler,
            decode_data=True,
            require_starttls=False,
            tls_context=get_server_context(),
        )


class RequireTLSAuthDecodingController(SimpleController):
    def factory(self):
        return SMTPProtocol(
            self.handler,
            decode_data=True,
            auth_require_tls=True,
            tls_context=get_server_context(),
        )


class HandshakeFailingHandler:
    def handle_STARTTLS(self, server, session, envelope):
        return False


# endregion


# region #### Fixtures ###############################################################


@pytest.fixture
def tls_controller(get_handler) -> TLSController:
    handler = get_handler()
    controller = TLSController(handler)
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
def tls_req_controller(get_handler) -> TLSController:
    handler = get_handler()
    controller = TLSRequiredController(handler)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def auth_req_tls_controller(get_handler) -> RequireTLSAuthDecodingController:
    handler = get_handler()
    controller = RequireTLSAuthDecodingController(handler)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def simple_controller() -> SimpleController:
    handler = Sink()
    controller = SimpleController(handler)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def client() -> SMTP:
    with SMTP(*Global.SrvAddr) as client:
        yield client


# endregion


def test_disabled_tls(simple_controller, client):
    code, _ = client.ehlo("example.com")
    assert code == 250
    resp = client.docmd("STARTTLS")
    assert resp == (454, b"TLS not available")


@pytest.mark.usefixtures("tls_controller")
class TestStartTLSNieuw:
    @pytest.mark.handler_data(class_=ReceivingHandler)
    def test_starttls(self, tls_controller, client):
        sender = "sender@example.com"
        recipients = ["rcpt1@example.com"]
        code, _ = client.ehlo("example.com")
        assert code == 250
        assert "starttls" in client.esmtp_features
        resp = client.starttls()
        assert resp == S.S220_READY_TLS
        client.send_message(MIMEText("hi"), sender, recipients)
        handler: ReceivingHandler = tls_controller.handler
        assert len(handler.box) == 1
        assert handler.box[0].mail_from == sender
        assert handler.box[0].rcpt_tos == recipients

    @pytest.mark.handler_data(class_=HandshakeFailingHandler)
    def test_failed_handshake(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.starttls()
        assert resp == S.S220_READY_TLS
        resp = client.mail("sender@example.com")
        assert resp == S.S554_LACK_SECURITY
        resp = client.rcpt("rcpt@example.com")
        assert resp == S.S554_LACK_SECURITY

    def test_tls_bad_syntax(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.docmd("STARTTLS", "TRUE")
        assert resp == (501, b"Syntax: STARTTLS")

    def test_help_after_starttls(self, client):
        resp = client.docmd("HELP")
        assert resp == (250, SUPPORTED_COMMANDS_TLS)

    def test_helo_starttls(self, tls_controller, client):
        code, _ = client.helo("example.com")
        assert code == 250
        # Entering portion of code where hang is possible (upon assertion fail), so
        # we must wrap with "try..finally".
        try:
            resp = client.docmd("STARTTLS")
            assert resp == S.S220_READY_TLS
        finally:
            tls_controller.stop()


@pytest.mark.usefixtures("tls_controller")
class TestTLSForgetsSessionDataNieuw:
    def test_forget_ehlo(self, client):
        resp = client.starttls()
        assert resp == S.S220_READY_TLS
        resp = client.mail("sender@example.com")
        assert resp == S.S503_HELO_FIRST

    def test_forget_mail(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.mail("sender@example.com")
        assert resp == S.S250_OK
        resp = client.starttls()
        assert resp == S.S220_READY_TLS
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.rcpt("rcpt@example.com")
        assert resp == (503, b"Error: need MAIL command")

    def test_forget_rcpt(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.mail("sender@example.com")
        assert resp == S.S250_OK
        resp = client.rcpt("rcpt@example.com")
        assert resp == S.S250_OK
        resp = client.starttls()
        assert resp == S.S220_READY_TLS
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.mail("sender@example.com")
        assert resp == S.S250_OK
        resp = client.docmd("DATA")
        assert resp == (503, b"Error: need RCPT command")


@pytest.mark.usefixtures("tls_req_controller")
class TestRequireTLSNieuw:
    def test_helo_fails(self, client):
        resp = client.helo("example.com")
        assert resp == S.S530_STARTTLS_FIRST

    def test_help_fails(self, client):
        resp = client.docmd("HELP", "HELO")
        assert resp == S.S530_STARTTLS_FIRST

    def test_ehlo(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        assert "starttls" in client.esmtp_features

    def test_mail_fails(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.mail("sender@example.com")
        assert resp == S.S530_STARTTLS_FIRST

    def test_rcpt_fails(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.rcpt("recipient@example.com")
        assert resp == S.S530_STARTTLS_FIRST

    def test_vrfy_fails(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.vrfy("sender@exapmle.com")
        assert resp == S.S530_STARTTLS_FIRST

    def test_data_fails(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.docmd("DATA")
        assert resp == S.S530_STARTTLS_FIRST


@pytest.mark.usefixtures("auth_req_tls_controller")
class TestRequireTLSAUTHNieuw:
    def test_auth_notls(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.docmd("AUTH ")
        assert resp == (
            538,
            b"5.7.11 Encryption required for requested authentication mechanism",
        )

    def test_auth_tls(self, client):
        resp = client.starttls()
        assert resp == (220, b"Ready to start TLS")
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.docmd("AUTH PLAIN AHRlc3QAdGVzdA==")
        assert resp == S.S535_AUTH_INVALID
