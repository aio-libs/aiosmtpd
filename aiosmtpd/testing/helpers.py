# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Testing helpers."""

import os
import select
import socket
import struct
import sys
import time
from smtplib import SMTP as SMTP_Client
from typing import List, Optional

from aiosmtpd.smtp import Envelope, Session, SMTP

ASYNCIO_CATCHUP_DELAY = float(os.environ.get("ASYNCIO_CATCHUP_DELAY", 0.1))
"""
Delay (in seconds) to give asyncio event loop time to catch up and do things. May need
to be increased for slow and/or overburdened test systems.
"""


def reset_connection(client: SMTP_Client):
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
    assert client.sock is not None
    client.sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_LINGER,
        struct.pack(struct_format, l_onoff, l_linger),
    )
    client.close()


class ReceivingHandler:
    def __init__(self):
        self.box: List[Envelope] = []

    async def handle_DATA(
            self, server: SMTP, session: Session, envelope: Envelope
    ) -> str:
        self.box.append(envelope)
        return "250 OK"


class ChunkedReceivingHandler:
    def __init__(self):
        self.box: List[Envelope] = []
        self.response: Optional[str] = '250 OK'
        self.respond_last = True
        self.sent_response = False

    async def handle_DATA_CHUNK(
            self, server: SMTP, session: Session, envelope: Envelope,
            data: bytes, text: Optional[str], last: bool,
    ) -> Optional[str]:
        assert not self.sent_response
        assert bool(data)
        if text is not None:
            if envelope.content is None:
                envelope.content = ''
            assert isinstance(envelope.content, str)
            envelope.content += text
            if envelope.original_content is None:
                envelope.original_content = b''
            envelope.original_content += data
        else:
            if envelope.content is None:
                envelope.content = b''
            assert isinstance(envelope.content, bytes)
            envelope.content += data

        if last:
            self.box.append(envelope)
        if not last and self.respond_last:
            return None
        if self.response is not None:
            self.sent_response = True
        return self.response


def catchup_delay(delay: float = ASYNCIO_CATCHUP_DELAY):
    """
    Sleep for awhile to give asyncio's event loop time to catch up.
    """
    time.sleep(delay)


def send_recv(
    sock: socket.socket, data: bytes, end: bytes = b"\r\n", timeout: float = 0.1
) -> bytes:
    sock.send(data + end)
    slist = [sock]
    result: List[bytes] = []
    while True:
        read_s, _, _ = select.select(slist, [], [], timeout)
        if read_s:
            # We can use sock instead of read_s because slist only contains sock
            result.append(sock.recv(1024))
        else:
            break
    return b"".join(result)
