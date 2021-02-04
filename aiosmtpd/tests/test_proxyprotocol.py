# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import random
import socket
import struct
import time
from ipaddress import IPv4Address, IPv6Address
from smtplib import SMTP as SMTPClient
from typing import List

import pytest
from pytest_mock import MockFixture

from aiosmtpd.handlers import Sink
from aiosmtpd.proxy_protocol import ProxyData
from aiosmtpd.smtp import SMTP as SMTPServer
from aiosmtpd.tests.conftest import Global, controller_data, handler_data


DEFAULT_AUTOCANCEL = 0.1
V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"


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
def setup_proxy_protocol(mocker: MockFixture, temp_event_loop):
    proxy_timeout = 1.0
    responses = []
    transport = mocker.Mock()
    transport.write = responses.append
    handler = ProxyPeekerHandler()
    loop = temp_event_loop

    def getter(
        test_obj: "_TestProxyProtocolCommon", *args, **kwargs
    ):
        kwargs["loop"] = loop
        kwargs["proxy_protocol_timeout"] = proxy_timeout
        protocol = SMTPServer(handler, *args, **kwargs)
        protocol.connection_made(transport)

        def runner(stop_after: float = DEFAULT_AUTOCANCEL):
            loop.call_later(stop_after, protocol._handler_coroutine.cancel)
            try:
                loop.run_until_complete(protocol._handler_coroutine)
            except asyncio.CancelledError:
                pass

        test_obj.protocol = protocol
        test_obj.runner = runner
        test_obj.transport = transport

    yield getter


class _TestProxyProtocolCommon:
    protocol = None
    runner = None
    transport = None


class TestProxyData:
    def test_invalid_version(self):
        pd = ProxyData(version=None)
        assert not pd.valid
        assert not pd

    def test_invalid_error(self):
        pd = ProxyData(version=1)
        pd.error = "SomeError"
        assert not pd.valid
        assert not pd

    def test_invalid_protocol(self):
        pd = ProxyData(version=1)
        assert not pd.valid
        assert not pd

    def test_mismatch(self):
        pd = ProxyData(version=1)
        pd.protocol = "UNKNOWN"
        assert pd.valid
        assert pd
        assert not pd.check(protocol="DIFFERENT")

    def test_unsetkey(self):
        pd = ProxyData(version=1)
        pd.protocol = "UNKNOWN"
        assert pd.valid
        assert pd
        assert not pd.check(src_addr="Missing")

    def test_unknownkey(self):
        pd = ProxyData(version=1)
        pd.protocol = "UNKNOWN"
        assert pd.valid
        assert pd
        assert not pd.check(strange_key="Unrecognized")


