import pytest

from .conftest import ExposingController, Global
from aiosmtpd.smtp import Session as Sess_, SMTP as Server
from aiosmtpd.testing.helpers import (
    catchup_delay,
    ReceivingHandler,
)
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S
from contextlib import suppress
from email.mime.text import MIMEText


# region #### Harness Classes & Functions #############################################


class EOFingHandler:
    """
    Handler to specifically test SMTP.eof_received() method. To trigger, invoke the
    SMTP NOOP command *twice*
    """
    ssl_existed = None
    result = None

    async def handle_NOOP(self, server: Server, session: Sess_, envelope, arg):
        self.ssl_existed = session.ssl is not None
        self.result = server.eof_received()
        return "250 OK"


class HandshakeFailingHandler:
    def handle_STARTTLS(self, server, session, envelope):
        return False


# endregion


# region #### Fixtures ###############################################################


@pytest.fixture
def tls_controller(
    get_handler, get_controller, ssl_context_server
) -> ExposingController:
    handler = get_handler()
    # controller = TLSController(handler)
    controller = get_controller(
        handler,
        decode_data=True,
        require_starttls=False,
        tls_context=ssl_context_server,
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
def tls_req_controller(
    get_handler, get_controller, ssl_context_server
) -> ExposingController:
    handler = get_handler()
    # controller = TLSRequiredController(handler)
    controller = get_controller(
        handler,
        decode_data=True,
        require_starttls=True,
        tls_context=ssl_context_server,
    )
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def auth_req_tls_controller(
    get_handler, get_controller, ssl_context_server
) -> ExposingController:
    handler = get_handler()
    # controller = RequireTLSAuthDecodingController(handler)
    controller = get_controller(
        handler,
        decode_data=True,
        auth_require_tls=True,
        tls_context=ssl_context_server,
    )
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


# endregion


class TestNoTLS:
    def test_disabled_tls(self, plain_controller, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.docmd("STARTTLS")
        assert resp == S.S454_TLS_NA


@pytest.mark.usefixtures("tls_controller")
class TestStartTLS:
    def test_help_starttls(self, tls_controller, client):
        resp = client.docmd("HELP STARTTLS")
        assert resp == S.S250_SYNTAX_STARTTLS

    def test_starttls_arg(self, tls_controller, client):
        resp = client.docmd("STARTTLS arg")
        assert resp == S.S501_SYNTAX_STARTTLS

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

    def test_starttls_quit(self, tls_controller, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.starttls()
        assert resp == S.S220_READY_TLS
        resp = client.quit()
        assert resp == S.S221_BYE
        client.close()

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
        assert resp == S.S501_SYNTAX_STARTTLS

    def test_help_after_starttls(self, client):
        resp = client.docmd("HELP")
        assert resp == S.S250_SUPPCMD_TLS

    def test_helo_starttls(self, tls_controller, client):
        resp = client.helo("example.com")
        assert resp == S.S250_FQDN
        # Entering portion of code where hang is possible (upon assertion fail), so
        # we must wrap with "try..finally".
        try:
            resp = client.docmd("STARTTLS")
            assert resp == S.S220_READY_TLS
        finally:
            tls_controller.stop()


class TestTLSEnding:

    @pytest.mark.handler_data(class_=EOFingHandler)
    def test_eof_received(self, tls_controller, client):
        # I don't like this. It's too intimately involved with the innards of the SMTP
        # class. But for the life of me, I can't figure out why coverage there fail
        # intermittently.
        #
        # I suspect it's a race condition, but with what, and how to prevent that from
        # happening, that's ... a mystery.

        # Entering portion of code where hang is possible (upon assertion fail), so
        # we must wrap with "try..finally".
        try:
            code, mesg = client.ehlo("example.com")
            assert code == 250
            resp = client.starttls()
            assert resp == S.S220_READY_TLS
            # Need this to make SMTP update its internal session variable
            code, mesg = client.ehlo("example.com")
            assert code == 250
            sess: Sess_ = tls_controller.smtpd.session
            assert sess.ssl is not None
            client.noop()
            catchup_delay()
            handler: EOFingHandler = tls_controller.handler
            assert handler.ssl_existed is True
            assert handler.result is False
        finally:
            tls_controller.stop()


@pytest.mark.usefixtures("tls_controller")
class TestTLSForgetsSessionData:
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
        assert resp == S.S503_MAIL_NEEDED

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
        assert resp == S.S503_RCPT_NEEDED


@pytest.mark.usefixtures("tls_req_controller")
class TestRequireTLS:
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
class TestRequireTLSAUTH:
    def test_auth_notls(self, client):
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.docmd("AUTH ")
        assert resp == S.S538_AUTH_ENCRYPTREQ

    def test_auth_tls(self, client):
        resp = client.starttls()
        assert resp == S.S220_READY_TLS
        code, _ = client.ehlo("example.com")
        assert code == 250
        resp = client.docmd("AUTH PLAIN AHRlc3QAdGVzdA==")
        assert resp == S.S535_AUTH_INVALID
