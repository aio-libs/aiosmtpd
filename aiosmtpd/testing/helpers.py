"""Testing helpers."""

import ssl
import sys
import socket
import struct

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import Envelope, SMTP as Server
from pkg_resources import resource_filename
from typing import List


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
    struct_format = "hh" if sys.platform == "win32" else "ii"
    l_onoff = 1
    l_linger = 0
    client.sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_LINGER,
        struct.pack(struct_format, l_onoff, l_linger),
    )
    client.close()


SUPPORTED_COMMANDS_TLS: bytes = (
    b"Supported commands: AUTH DATA EHLO HELO HELP MAIL "
    b"NOOP QUIT RCPT RSET STARTTLS VRFY"
)

SUPPORTED_COMMANDS_NOTLS = SUPPORTED_COMMANDS_TLS.replace(b" STARTTLS", b"")


def get_server_context() -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.check_hostname = False
    context.load_cert_chain(
        resource_filename("aiosmtpd.tests.certs", "server.crt"),
        resource_filename("aiosmtpd.tests.certs", "server.key"),
    )
    return context


def get_client_context() -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.check_hostname = False
    context.load_verify_locations(
        resource_filename("aiosmtpd.tests.certs", "server.crt")
    )
    return context


class ReceivingHandler:
    box: List[Envelope] = None

    def __init__(self):
        self.box = []

    async def handle_DATA(self, server, session, envelope):
        self.box.append(envelope)
        return "250 OK"


class DecodingController(Controller):
    def factory(self):
        return Server(self.handler, decode_data=True)
