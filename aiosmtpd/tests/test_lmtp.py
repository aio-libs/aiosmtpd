# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Test the LMTP protocol."""

import pytest
import socket

from .conftest import Global
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.lmtp import LMTP
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S


class LMTPController(Controller):
    def factory(self):
        self.smtpd = LMTP(self.handler)
        return self.smtpd


@pytest.fixture(scope="module", autouse=True)
def lmtp_controller() -> LMTPController:
    controller = LMTPController(Sink)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


def test_lhlo(lmtp_controller, client):
    code, mesg = client.docmd("LHLO example.com")
    lines = mesg.splitlines()
    assert lines == [
        bytes(socket.getfqdn(), "utf-8"),
        b"SIZE 33554432",
        b"8BITMIME",
        b"HELP",
    ]
    assert code == 250


def test_helo(lmtp_controller, client):
    # HELO and EHLO are not valid LMTP commands.
    resp = client.helo("example.com")
    assert resp == S.S500_CMD_UNRECOG(b"HELO")


def test_ehlo(lmtp_controller, client):
    # HELO and EHLO are not valid LMTP commands.
    resp = client.ehlo("example.com")
    assert resp == S.S500_CMD_UNRECOG(b"EHLO")


def test_help(lmtp_controller, client):
    # https://github.com/aio-libs/aiosmtpd/issues/113
    resp = client.docmd("HELP")
    assert resp == S.S250_SUPPCMD_LMTP
