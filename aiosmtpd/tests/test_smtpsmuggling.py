# Copyright 2024 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Test SMTP smuggling."""

import smtplib

from aiosmtpd.testing.helpers import ReceivingHandler
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S

from .conftest import handler_data


def new_data(self, msg):
    self.putcmd("data")

    (code, repl) = self.getreply()
    if self.debuglevel > 0:
        self._print_debug('data:', (code, repl))
    if code != 354:
        raise smtplib.SMTPDataError(code, repl)
    else:
        q = msg
        self.send(q)
        (code, msg) = self.getreply()
        if self.debuglevel > 0:
            self._print_debug('data:', (code, msg))
        return (code, msg)


def return_unchanged(data):
    return data


class TestSmuggling:
    @handler_data(class_=ReceivingHandler)
    def test_smtp_smuggling(self, plain_controller, client):
        smtplib._fix_eols = return_unchanged
        smtplib._quote_periods = return_unchanged
        smtplib.SMTP.data = new_data

        handler = plain_controller.handler
        sender = "sender@example.com"
        recipients = ["rcpt1@example.com"]
        resp = client.helo("example.com")
        assert resp == S.S250_FQDN
        # Trying SMTP smuggling with a fake \n.\r\n end-of-data sequence.
        message_data = b"""\
From: Anne Person <anne@example.com>\r\n\
To: Bart Person <bart@example.com>\r\n\
Subject: A test\r\n\
Message-ID: <ant>\r\n\
\r\n\
Testing\
\n.\r\n\
NO SMUGGLING
\r\n.\r\n\
"""
        client.sendmail(sender, recipients, message_data)
        client.quit()
        assert b"NO SMUGGLING" in handler.box[0].content
