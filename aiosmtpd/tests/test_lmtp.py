"""Test the LMTP protocol."""

import pytest
import socket

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.lmtp import LMTP
from smtplib import SMTP


class LMTPController(Controller):
    def factory(self):
        return LMTP(self.handler)


@pytest.fixture(autouse=True)
def lmtp_controller():
    controller = LMTPController(Sink)
    controller.start()
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def client(lmtp_controller: LMTPController):
    client = SMTP(lmtp_controller.hostname, lmtp_controller.port)
    #
    yield client
    #
    client.quit()


def test_lhlo(client):
    resp = client.docmd("LHLO example.com")
    assert resp == (250, bytes(socket.getfqdn(), 'utf-8'))


def test_helo(client):
    # HELO and EHLO are not valid LMTP commands.
    resp = client.helo('example.com')
    assert resp == (500, b'Error: command "HELO" not recognized')


def test_ehlo(client):
    # HELO and EHLO are not valid LMTP commands.
    resp = client.ehlo('example.com')
    assert resp == (500, b'Error: command "EHLO" not recognized')


def test_help(client):
    # https://github.com/aio-libs/aiosmtpd/issues/113
    resp = client.docmd("HELP")
    assert resp == (250,
                    b'Supported commands: AUTH DATA HELP LHLO MAIL '
                    b'NOOP QUIT RCPT RSET VRFY'
                    )
