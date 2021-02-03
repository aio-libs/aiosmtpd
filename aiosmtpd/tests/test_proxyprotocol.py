import time
import pytest
import random
import socket
import asyncio

from aiosmtpd.handlers import Sink
from aiosmtpd.proxy_protocol import ProxyData
from aiosmtpd.smtp import SMTP as SMTPServer
from aiosmtpd.tests.conftest import controller_data, handler_data, Global
from ipaddress import IPv4Address, IPv6Address
from smtplib import SMTP as SMTPClient
from pytest_mock import MockFixture

from typing import List


class ProxyPeekerHandler(Sink):
    def __init__(self):
        self.called = False
        self.proxy_datas: List[ProxyData] = []
        self.retval = True

    async def handle_PROXY(self, server, session, envelope, proxy_data: ProxyData):
        self.called = True
        self.proxy_datas.append(proxy_data)
        return self.retval


@pytest.fixture
def setup_proxy_protocol(mocker: MockFixture):
    proxy_timeout = 1.0
    responses = []
    transport = mocker.Mock()
    transport.write = responses.append
    old_loop = asyncio.get_event_loop()
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    handler = ProxyPeekerHandler()

    def getter(testcase, *args, **kwargs):
        kwargs["loop"] = new_loop
        kwargs["proxy_protocol_timeout"] = proxy_timeout
        protocol = SMTPServer(handler, *args, **kwargs)
        protocol.connection_made(transport)

        def runner(limit=1.0):
            new_loop.call_later(limit, protocol._handler_coroutine.cancel)
            try:
                new_loop.run_until_complete(protocol._handler_coroutine)
            except asyncio.CancelledError:
                pass

        testcase.protocol = protocol
        testcase.runner = runner
        testcase.transport = transport

    yield getter

    new_loop.close()
    asyncio.set_event_loop(old_loop)


class TestProxyProtocolV1:
    protocol = None
    runner = None
    transport = None

    def test_noproxy(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        data = b"HELO example.org\r\n"
        self.protocol.data_received(data)
        self.runner()
        assert self.transport.close.called

    def _assert_valid(self, ipaddr, proto, srcip, dstip, srcport, dstport, testline):
        self.protocol.data_received(testline.encode("ascii"))
        self.runner()
        handler = self.protocol.event_handler
        assert handler.called
        proxy_data = handler.proxy_datas[0]
        assert proxy_data.check(
            valid=True,
            version=1,
            protocol=proto,
            src_addr=ipaddr(srcip),
            dst_addr=ipaddr(dstip),
            src_port=srcport,
            dst_port=dstport,
        )

    def test_tcp4(self, setup_proxy_protocol):
        srcip = "1.2.3.4"
        dstip = "5.6.7.8"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_valid(
            IPv4Address, b"TCP4", srcip, dstip, srcport, dstport, prox_test
        )

    def test_tcp6_shortened(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_valid(
            IPv6Address, b"TCP6", srcip, dstip, srcport, dstport, prox_test
        )

    def test_tcp6_random(self, setup_proxy_protocol):
        srcip = ":".join(f"{random.getrandbits(16):04x}" for _ in range(0, 8))
        dstip = ":".join(f"{random.getrandbits(16):04x}" for _ in range(0, 8))
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_valid(
            IPv6Address, b"TCP6", srcip, dstip, srcport, dstport, prox_test
        )

    def test_unknown(self, setup_proxy_protocol):
        prox_test = "PROXY UNKNOWN whatever\r\n"
        setup_proxy_protocol(self)
        self.protocol.data_received(prox_test.encode("ascii"))
        self.runner()
        handler = self.protocol.event_handler
        assert handler.called
        proxy_data = handler.proxy_datas[0]
        assert proxy_data.check(
            valid=True,
            version=1,
            protocol=b"UNKNOWN",
            rest=b" whatever",
        )

    def test_unknown_short(self, setup_proxy_protocol):
        prox_test = "PROXY UNKNOWN\r\n"
        setup_proxy_protocol(self)
        self.protocol.data_received(prox_test.encode("ascii"))
        self.runner()
        handler = self.protocol.event_handler
        assert handler.called
        proxy_data = handler.proxy_datas[0]
        assert proxy_data.check(
            valid=True,
            version=1,
            protocol=b"UNKNOWN",
            rest=b"",
        )

    def _assert_invalid(self, testline: str):
        self.protocol.data_received(testline.encode("ascii"))
        self.runner()
        handler = self.protocol.event_handler
        assert not self.protocol._proxy_result.valid
        assert not handler.called
        assert self.transport.close.called

    def test_too_long(self, setup_proxy_protocol):
        prox_test = "PROXY UNKNOWN " + "*" * 100 + "\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_nocr(self, setup_proxy_protocol):
        prox_test = "PROXY UNKNOWN\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_notproxy(self, setup_proxy_protocol):
        srcip = "1.2.3.4"
        dstip = "5.6.7.8"
        srcport = 65535
        dstport = 65535
        prox_test = f"NOTPROX TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_wrongtype_64(self, setup_proxy_protocol):
        srcip = "1.2.3.4"
        dstip = "5.6.7.8"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_wrongtype_46(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_wrongtype_6mixed(self, setup_proxy_protocol):
        srcip = "1.2.3.4"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_zeroleader(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 2501
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} 0{srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_space1(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6  {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_space2(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip}  {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_space3(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport}  {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)

    def test_malformed_space4(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport} \r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test)


@controller_data(proxy_protocol_timeout=1.0)
@handler_data(class_=ProxyPeekerHandler)
class TestProxyProtocolV1Controller:
    def test_timeout(self, plain_controller):
        prox_test = b"PROXY TCP4 255.255.255.255 255.255.255.255 65535 65535\r\n"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(Global.SrvAddr)
            time.sleep(plain_controller.smtpd._proxy_timeout * 1.1)
            with pytest.raises(ConnectionAbortedError):
                sock.send(prox_test)
                _ = sock.recv(4096)

    def test_nonewline(self, plain_controller):
        prox_test = b"PROXY TCP4 255.255.255.255 255.255.255.255 65535 65535\r"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(Global.SrvAddr)
            sock.send(prox_test)
            time.sleep(plain_controller.smtpd._proxy_timeout * 1.1)
            with pytest.raises(ConnectionAbortedError):
                sock.send(b"\n")
                _ = sock.recv(4096)

    def test_okay(self, plain_controller):
        prox_test = b"PROXY TCP4 255.255.255.255 255.255.255.255 65535 65535\r\n"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(Global.SrvAddr)
            sock.sendall(prox_test)
            resp = sock.makefile("rb").readline()
            assert resp.startswith(b"220 ")
            with SMTPClient() as client:
                client.sock = sock
                code, mesg = client.ehlo("example.org")
                assert code == 250
                code, mesg = client.quit()
                assert code == 221
