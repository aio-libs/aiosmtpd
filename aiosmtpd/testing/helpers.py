"""Testing helpers."""

import ssl
import sys
import socket
import struct
import smtplib

from aiosmtpd.controller import Controller
from contextlib import ExitStack
from pkg_resources import resource_filename
from typing import Optional, Tuple
from unittest import TestCase
from unittest.mock import DEFAULT, Mock, patch


SMTPResponse = Tuple[int, bytes]


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


SUPPORTED_COMMANDS_TLS: bytes = (
    b'Supported commands: AUTH DATA EHLO HELO HELP MAIL '
    b'NOOP QUIT RCPT RSET STARTTLS VRFY'
)

SUPPORTED_COMMANDS_NOTLS = SUPPORTED_COMMANDS_TLS.replace(b" STARTTLS", b"")


class SMTP_with_asserts(smtplib.SMTP):

    _addr: Tuple[str, int] = None

    def __init__(self,
                 testcase: TestCase,
                 *args,
                 from_: Controller = None,
                 **kwargs):
        if not isinstance(testcase, TestCase):
            raise RuntimeError("testcase not a TestCase")
        if isinstance(from_, Controller):
            kwargs["host"] = from_.hostname
            kwargs["port"] = from_.port
            self._addr = (from_.hostname, from_.port)
        super().__init__(*args, **kwargs)
        self._testcase = testcase

    def assert_cmd_resp(self, cmd: str, expected_response: SMTPResponse):
        """
        Sends cmd using .docmd() and assert that response tuple is exactly
        the same as expected_response
        """
        self._testcase.assertEqual(
            expected_response,
            self.docmd(cmd)
        )

    def assert_cmd_ok(self, cmd):
        self.assert_cmd_resp(cmd, (250, b"OK"))

    def assert_auth_invalid(self, auth_cmd: str):
        self.assert_cmd_resp(
            auth_cmd,
            (535, b'5.7.8 Authentication credentials invalid'),
        )

    def assert_auth_success(self, auth_cmd: str):
        """
        Send auth_cmd using .docmd() and assert that server responds with an
        'Authentication successful' status.
        """
        self.assert_cmd_resp(
            auth_cmd,
            (235, b"2.7.0 Authentication successful"),
        )

    def assert_auth_required(self, cmd: str):
        """
        Send auth_cmd using .docmd() and assert that server responds with an
        'Authentication required' status.
        """
        self.assert_cmd_resp(
            cmd,
            (530, b'5.7.0 Authentication required'),
        )

    def assert_ehlo_ok(self, name: str):
        """Assert EHLO returns code 250, message ignored"""
        code, resp_text = self.ehlo(name)
        self._testcase.assertEqual(250, code)
        return resp_text

    def assert_helo_ok(self, name: str):
        """Assert HELO returns code 250, message ignored"""
        code, resp_text = self.helo(name)
        self._testcase.assertEqual(250, code)
        return resp_text


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

    def enter_patch(self, target: str, new=DEFAULT, spec=None, create=False,
                    spec_set=None, autospec=None, new_callable=None, **kwargs)\
            -> Mock:
        return self.enter_context(
            patch(target, new=new, spec=spec, create=create, spec_set=spec_set,
                  autospec=autospec, new_callable=new_callable, **kwargs))

    def enter_patch_object(self, obj, target: str) -> Mock:
        return self.enter_context(patch.object(obj, target))


class ReceivingHandler:
    box = None

    def __init__(self):
        self.box = []

    async def handle_DATA(self, server, session, envelope):
        self.box.append(envelope)
        return '250 OK'
