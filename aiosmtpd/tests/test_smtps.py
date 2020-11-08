"""Test SMTP over SSL/TLS."""

import pytest
import socket

from .conftest import SRV_ADDR
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPProtocol
from aiosmtpd.testing.helpers import (
    ReceivingHandler,
    get_client_context,
    get_server_context,
)
from email.mime.text import MIMEText
from smtplib import SMTP_SSL


class SimpleController(Controller):
    def factory(self):
        return SMTPProtocol(self.handler)


@pytest.fixture
def ssl_controller() -> Controller:
    context = get_server_context()
    handler = ReceivingHandler()
    controller = SimpleController(
        handler, hostname=SRV_ADDR.host, port=SRV_ADDR.port, ssl_context=context
    )
    controller.start()
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def smtps_client() -> SMTP_SSL:
    context = get_client_context()
    with SMTP_SSL(SRV_ADDR.host, SRV_ADDR.port, context=context) as client:
        yield client


class TestSMTPSNieuw:
    def test_smtps(self, ssl_controller, smtps_client):
        sender = "sender@example.com"
        recipients = ["rcpt1@example.com"]
        code, mesg = smtps_client.helo("example.com")
        assert code == 250
        assert mesg == socket.getfqdn().encode("utf-8")
        results = smtps_client.send_message(MIMEText("hi"), sender, recipients)
        assert results == {}
        handler: ReceivingHandler = ssl_controller.handler
        assert len(handler.box) == 1
        envelope = handler.box[0]
        assert envelope.mail_from == sender
        assert envelope.rcpt_tos == recipients
