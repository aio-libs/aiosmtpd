import time
import random
import socket
import asyncio
import unittest
import ipaddress

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as SMTPServer
from smtplib import SMTP as SMTPClient
from unittest.mock import Mock


class ProxyPeekerHandler(Sink):
    def __init__(self):
        self.called = False
        self.proxy = []
        self.retval = True

    async def handle_PROXY(self, server, session, envelope, proxy_pieces):
        self.called = True
        self.proxy.extend(proxy_pieces)
        return self.retval


class TestProxyProtocol(unittest.TestCase):
    def setUp(self):
        self.proxy_timeout = 1.0
        self.transport = Mock()
        self.transport.write = self._write
        self.responses = []
        self._old_loop = asyncio.get_event_loop()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()
        asyncio.set_event_loop(self._old_loop)

    def _write(self, data):
        self.responses.append(data)

    def _run(self, protocol):
        try:
            self.loop.run_until_complete(protocol._handler_coroutine)
        except asyncio.CancelledError:
            pass

    def _get_proxy_protocol(self, *args, **kwargs):
        kwargs.setdefault("proxy_protocol_timeout", self.proxy_timeout)
        protocol = SMTPServer(*args, loop=self.loop, **kwargs)
        protocol.connection_made(self.transport)
        return protocol

    def test_noproxy(self):
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        data = b"HELO example.org\r\n"
        protocol.data_received(data)
        self._run(protocol)
        assert self.transport.close.called

    def test_tcp4(self):
        srcip = "1.2.3.4"
        dstip = "5.6.7.8"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert handler.called
        assert handler.proxy == [
            b"TCP4",
            ipaddress.IPv4Address(srcip),
            ipaddress.IPv4Address(dstip),
            srcport,
            dstport
        ]

    def test_tcp6_shortened(self):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert handler.called
        assert handler.proxy == [
            b"TCP6",
            ipaddress.IPv6Address(srcip),
            ipaddress.IPv6Address(dstip),
            srcport,
            dstport
        ]

    def test_tcp6_random(self):
        srcip = ":".join(f"{random.getrandbits(16):04x}" for _ in range(0, 8))
        dstip = ":".join(f"{random.getrandbits(16):04x}" for _ in range(0, 8))
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert handler.called
        assert handler.proxy == [
            b"TCP6",
            ipaddress.IPv6Address(srcip),
            ipaddress.IPv6Address(dstip),
            srcport,
            dstport
        ]

    def test_unknown(self):
        prox_test = "PROXY UNKNOWN whatever\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert handler.called
        assert handler.proxy == [
            b"UNKNOWN",
            b" whatever"
        ]

    def test_unknown_short(self):
        prox_test = "PROXY UNKNOWN\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert handler.called
        assert handler.proxy == [
            b"UNKNOWN",
            b""
        ]

    def test_too_long(self):
        prox_test = "PROXY UNKNOWN " + "*" * 100
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_nocr(self):
        prox_test = "PROXY UNKNOWN\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_notproxy(self):
        srcip = "1.2.3.4"
        dstip = "5.6.7.8"
        srcport = 65535
        dstport = 65535
        prox_test = f"NOTPROX TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_wrongtype_64(self):
        srcip = "1.2.3.4"
        dstip = "5.6.7.8"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_wrongtype_46(self):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_wrongtype_6mixed(self):
        srcip = "1.2.3.4"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_zeroleader(self):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 2501
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} 0{srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_space1(self):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6  {srcip} {dstip} {srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_space2(self):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip}  {srcport} {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_space3(self):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport}  {dstport}\r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called

    def test_malformed_space4(self):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport} \r\n"
        handler = ProxyPeekerHandler()
        protocol = self._get_proxy_protocol(handler)
        protocol.data_received(
            prox_test.encode("ascii") + b"QUIT\r\n"
        )
        self._run(protocol)
        assert not protocol._proxy_result
        assert not handler.called
        assert self.transport.close.called


class TestProxyProtocolController(unittest.TestCase):
    def setUp(self):
        self.proxy_timeout = 1.0
        self.handler = ProxyPeekerHandler()
        kwargs = dict(proxy_protocol_timeout=self.proxy_timeout)
        self.controller = Controller(
            self.handler, hostname="127.0.0.1", port=8025, server_kwargs=kwargs
        )
        self.controller.start()

    def tearDown(self) -> None:
        self.controller.stop()

    def test_timeout(self):
        prox_test = b"PROXY TCP4 255.255.255.255 255.255.255.255 65535 65535\r\n"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(("127.0.0.1", 8025))
            time.sleep(self.proxy_timeout * 1.1)
            with self.assertRaises(ConnectionAbortedError):
                sock.send(prox_test)
                resp = sock.recv(4096)

    def test_nonewline(self):
        prox_test = b"PROXY TCP4 255.255.255.255 255.255.255.255 65535 65535\r"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(("127.0.0.1", 8025))
            time.sleep(self.proxy_timeout * 1.1)
            with self.assertRaises(ConnectionAbortedError):
                sock.send(prox_test)
                resp = sock.recv(4096)

    def test_okay(self):
        prox_test = b"PROXY TCP4 255.255.255.255 255.255.255.255 65535 65535\r\n"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(("127.0.0.1", 8025))
            sock.sendall(prox_test)
            resp = sock.makefile("rb").readline()
            assert resp.startswith(b"220 ")
            client = SMTPClient()
            client.sock = sock
            code, mesg = client.ehlo("example.org")
            assert code == 250
            code, mesg = client.quit()
            assert code == 221