class TestProxyProtocolV1(_TestProxyProtocolCommon):
    def test_noproxy(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        data = b"HELO example.org\r\n"
        self.protocol.data_received(data)
        self.runner()
        assert self.transport.close.called

    def _assert_valid(self, ipaddr, proto, srcip, dstip, srcport, dstport, testline):
        self.protocol.data_received(testline.encode("ascii"))
        self.runner()
        assert self.protocol._proxy_result.error == ""
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
        srcport = 0
        dstport = 65535
        prox_test = f"PROXY TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_valid(
            IPv4Address, b"TCP4", srcip, dstip, srcport, dstport, prox_test
        )

    def test_tcp6_shortened(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 8000
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

    def _assert_invalid(self, testline: str, expect_err: str = ""):
        self.protocol.data_received(testline.encode("ascii"))
        self.runner()
        handler = self.protocol.event_handler
        assert not self.protocol._proxy_result.valid
        assert not handler.called
        assert self.transport.close.called
        assert self.protocol._proxy_result.error == expect_err

    def test_too_long(self, setup_proxy_protocol):
        prox_test = "PROXY UNKNOWN " + "*" * 100 + "\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 too long")

    def test_malformed_nocr(self, setup_proxy_protocol):
        prox_test = "PROXY UNKNOWN\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 malformed")

    def test_malformed_notproxy(self, setup_proxy_protocol):
        srcip = "1.2.3.4"
        dstip = "5.6.7.8"
        srcport = 65535
        dstport = 65535
        prox_test = f"NOTPROX TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXY unrecognized signature")

    def test_malformed_wrongtype_64(self, setup_proxy_protocol):
        srcip = "1.2.3.4"
        dstip = "5.6.7.8"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address not IPv6")

    def test_malformed_wrongtype_46(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address not IPv4")

    def test_malformed_wrongtype_6mixed(self, setup_proxy_protocol):
        srcip = "1.2.3.4"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address not IPv6")

    def test_malformed_zeroleader(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 2501
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} 0{srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address malformed")

    def test_malformed_space1(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6  {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address malformed")

    def test_malformed_space2(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip}  {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address malformed")

    def test_malformed_space3(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport}  {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address malformed")

    def test_malformed_space4(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport} \r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address malformed")

    def test_malformed_addr4(self, setup_proxy_protocol):
        srcip = "1.2.3.a"
        dstip = "5.6.7.8"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address parse error")

    def test_malformed_addr6(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::000g"
        srcport = 65535
        dstport = 65535
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport} \r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 address malformed")

    def test_ports_oob(self, setup_proxy_protocol):
        srcip = "1.2.3.4"
        dstip = "5.6.7.8"
        srcport = 65536
        dstport = 10200
        prox_test = f"PROXY TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 src port out of bounds")

    def test_portd_oob(self, setup_proxy_protocol):
        srcip = "2020:dead::0001"
        dstip = "2021:cafe::0002"
        srcport = 10000
        dstport = 65536
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
        setup_proxy_protocol(self)
        self._assert_invalid(prox_test, "PROXYv1 dst port out of bounds")


class TestProxyProtocolV2(_TestProxyProtocolCommon):
    def _send_valid(self, cmd: int, fam: int, proto: int, payload: bytes) -> ProxyData:
        ver_cmd = 0x20 + cmd
        fam_pro = (fam << 4) + proto
        self.protocol.data_received(
            V2_SIGNATURE
            + ver_cmd.to_bytes(1, "big")
            + fam_pro.to_bytes(1, "big")
            + len(payload).to_bytes(2, "big")
            + payload
        )
        self.runner()
        assert self.protocol._proxy_result.error == ""
        handler = self.protocol.event_handler
        assert handler.called
        return handler.proxy_datas[0]

    def test_UNSPEC_empty(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        self._send_valid(0, 0, 0, b"").check(
            valid=True, version=2, command=0, family=0, protocol=0, rest=b""
        )

    def test_UNSPEC_notempty(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        payload = b"asdfghjkl"
        self._send_valid(0, 0, 0, payload).check(
            valid=True, version=2, command=0, family=0, protocol=0, rest=payload
        )

    @pytest.mark.parametrize("ttlv", [b"", b"fake_tlv"])
    @pytest.mark.parametrize("tproto", [1, 2])
    def test_INET4(self, setup_proxy_protocol, tproto, ttlv):
        setup_proxy_protocol(self)
        src_addr = IPv4Address("10.212.4.33")
        dst_addr = IPv4Address("10.11.12.13")
        src_port = 0
        dst_port = 65535
        payload = (
            src_addr.packed
            + dst_addr.packed
            + src_port.to_bytes(2, "big")
            + dst_port.to_bytes(2, "big")
            + ttlv
        )
        self._send_valid(0, 1, tproto, payload).check(
            valid=True,
            version=2,
            command=0,
            family=1,
            protocol=1,
            src_addr=src_addr,
            dst_addr=dst_addr,
            src_port=src_port,
            dst_port=dst_port,
            rest=ttlv,
        )

    @pytest.mark.parametrize("ttlv", [b"", b"fake_tlv"])
    @pytest.mark.parametrize("tproto", [1, 2])
    def test_INET6(self, setup_proxy_protocol, tproto, ttlv):
        setup_proxy_protocol(self)
        src_addr = IPv6Address("2020:dead::0001")
        dst_addr = IPv6Address("2021:cafe::0022")
        src_port = 65534
        dst_port = 8080
        payload = (
            src_addr.packed
            + dst_addr.packed
            + src_port.to_bytes(2, "big")
            + dst_port.to_bytes(2, "big")
            + ttlv
        )
        self._send_valid(0, 2, tproto, payload).check(
            valid=True,
            version=2,
            command=0,
            family=2,
            protocol=tproto,
            src_addr=src_addr,
            dst_addr=dst_addr,
            src_port=src_port,
            dst_port=dst_port,
            rest=ttlv,
        )

    @pytest.mark.parametrize("ttlv", [b"", b"fake_tlv"])
    @pytest.mark.parametrize("tproto", [1, 2])
    def test_UNIX(self, setup_proxy_protocol, tproto, ttlv):
        setup_proxy_protocol(self)
        src_addr = struct.pack("108s", b"/proc/source")
        dst_addr = struct.pack("108s", b"/proc/dest")
        payload = src_addr + dst_addr + ttlv
        self._send_valid(0, 3, tproto, payload).check(
            valid=True,
            version=2,
            command=0,
            family=3,
            protocol=tproto,
            src_addr=src_addr,
            dst_addr=dst_addr,
            src_port=None,
            dst_port=None,
            rest=ttlv,
        )

    @pytest.mark.parametrize("tfam, tproto", [(0, 1), (0, 2), (1, 0), (2, 0), (3, 0)])
    def test_fallback_UNSPEC(self, setup_proxy_protocol, tfam, tproto):
        setup_proxy_protocol(self)
        payload = b"whatever"
        self._send_valid(0, tfam, tproto, payload).check(
            valid=True,
            version=2,
            command=0,
            family=tfam,
            protocol=tproto,
            src_address=None,
            dst_address=None,
            src_port=None,
            dst_port=None,
            rest=payload,
        )

    def _send_invalid(
        self,
        sig: bytes = V2_SIGNATURE,
        ver: int = 2,
        cmd: int = 0,
        fam: int = 0,
        proto: int = 0,
        payload: bytes = b"",
        expect: str = None,
    ) -> ProxyData:
        ver_cmd = (ver << 4) | cmd
        fam_pro = (fam << 4) | proto
        self.protocol.data_received(
            sig
            + ver_cmd.to_bytes(1, "big")
            + fam_pro.to_bytes(1, "big")
            + len(payload).to_bytes(2, "big")
            + payload
        )
        self.runner()
        handler = self.protocol.event_handler
        assert not handler.called
        assert not self.protocol._proxy_result
        if expect is not None:
            assert self.protocol._proxy_result.error == expect
        return self.protocol._proxy_result

    def test_invalid_sig(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        ERRSIG = b"\r\n\r\n\x00\r\nQUIP\n"
        self._send_invalid(sig=ERRSIG, expect="PROXYv2 wrong signature")

    def test_incomplete(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        ERRSIG = b"\r\n\r\n\x00\r\nQUIT\n"
        self.protocol.data_received(ERRSIG + b"\x20" + b"\x00" + b"\x00")
        self.runner()
        assert self.transport.close.called
        handler = self.protocol.event_handler
        assert not handler.called
        assert not self.protocol._proxy_result.valid
        assert not self.protocol._proxy_result
        assert self.protocol._proxy_result.error == "PROXYv2 malformed header"

    def test_illegal_ver(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        self._send_invalid(ver=3, expect="PROXYv2 illegal version")

    def test_unsupported_cmd(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        self._send_invalid(cmd=2, expect="PROXYv2 unsupported command")

    def test_unsupported_fam(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        self._send_invalid(fam=4, expect="PROXYv2 unsupported family")

    def test_unsupported_proto(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        self._send_invalid(proto=3, expect="PROXYv2 unsupported protocol")

    def test_wrong_proto_6shouldbe4(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        src_addr = IPv4Address("192.168.0.11")
        dst_addr = IPv4Address("172.16.0.22")
        src_port = 65534
        dst_port = 8080
        payload = (
            src_addr.packed
            + dst_addr.packed
            + src_port.to_bytes(2, "big")
            + dst_port.to_bytes(2, "big")
        )
        self._send_invalid(
            fam=2, proto=1, payload=payload, expect="PROXYv2 truncated address"
        )


@controller_data(proxy_protocol_timeout=0.3)
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