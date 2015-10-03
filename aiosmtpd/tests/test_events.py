__all__ = [
    'TestEvents',
    ]


import socket
import asyncio
import smtplib
import unittest
import threading

from aiosmtpd.smtpd import SMTP
from aiosmtpd.events import Debugging
from functools import partial
from io import StringIO


class TestableSMTP(SMTP):
    def smtp_EXIT(self):
        self.stop()


def factory(stream):
    return SMTP(Debugging(stream))


class TestEvents(unittest.TestCase):
    def _start(self, loop, stream):
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        sock.bind(('::0', 9978))
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            loop.create_server(partial(factory, stream), sock=sock))
        loop.run_forever()

    def test_debugging(self):
        stream = StringIO()
        loop = asyncio.get_event_loop()
        thread = threading.Thread(
            target=self._start, args=(loop, stream))
        thread.daemon = True
        thread.start()
        client = smtplib.SMTP()
        client.connect('::0', 9978)
        client.sendmail('anne@example.com', ['bart@example.com'], """\
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test

Testing
""")
        client.docmd('EXIT')
        text = stream.getvalue()
        self.assertMultiLineEqual(text, """\
---------- MESSAGE FOLLOWS ----------
From: Anne Person <anne@example.com>
To: Bart Person <bart@example.com>
Subject: A test
X-Peer: ::1

Testing
------------ END MESSAGE ------------
""")
