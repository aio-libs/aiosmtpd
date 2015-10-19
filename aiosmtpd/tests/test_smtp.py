"""Test the SMTP protocol."""

__all__ = [
    'TestSMTP',
    ]


import socket
import unittest

from aiosmtpd.smtp import SMTP as Server
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from smtplib import SMTP


class UTF8Controller(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True)


class TestSMTP(unittest.TestCase):
    def setUp(self):
        controller = UTF8Controller(Sink)
        controller.start()
        self.address = (controller.hostname, controller.port)
        self.addCleanup(controller.stop)

    def test_helo(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            self.assertEqual(response, bytes(socket.getfqdn(), 'utf-8'))

    def test_helo_no_hostname(self):
        with SMTP(*self.address) as client:
            # smtplib substitutes .local_hostname if the argument is falsey.
            client.local_hostname = ''
            code, response = client.helo('')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: HELO hostname')

    def test_helo_duplicate(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.helo('example.org')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Duplicate HELO/EHLO')

    def test_ehlo(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            lines = response.splitlines()
            self.assertEqual(lines[0], bytes(socket.getfqdn(), 'utf-8'))
            self.assertEqual(lines[1], b'SIZE 33554432')
            self.assertEqual(lines[2], b'HELP')

    def test_ehlo_duplicate(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.ehlo('example.org')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Duplicate HELO/EHLO')

    def test_ehlo_no_hostname(self):
        with SMTP(*self.address) as client:
            # smtplib substitutes .local_hostname if the argument is falsey.
            client.local_hostname = ''
            code, response = client.ehlo('')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: EHLO hostname')

    def test_helo_then_ehlo(self):
        with SMTP(*self.address) as client:
            code, response = client.helo('example.com')
            self.assertEqual(code, 250)
            code, response = client.ehlo('example.org')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Duplicate HELO/EHLO')

    def test_ehlo_then_helo(self):
        with SMTP(*self.address) as client:
            code, response = client.ehlo('example.com')
            self.assertEqual(code, 250)
            code, response = client.helo('example.org')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Duplicate HELO/EHLO')

    def test_noop(self):
        with SMTP(*self.address) as client:
            code, response = client.noop()
            self.assertEqual(code, 250)

    def test_noop_with_arg(self):
        with SMTP(*self.address) as client:
            # .noop() doesn't accept arguments.
            code, response = client.docmd('noop', 'oops')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: NOOP')

    def test_quit(self):
        client = SMTP(*self.address)
        code, response = client.quit()
        self.assertEqual(code, 221)
        self.assertEqual(response, b'Bye')

    def test_quit_with_arg(self):
        client = SMTP(*self.address)
        code, response = client.docmd('quit', 'oops')
        self.assertEqual(code, 501)
        self.assertEqual(response, b'Syntax: QUIT')

    def test_help(self):
        with SMTP(*self.address) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('help')
            self.assertEqual(code, 250)
            self.assertEqual(response,
                             b'Supported commands: EHLO HELO MAIL RCPT '
                             b'DATA RSET NOOP QUIT VRFY')

    def test_help_ehlo(self):
        with SMTP(*self.address) as client:
            # Don't get tricked by smtplib processing of the response.
            code, response = client.docmd('help', 'ehlo')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'Syntax: EHLO hostname')

    def test_expn(self):
        with SMTP(*self.address) as client:
            code, response = client.expn('anne@example.com')
            self.assertEqual(code, 502)
            self.assertEqual(response, b'EXPN not implemented')

    def test_mail_no_helo(self):
        with SMTP(*self.address) as client:
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: send HELO first')

    def test_mail_no_arg(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('MAIL')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')

    def test_mail_no_from(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('MAIL <anne@example.com>')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')

    def test_mail_params_no_esmtp(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> SIZE=10000')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')

    def test_mail_params_esmtp(self):
        with SMTP(*self.address) as client:
            client.ehlo('example.com')
            code, response = client.docmd(
                'MAIL FROM: <anne@example.com> SIZE=10000')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')

    def test_mail_from_twice(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 250)
            self.assertEqual(response, b'OK')
            code, response = client.docmd('MAIL FROM: <anne@example.com>')
            self.assertEqual(code, 503)
            self.assertEqual(response, b'Error: nested MAIL command')

    def test_mail_from_malformed(self):
        with SMTP(*self.address) as client:
            client.helo('example.com')
            code, response = client.docmd('MAIL FROM: Anne <anne@example.com>')
            self.assertEqual(code, 501)
            self.assertEqual(response, b'Syntax: MAIL FROM: <address>')
