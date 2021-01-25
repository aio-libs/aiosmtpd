# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Test SMTP over SSL/TLS."""

import ssl
import socket
import unittest
import pkg_resources

from aiosmtpd.controller import Controller as BaseController
from aiosmtpd.smtp import SMTP as SMTPProtocol
from contextlib import ExitStack
from aiosmtpd.testing.helpers import (
    ReceivingHandler,
    get_server_context
)
from email.mime.text import MIMEText
from smtplib import SMTP_SSL
from unittest.mock import patch


ModuleResources = ExitStack()


def setUpModule():
    # Needed especially on FreeBSD because socket.getfqdn() is slow on that OS,
    # and oftentimes (not always, though) leads to Error
    ModuleResources.enter_context(patch("socket.getfqdn", return_value="localhost"))


def tearDownModule():
    ModuleResources.close()


class Controller(BaseController):
    def factory(self):
        return SMTPProtocol(self.handler)


def get_client_context():
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.check_hostname = False
    context.load_verify_locations(
        cafile=pkg_resources.resource_filename(
            'aiosmtpd.tests.certs', 'server.crt'))
    return context


class TestSMTPS(unittest.TestCase):
    def setUp(self):
        self.handler = ReceivingHandler()
        controller = Controller(self.handler, ssl_context=get_server_context())
        controller.start()
        self.addCleanup(controller.stop)
        self.address = (controller.hostname, controller.port)

    def test_smtps(self):
        with SMTP_SSL(*self.address, context=get_client_context()) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            self.assertEqual(response, socket.getfqdn().encode('utf-8'))
            client.send_message(
                MIMEText('hi'), 'sender@example.com', 'rcpt1@example.com')
        self.assertEqual(len(self.handler.box), 1)
        envelope = self.handler.box[0]
        self.assertEqual(envelope.mail_from, 'sender@example.com')
