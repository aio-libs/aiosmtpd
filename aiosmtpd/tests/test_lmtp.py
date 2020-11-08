"""Test the LMTP protocol."""

import pytest
import socket

from .conftest import Global
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.lmtp import LMTP


class LMTPController(Controller):
    def factory(self):
        return LMTP(self.handler)


@pytest.fixture(autouse=True)
def lmtp_controller():
    controller = LMTPController(Sink)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


def test_lhlo(lmtp_controller, client):
    resp = client.docmd("LHLO example.com")
    assert resp == (250, bytes(socket.getfqdn(), "utf-8"))


def test_helo(lmtp_controller, client):
    # HELO and EHLO are not valid LMTP commands.
    resp = client.helo("example.com")
    assert resp == (500, b'Error: command "HELO" not recognized')


def test_ehlo(lmtp_controller, client):
    # HELO and EHLO are not valid LMTP commands.
    resp = client.ehlo("example.com")
    assert resp == (500, b'Error: command "EHLO" not recognized')


def test_help(lmtp_controller, client):
    # https://github.com/aio-libs/aiosmtpd/issues/113
    resp = client.docmd("HELP")
    assert resp == (
        250,
        b"Supported commands: AUTH DATA HELP LHLO MAIL NOOP QUIT RCPT RSET VRFY",
    )
