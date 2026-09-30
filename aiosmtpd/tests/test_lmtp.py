# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Test the LMTP protocol."""

import socket
from smtplib import SMTP as SMTPClient
from typing import Generator

import pytest

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.lmtp import LMTP
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S

from .conftest import Global


class LMTPController(Controller):
    def factory(self):
        self.smtpd = LMTP(self.handler)
        return self.smtpd


@pytest.fixture(scope="module", autouse=True)
def lmtp_controller() -> Generator[LMTPController, None, None]:
    controller = LMTPController(Sink)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


def test_lhlo(client):
    code, mesg = client.docmd("LHLO example.com")
    lines = mesg.splitlines()
    assert lines == [
        bytes(socket.getfqdn(), "utf-8"),
        b"SIZE 33554432",
        b"8BITMIME",
        b"HELP",
    ]
    assert code == 250


def test_helo(client):
    # HELO and EHLO are not valid LMTP commands.
    resp = client.helo("example.com")
    assert resp == S.S500_CMD_UNRECOG(b"HELO")


def test_ehlo(client):
    # HELO and EHLO are not valid LMTP commands.
    resp = client.ehlo("example.com")
    assert resp == S.S500_CMD_UNRECOG(b"EHLO")


def test_help(client):
    # https://github.com/aio-libs/aiosmtpd/issues/113
    resp = client.docmd("HELP")
    assert resp == S.S250_SUPPCMD_LMTP


class SingleStatus:
    """Returns one status for the whole message."""

    async def handle_DATA(self, server, session, envelope) -> str:
        return "451 4.3.0 Try again later"


class PerRecipientStatus:
    """Returns one status per accepted recipient (RFC 2033 § 4.2)."""

    async def handle_DATA(self, server, session, envelope) -> list:
        return ["250 OK", "550 5.1.1 No such user"]


class TooFewStatuses:
    """Returns fewer statuses than there are recipients."""

    async def handle_DATA(self, server, session, envelope) -> list:
        return ["250 OK"]


@pytest.fixture
def lmtp_client(request) -> Generator[SMTPClient, None, None]:
    """An LMTP controller running ``request.param`` as its handler, plus a
    client already connected to it."""
    # A port of its own: the module-scoped ``lmtp_controller`` already holds
    # the default one.
    controller = LMTPController(request.param(), port=Global.SrvAddr.port + 1)
    controller.start()
    try:
        with SMTPClient(controller.hostname, controller.port) as client:
            yield client
    finally:
        controller.stop()


def send_data(client: SMTPClient, *recipients: str) -> None:
    """Drive a full LMTP transaction up to (and including) the final dot."""
    assert client.docmd("LHLO example.com")[0] == 250
    assert client.docmd("MAIL FROM:<sender@example.com>")[0] == 250
    for rcpt in recipients:
        assert client.docmd(f"RCPT TO:<{rcpt}>")[0] == 250
    assert client.docmd("DATA")[0] == 354
    client.send(b"From: sender@example.com\r\n\r\nbody\r\n.\r\n")


@pytest.mark.parametrize("lmtp_client", [Sink], indirect=True)
def test_data_replies_once_per_recipient(lmtp_client):
    # https://github.com/aio-libs/aiosmtpd/issues/517
    send_data(lmtp_client, "a@example.com", "b@example.com")
    assert lmtp_client.getreply() == S.S250_OK
    assert lmtp_client.getreply() == S.S250_OK


@pytest.mark.parametrize("lmtp_client", [Sink], indirect=True)
def test_data_single_recipient_replies_once(lmtp_client):
    send_data(lmtp_client, "a@example.com")
    assert lmtp_client.getreply() == S.S250_OK
    # The session is usable again straight away: no reply is left unread.
    assert lmtp_client.docmd("NOOP") == S.S250_OK


@pytest.mark.parametrize("lmtp_client", [SingleStatus], indirect=True)
def test_data_single_status_is_repeated(lmtp_client):
    send_data(lmtp_client, "a@example.com", "b@example.com")
    expected = (451, b"4.3.0 Try again later")
    assert lmtp_client.getreply() == expected
    assert lmtp_client.getreply() == expected


@pytest.mark.parametrize("lmtp_client", [PerRecipientStatus], indirect=True)
def test_data_per_recipient_statuses_in_order(lmtp_client):
    send_data(lmtp_client, "a@example.com", "b@example.com")
    assert lmtp_client.getreply() == S.S250_OK
    assert lmtp_client.getreply() == (550, b"5.1.1 No such user")


@pytest.mark.parametrize("lmtp_client", [TooFewStatuses], indirect=True)
def test_data_status_count_mismatch_is_an_error(lmtp_client):
    send_data(lmtp_client, "a@example.com", "b@example.com")
    code, mesg = lmtp_client.getreply()
    assert code == 500
    assert b"ValueError" in mesg
