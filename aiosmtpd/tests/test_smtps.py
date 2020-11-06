"""Test SMTP over SSL/TLS."""

import pytest
import socket

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
    controller = SimpleController(handler, ssl_context=context)
    controller.start()
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def client(ssl_controller) -> SMTP_SSL:
    context = get_client_context()
    c = ssl_controller
    with SMTP_SSL(c.hostname, c.port, context=context) as client:
        yield client


class TestSMTPSNieuw:
    def test_smtps(self, ssl_controller, client):
        sender = "sender@example.com"
        recipients = ["rcpt1@example.com"]
        code, mesg = client.helo("example.com")
        assert code == 250
        assert mesg == socket.getfqdn().encode("utf-8")
        results = client.send_message(MIMEText("hi"), sender, recipients)
        assert results == {}
        handler: ReceivingHandler = ssl_controller.handler
        assert len(handler.box) == 1
        envelope = handler.box[0]
        assert envelope.mail_from == sender
        assert envelope.rcpt_tos == recipients
