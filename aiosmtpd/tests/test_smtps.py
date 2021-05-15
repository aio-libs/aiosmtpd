# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Test SMTP over SSL/TLS."""

import sys
import time
from email.mime.text import MIMEText
from smtplib import SMTP, SMTPServerDisconnected, SMTP_SSL
from typing import Generator, Union

import pytest

from aiosmtpd.controller import Controller
from aiosmtpd.testing.helpers import ReceivingHandler
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S

from .conftest import Global, controller_data


@pytest.fixture
def ssl_controller(
    get_controller, ssl_context_server
) -> Generator[Controller, None, None]:
    handler = ReceivingHandler()
    controller = get_controller(
        handler,
        hostname="127.0.0.1",
        ssl_context=ssl_context_server
    )
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    controller.stop()


@pytest.fixture
def smtps_client(ssl_context_client) -> Generator[Union[SMTP_SSL, SMTP], None, None]:
    context = ssl_context_client
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

    @pytest.mark.skipif(sys.version_info < (3, 7),
                        reason="SSL timeout implemented implemented with 3.7")
    @controller_data(ssl_handshake_timeout=1.0)
    def test_SSL_timeout(self, ssl_controller):
        assert ssl_controller.server._ssl_handshake_timeout == 1.0
        start = time.monotonic()
        with pytest.raises(SMTPServerDisconnected) as ex, \
             SMTP(*Global.SrvAddr) as smtp_client:  # noqa: N400
            # smtplib.SMTP does not support opporutnistic SSL so the SSL
            # handshake never completes. On Python 3.6 and earlier this meant
            # that the connection would just hang.
            smtp_client.helo("example.com")
        end = time.monotonic()
        assert "Connection unexpectedly closed" in str(ex)
        # default ssl_handshake_timeout is 60 seconds
        assert end <= start + 61.0
