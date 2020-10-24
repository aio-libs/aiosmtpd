"""Testing helpers."""

import ssl
import sys
import socket
import struct
import asyncio
import logging
import warnings

from contextlib import ExitStack
from pkg_resources import resource_filename
from typing import Optional
from unittest import TestCase
from unittest.mock import Mock, patch


def reset_connection(client):
    # Close the connection with a TCP RST instead of a TCP FIN.  client must
    # be a smtplib.SMTP instance.
    #
    # https://stackoverflow.com/a/6440364/1570972
    #
    # socket(7) SO_LINGER option.
    #
    # struct linger {
    #   int l_onoff;    /* linger active */
    #   int l_linger;   /* how many seconds to linger for */
    # };
    #
    # Is this correct for Windows/Cygwin and macOS?
    struct_format = 'hh' if sys.platform == 'win32' else 'ii'
    l_onoff = 1
    l_linger = 0
    client.sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_LINGER,
        struct.pack(struct_format, l_onoff, l_linger))
    client.close()


# For integration with flufl.testing.

def setup(testobj):
    testobj.globs['resources'] = ExitStack()


def teardown(testobj):
    testobj.globs['resources'].close()


def make_debug_loop():
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    return loop


def start(plugin):
    if plugin.stderr:
        # Turn on lots of debugging.
        patch('aiosmtpd.smtp.make_loop', make_debug_loop).start()
        logging.getLogger('asyncio').setLevel(logging.DEBUG)
        logging.getLogger('mail.log').setLevel(logging.DEBUG)
        warnings.filterwarnings('always', category=ResourceWarning)


def assert_auth_success(testcase: TestCase, code, response):
    testcase.assertEqual(code, 235)
    testcase.assertEqual(response, b"2.7.0 Authentication successful")


def assert_auth_invalid(testcase: TestCase, code, response):
    testcase.assertEqual(code, 535)
    testcase.assertEqual(response, b'5.7.8 Authentication credentials invalid')


def assert_auth_required(testcase: TestCase, code, response):
    testcase.assertEqual(code, 530)
    testcase.assertEqual(response, b'5.7.0 Authentication required')


SUPPORTED_COMMANDS_TLS: bytes = (
    b'Supported commands: AUTH DATA EHLO HELO HELP MAIL '
    b'NOOP QUIT RCPT RSET STARTTLS VRFY'
)

SUPPORTED_COMMANDS_NOTLS = SUPPORTED_COMMANDS_TLS.replace(b" STARTTLS", b"")


def get_server_context():
    tls_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls_context.load_cert_chain(
        resource_filename('aiosmtpd.tests.certs', 'server.crt'),
        resource_filename('aiosmtpd.tests.certs', 'server.key'),
    )
    return tls_context


class ExitStackWithMock(ExitStack):

    def __init__(self, test_case: Optional[TestCase] = None):
        super().__init__()
        if isinstance(test_case, TestCase):
            test_case.addCleanup(self.close)

    def enter_patch(self, target: str) -> Mock:
        return self.enter_context(patch(target))

    def enter_patch_object(self, obj, target: str) -> Mock:
        return self.enter_context(patch.object(obj, target))


class ReceivingHandler:
    box = None

    def __init__(self):
        self.box = []

    async def handle_DATA(self, server, session, envelope):
        self.box.append(envelope)
        return '250 OK'
