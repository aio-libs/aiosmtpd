"""Test SMTP over SSL/TLS."""

import pytest

from .conftest import Global
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPProtocol
from aiosmtpd.testing.helpers import (
    ReceivingHandler,
    get_client_context,
    get_server_context,
)
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S
from email.mime.text import MIMEText
from smtplib import SMTP_SSL


class SimpleController(Controller):
    def factory(self):
        return SMTPProtocol(self.handler)


@pytest.fixture
def ssl_controller() -> SimpleController:
    context = get_server_context()
    handler = ReceivingHandler()
    controller = SimpleController(handler, ssl_context=context)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def smtps_client() -> SMTP_SSL:
    context = get_client_context()
    with SMTP_SSL(*Global.SrvAddr, context=context) as client:
        yield client


class TestSMTPS:
    def test_smtps(self, ssl_controller, smtps_client):
        sender = "sender@example.com"
        recipients = ["rcpt1@example.com"]
        resp = smtps_client.helo("example.com")
        assert resp == S.S250_FQDN
        results = smtps_client.send_message(MIMEText("hi"), sender, recipients)
        assert results == {}
        handler: ReceivingHandler = ssl_controller.handler
        assert len(handler.box) == 1
        envelope = handler.box[0]
        assert envelope.mail_from == sender
        assert envelope.rcpt_tos == recipients
