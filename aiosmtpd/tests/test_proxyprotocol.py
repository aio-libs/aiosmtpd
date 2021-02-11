# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
import operator
import random
import socket
import struct
import time
from base64 import b64decode
from contextlib import contextmanager
from functools import partial
from ipaddress import IPv4Address, IPv6Address
from smtplib import SMTP as SMTPClient
from smtplib import SMTPServerDisconnected
from typing import Any, Dict, List

import pytest
from pytest_mock import MockFixture

from aiosmtpd.handlers import Sink
from aiosmtpd.proxy_protocol import (
    V2_CMD,
    V2_FAM,
    V2_PRO,
    V2_SIGNATURE,
    MalformedTLV,
    ProxyData,
    ProxyTLV,
    UnknownTypeTLV,
)
from aiosmtpd.smtp import SMTP as SMTPServer
from aiosmtpd.smtp import Session as SMTPSession
from aiosmtpd.tests.conftest import Global, controller_data, handler_data

DEFAULT_AUTOCANCEL = 0.1
TIMEOUT_MULTIPLIER = 1.5

param = pytest.param
parametrize = pytest.mark.parametrize
random_port = partial(random.getrandbits, 16)

TEST_TLV_DATA_1 = (
    b"\x03\x00\x04Z\xfd\xc6\xff\x02\x00\tAUTHORITI\x05\x00\tUNIKUE_ID"
    b"\x20\x00D\x01\x00\x00\x00\x00!\x00\x07TLSv1.3%\x00\x07RSA4096"
    b"$\x00\nRSA-SHA256#\x00\x1bECDHE-RSA-AES256-CBC-SHA384"
)

TEST_TLV_DATA_2 = (
    b"\x03\x00\x04Z\xfd\xc6\xff\x02\x00\tAUTHORIT2\x05\x00\tUNIQUEID2"
    b"\x20\x00D\x01\x00\x00\x00\x00!\x00\x07TLSv1.3%\x00\x07RSA4096"
    b"$\x00\nRSA-SHA256#\x00\x1bECDHE-RSA-AES256-CBC-SHA384"
    b"\x30\x00\x09something"
)

# This has a tail which is not part of PROXYv2
TEST_V2_DATA1_XTRA = b64decode(
    "DQoNCgANClFVSVQKIREAcn8AAAF/AAABsFJipQMABFT9xv8CAAlBVVRIT1JJVFkFAAlVTklRVUVf\n"
    "SUQgAEQBAAAAACEAB1RMU3YxLjIlAAdSU0E0MDk2JAAKUlNBLVNIQTI1NiMAG0VDREhFLVJTQS1B\n"
    "RVMyNTYtR0NNLVNIQTM4NFRlc3QgZGF0YSB0aGF0IGlzIG5vdCBwYXJ0IG9mIFBST1hZdjIuCg==\n"
)

# The extra part is:
# b"Test data that is not part of PROXYv2.\n"

# This same as the above but no extraneous tail
TEST_V2_DATA1_EXACT = b64decode(
    "DQoNCgANClFVSVQKIREAcn8AAAF/AAABsFJipQMABFT9xv8CAAlBVVRIT1JJVFkFAAlVTklRVUVf\n"
    "SUQgAEQBAAAAACEAB1RMU3YxLjIlAAdSU0E0MDk2JAAKUlNBLVNIQTI1NiMAG0VDREhFLVJTQS1B\n"
    "RVMyNTYtR0NNLVNIQTM4NA==\n"
)


class ProxyPeekerHandler(Sink):
    def __init__(self, retval=True):
        self.called = False
        self.sessions: List[SMTPSession] = []
        self.proxy_datas: List[ProxyData] = []
        self.retval = retval

    async def handle_PROXY(self, server, session, envelope):
        self.called = True
        self.sessions.append(session)
        self.proxy_datas.append(session.proxy_data)
        return self.retval


@contextmanager
def does_not_raise():
    yield


@pytest.fixture
def setup_proxy_protocol(mocker: MockFixture, temp_event_loop):
    proxy_timeout = 1.0
    responses = []
    transport = mocker.Mock()
    transport.write = responses.append
    handler = ProxyPeekerHandler()
    loop = temp_event_loop

    def getter(test_obj: "_TestProxyProtocolCommon", *args, **kwargs):
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
    protocol: SMTPServer = None
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
        assert not pd.same_attribs(protocol="DIFFERENT")

    def test_unsetkey(self):
        pd = ProxyData(version=1)
        pd.protocol = "UNKNOWN"
        assert pd.valid
        assert pd
        assert not pd.same_attribs(src_addr="Missing")

    def test_unknownkey(self):
        pd = ProxyData(version=1)
        pd.protocol = "UNKNOWN"
        assert pd.valid
        assert pd
        assert not pd.same_attribs(strange_key="Unrecognized")

    def test_tlv_none(self):
        pd = ProxyData(version=2)
        assert pd.tlv is None

    def test_tlv_fake(self):
        pd = ProxyData(version=2)
        pd.rest = b"fake_tlv"  # Must be something that fails parsing in ProxyTLV
        assert pd.tlv is None

    def test_tlv_1(self):
        pd = ProxyData(version=2)
        pd.rest = TEST_TLV_DATA_1
        assert pd.tlv is not None
        assert pd.tlv.same_attribs(
            AUTHORITY=b"AUTHORITI",
            CRC32C=b"Z\xfd\xc6\xff",
            UNIQUE_ID=b"UNIKUE_ID",
            SSL=True,
            SSL_VERSION=b"TLSv1.3",
            SSL_CIPHER=b"ECDHE-RSA-AES256-CBC-SHA384",
            SSL_SIG_ALG=b"RSA-SHA256",
            SSL_KEY_ALG=b"RSA4096",
        )


class TestProxyTLV:
    def test_1(self):
        ptlv = ProxyTLV.from_raw(TEST_TLV_DATA_1)
        assert "ALPN" not in ptlv
        assert "NOOP" not in ptlv
        assert "SSL_CN" not in ptlv
        assert "NETNS" not in ptlv
        assert ptlv.ALPN is None
        assert ptlv.AUTHORITY == b"AUTHORITI"
        assert ptlv.same_attribs(
            AUTHORITY=b"AUTHORITI",
            CRC32C=b"Z\xfd\xc6\xff",
            UNIQUE_ID=b"UNIKUE_ID",
            SSL=True,
            SSL_VERSION=b"TLSv1.3",
            SSL_CIPHER=b"ECDHE-RSA-AES256-CBC-SHA384",
            SSL_SIG_ALG=b"RSA-SHA256",
            SSL_KEY_ALG=b"RSA4096",
        )
        assert not ptlv.same_attribs(SSL=False)
        assert not ptlv.same_attribs(false_attrib=None)

    def test_2(self):
        ptlv = ProxyTLV.from_raw(TEST_TLV_DATA_2)
        assert "ALPN" not in ptlv
        assert "NOOP" not in ptlv
        assert "SSL_CN" not in ptlv
        assert "NETNS" in ptlv
        assert ptlv.ALPN is None
        assert ptlv.AUTHORITY == b"AUTHORIT2"
        assert ptlv.UNIQUE_ID == b"UNIQUEID2"
        assert ptlv.same_attribs(
            CRC32C=b"Z\xfd\xc6\xff",
            UNIQUE_ID=b"UNIQUEID2",
            SSL=True,
            SSL_VERSION=b"TLSv1.3",
            SSL_CIPHER=b"ECDHE-RSA-AES256-CBC-SHA384",
            SSL_SIG_ALG=b"RSA-SHA256",
            SSL_KEY_ALG=b"RSA4096",
            NETNS=b"something",
        )
        assert not ptlv.same_attribs(false_attrib=None)
        with pytest.raises(ValueError, match=r"mismatch:SSL"):
            ptlv.same_attribs(SSL=False, _raises=True)

    @parametrize(
        "typeint, typename",
        [
            (0x01, "ALPN"),
            (0x02, "AUTHORITY"),
            (0x03, "CRC32C"),
            (0x04, "NOOP"),
            (0x05, "UNIQUE_ID"),
            (0x20, "SSL"),
            (0x21, "SSL_VERSION"),
            (0x22, "SSL_CN"),
            (0x23, "SSL_CIPHER"),
            (0x24, "SSL_SIG_ALG"),
            (0x25, "SSL_KEY_ALG"),
            (0x30, "NETNS"),
            (None, "wrongname"),
        ],
    )
    def test_backmap(self, typename, typeint):
        assert ProxyTLV.name_to_num(typename) == typeint

    def test_parse_partial(self):
        TEST_TLV_DATA_1_TRUNCATED = (
            b"\x03\x00\x04Z\xfd\xc6\xff\x02\x00\tAUTHORITI\x05\x00\tUNIKUE_I"
        )
        rslt: Dict[str, Any]
        rslt, _ = ProxyTLV.parse(TEST_TLV_DATA_1_TRUNCATED)
        assert isinstance(rslt, dict)
        assert "CRC32C" in rslt

    def test_unknowntype_notstrict(self):
        test_data = b"\xFF\x00\x04yeah"
        rslt: Dict[str, Any]
        rslt, _ = ProxyTLV.parse(test_data, strict=False)
        assert "xFF" in rslt
        assert rslt["xFF"] == b"yeah"

    def test_unknowntype_strict(self):
        test_data = b"\xFF\x00\x04yeah"
        with pytest.raises(UnknownTypeTLV):
            _ = ProxyTLV.parse(test_data, strict=True)

    def test_malformed_ssl_partialok(self):
        test_data = (
            b"\x20\x00\x17\x01\x02\x03\x04\x05\x21\x00\x07version\x22\x00\x09trunc"
        )
        rslt: Dict[str, Any]
        rslt, _ = ProxyTLV.parse(test_data, partial_ok=True)
        assert rslt["SSL"] is False
        assert rslt["SSL_VERSION"] == b"version"
        assert "SSL_CN" not in rslt

    def test_malformed_ssl_notpartialok(self):
        test_data = (
            b"\x20\x00\x0D\x01\x02\x03\x04\x05\x21\x00\x07version\x22\x00\x09trunc"
        )
        with pytest.raises(MalformedTLV):
            _ = ProxyTLV.parse(test_data, partial_ok=False)

    def test_eq(self):
        data1 = b"\x03\x00\x04Z\xfd\xc6\xff\x02\x00\tAUTHORITI"
        ptlv1 = ProxyTLV.from_raw(data1)
        data2 = b"\x02\x00\tAUTHORITI\x03\x00\x04Z\xfd\xc6\xff"
        ptlv2 = ProxyTLV.from_raw(data2)
        assert ptlv1 is not ptlv2
        assert ptlv1 == ptlv2


class TestProxyProtocolInit:
    @parametrize("value", [int(-1), float(-1.0), int(0), float(0.0)])
    def test_value_error(self, temp_event_loop, value):
        with pytest.raises(ValueError, match=r"proxy_protocol_timeout must be > 0"):
            _ = SMTPServer(Sink(), proxy_protocol_timeout=value, loop=temp_event_loop)

    def test_lt_3(self, caplog, temp_event_loop):
        _ = SMTPServer(Sink(), proxy_protocol_timeout=1, loop=temp_event_loop)
        expect = ("mail.log", logging.WARNING, "proxy_protocol_timeout < 3.0")
        assert expect in caplog.record_tuples

    @parametrize("value", [int(3), float(3.0), int(4), float(4.0)])
    def test_ge_3(self, caplog, temp_event_loop, value):
        _ = SMTPServer(Sink(), proxy_protocol_timeout=value, loop=temp_event_loop)
        expect = ("mail.log", logging.WARNING, "proxy_protocol_timeout < 3.0")
        assert expect not in caplog.record_tuples


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
        assert self.protocol.session.proxy_data.error == ""
        handler = self.protocol.event_handler
        assert handler.called
        proxy_data = handler.proxy_datas[-1]
        assert proxy_data.same_attribs(
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

    def test_tcp4_random(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        srcip = ".".join(f"{random.getrandbits(8)}" for _ in range(0, 4))
        dstip = ".".join(f"{random.getrandbits(8)}" for _ in range(0, 4))
        srcport = random_port()
        dstport = random_port()
        prox_test = f"PROXY TCP4 {srcip} {dstip} {srcport} {dstport}\r\n"
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
        srcport = random_port()
        dstport = random_port()
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
        assert proxy_data.same_attribs(
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
        assert proxy_data.same_attribs(
            valid=True,
            version=1,
            protocol=b"UNKNOWN",
            rest=b"",
        )

    def _assert_invalid(self, testline: str, expect_err: str = ""):
        self.protocol.data_received(testline.encode("ascii"))
        self.runner()
        handler: ProxyPeekerHandler = self.protocol.event_handler
        assert not self.protocol.session.proxy_data.valid
        assert not handler.called
        assert self.transport.close.called
        assert self.protocol.session.proxy_data.error == expect_err

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

    IP6_dead = "2020:dead::0001"
    IP6_cafe = "2021:cafe::0002"

    @parametrize(
        "srcip, dstip, srcport, dstport",
        [
            param(IP6_dead, IP6_cafe, "02501", None, id="zeroleader"),
            param(" " + IP6_dead, IP6_cafe, None, None, id="space1"),
            param(IP6_dead, " " + IP6_cafe, None, None, id="space2"),
            param(IP6_dead, IP6_cafe, " 8080", None, id="space3"),
            param(IP6_dead, IP6_cafe, None, " 0", id="space4"),
            param(IP6_dead, IP6_cafe, None, "0 ", id="space5"),
            param(IP6_dead[:-1] + "g", IP6_cafe, None, None, id="addr6s"),
            param(IP6_dead, IP6_cafe[:-1] + "h", None, None, id="addr6d"),
        ],
    )
    def test_malformed_addr(self, setup_proxy_protocol, srcip, dstip, srcport, dstport):
        if srcport is None:
            srcport = random_port()
        if dstport is None:
            dstport = random_port()
        prox_test = f"PROXY TCP6 {srcip} {dstip} {srcport} {dstport}\r\n"
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
    def test_1(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        self.protocol.data_received(TEST_V2_DATA1_XTRA)
        self.runner()
        sess: SMTPSession = self.protocol.session
        assert sess.proxy_data.error == ""
        handler: ProxyPeekerHandler = self.protocol.event_handler
        assert handler.called
        pd: ProxyData = handler.proxy_datas[-1]
        assert pd.same_attribs(
            version=2,
            command=1,
            family=1,
            src_addr=IPv4Address("127.0.0.1"),
            dst_addr=IPv4Address("127.0.0.1"),
            src_port=45138,
            dst_port=25253,
        )
        assert b"not part of PROXYv2" not in pd.rest
        assert pd.tlv
        assert pd.tlv.same_attribs(
            AUTHORITY=b"AUTHORITY",
            CRC32C=b"T\xfd\xc6\xff",
            UNIQUE_ID=b"UNIQUE_ID",
            SSL=True,
            SSL_VERSION=b"TLSv1.2",
            SSL_CIPHER=b"ECDHE-RSA-AES256-GCM-SHA384",
            SSL_SIG_ALG=b"RSA-SHA256",
            SSL_KEY_ALG=b"RSA4096",
        )

    def _send_valid(
        self, cmd: V2_CMD, fam: V2_FAM, proto: V2_PRO, payload: bytes
    ) -> ProxyData:
        ver_cmd = 0x20 + cmd.value
        fam_pro = (fam << 4) + proto
        to_send = bytes(
            V2_SIGNATURE
            + ver_cmd.to_bytes(1, "big")
            + fam_pro.to_bytes(1, "big")
            + len(payload).to_bytes(2, "big")
            + payload
        )
        self.protocol.data_received(to_send)
        self.runner()
        assert self.protocol.session.proxy_data.error == ""
        assert self.protocol.session.proxy_data.whole_raw == to_send
        handler = self.protocol.event_handler
        assert handler.called
        return handler.proxy_datas[-1]

    def test_UNSPEC_empty(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        assert self._send_valid(
            V2_CMD.LOCAL, V2_FAM.UNSPEC, V2_PRO.UNSPEC, b""
        ).same_attribs(valid=True, version=2, command=0, family=0, protocol=0, rest=b"")

    def test_UNSPEC_notempty(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        payload = b"asdfghjkl"
        assert self._send_valid(
            V2_CMD.LOCAL, V2_FAM.UNSPEC, V2_PRO.UNSPEC, payload
        ).same_attribs(
            valid=True, version=2, command=0, family=0, protocol=0, rest=payload
        )

    @parametrize("ttlv", [b"", b"fake_tlv"])
    @parametrize("tproto", [V2_PRO.STREAM, V2_PRO.DGRAM])
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
        pd = self._send_valid(V2_CMD.LOCAL, V2_FAM.IP4, tproto, payload)
        assert pd.same_attribs(
            valid=True,
            version=2,
            command=0,
            family=V2_FAM.IP4,
            protocol=tproto,
            src_addr=src_addr,
            dst_addr=dst_addr,
            src_port=src_port,
            dst_port=dst_port,
            rest=ttlv,
        )
        assert pd.tlv is None

    @parametrize("ttlv", [b"", b"fake_tlv"])
    @parametrize("tproto", [V2_PRO.STREAM, V2_PRO.DGRAM])
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
        pd = self._send_valid(V2_CMD.LOCAL, V2_FAM.IP6, tproto, payload)
        assert pd.same_attribs(
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
        assert pd.tlv is None

    @parametrize("ttlv", [b"", b"fake_tlv"])
    @parametrize("tproto", [V2_PRO.STREAM, V2_PRO.DGRAM])
    def test_UNIX(self, setup_proxy_protocol, tproto, ttlv):
        setup_proxy_protocol(self)
        src_addr = struct.pack("108s", b"/proc/source")
        dst_addr = struct.pack("108s", b"/proc/dest")
        payload = src_addr + dst_addr + ttlv
        pd = self._send_valid(V2_CMD.LOCAL, V2_FAM.UNIX, tproto, payload)
        assert pd.same_attribs(
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
        assert pd.tlv is None

    @parametrize(
        "tfam, tproto",
        [
            (V2_FAM.UNSPEC, V2_PRO.STREAM),
            (V2_FAM.UNSPEC, V2_PRO.DGRAM),
            (V2_FAM.IP4, V2_PRO.UNSPEC),
            (V2_FAM.IP6, V2_PRO.UNSPEC),
            (V2_FAM.UNIX, V2_PRO.UNSPEC),
        ],
    )
    def test_fallback_UNSPEC(self, setup_proxy_protocol, tfam, tproto):
        setup_proxy_protocol(self)
        payload = b"whatever"
        assert self._send_valid(V2_CMD.LOCAL, tfam, tproto, payload).same_attribs(
            valid=True,
            version=2,
            command=0,
            family=tfam,
            protocol=tproto,
            src_addr=None,
            dst_addr=None,
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
        assert not self.protocol.session.proxy_data
        if expect is not None:
            assert self.protocol.session.proxy_data.error == expect
        return self.protocol.session.proxy_data

    def test_invalid_sig(self, setup_proxy_protocol):
        setup_proxy_protocol(self)
        ERRSIG = b"\r\n\r\n\x00\r\nQUIP\n"
        self._send_invalid(sig=ERRSIG, expect="PROXYv2 wrong signature")

    # Using readexactly() instead of just read() causes incomplete handshake to time-
    # out as readexactly() waits until the number of bytes is complete.
    # So, we can no longer look for this error.
    #
    # def test_incomplete(self, setup_proxy_protocol):
    #     setup_proxy_protocol(self)
    #     self.protocol.data_received(V2_SIGNATURE + b"\x20" + b"\x00" + b"\x00")
    #     self.runner()
    #     assert self.transport.close.called
    #     handler = self.protocol.event_handler
    #     assert not handler.called
    #     sess: SMTPSession = self.protocol.session
    #     assert not sess.proxy_data.valid
    #     assert sess.proxy_data.error == "PROXYv2 malformed header"

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
class TestWithController:
    GOOD_V1_HANDSHAKE = b"PROXY TCP4 255.255.255.255 255.255.255.255 65535 65535\r\n"

    HANDSHAKES = {
        "v1": GOOD_V1_HANDSHAKE,
        "v2": TEST_V2_DATA1_EXACT,
    }

    def _okay(self, handshake: bytes):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(Global.SrvAddr)
            sock.sendall(handshake)
            resp = sock.makefile("rb").readline()
            assert resp.startswith(b"220 ")
            with SMTPClient() as client:
                client.sock = sock
                code, mesg = client.ehlo("example.org")
                assert code == 250
                code, mesg = client.quit()
                assert code == 221

    @parametrize("handshake", HANDSHAKES.values(), ids=HANDSHAKES.keys())
    def test_okay(self, plain_controller, handshake):
        assert plain_controller.smtpd._proxy_timeout > 0.0
        self._okay(handshake)

    @parametrize("handshake", HANDSHAKES.values(), ids=HANDSHAKES.keys())
    def test_hiccup(self, plain_controller, handshake):
        assert plain_controller.smtpd._proxy_timeout > 0.0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(Global.SrvAddr)
            sock.sendall(handshake[0:20])
            time.sleep(0.01)
            sock.sendall(handshake[20:])
            resp = sock.makefile("rb").readline()
            assert resp.startswith(b"220 ")
            with SMTPClient() as client:
                client.sock = sock
                code, mesg = client.ehlo("example.org")
                assert code == 250
                code, mesg = client.quit()
                assert code == 221

    @parametrize("handshake", HANDSHAKES.values(), ids=HANDSHAKES.keys())
    def test_timeout(self, plain_controller, handshake):
        assert plain_controller.smtpd._proxy_timeout > 0.0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(Global.SrvAddr)
            time.sleep(plain_controller.smtpd._proxy_timeout * TIMEOUT_MULTIPLIER)
            # noinspection PyTypeChecker
            with pytest.raises(ConnectionError):
                sock.send(handshake)
                resp = sock.recv(4096)
                if resp == b"":
                    raise ConnectionError
            # Try resending the handshake. Should also fail (because connection has
            # been closed by the server.
            # noinspection PyTypeChecker
            with pytest.raises(ConnectionError):
                sock.send(handshake)
                resp = sock.recv(4096)
                if resp == b"":
                    raise ConnectionAbortedError
        # Assert that we can connect properly afterwards (that is, server is not
        # terminated)
        self._okay(handshake)

    @parametrize("handshake", HANDSHAKES.values(), ids=HANDSHAKES.keys())
    def test_incomplete(self, plain_controller, handshake):
        assert plain_controller.smtpd._proxy_timeout > 0.0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(Global.SrvAddr)
            sock.send(handshake[:-1])
            time.sleep(plain_controller.smtpd._proxy_timeout * TIMEOUT_MULTIPLIER)
            # noinspection PyTypeChecker
            with pytest.raises(ConnectionError):
                sock.send(b"\n")
                resp = sock.recv(4096)  # On Windows, this line raises
                if resp == b"":  # On Linux, no raise, just "EOF"
                    raise ConnectionError
            # Try resending the handshake. Should also fail (because connection has
            # been closed by the server.
            # noinspection PyTypeChecker
            with pytest.raises(ConnectionError):
                sock.send(handshake)
                resp = sock.recv(4096)
                if resp == b"":
                    raise ConnectionError
        # Assert that we can connect properly afterwards (that is, server is not
        # terminated)
        self._okay(handshake)


@controller_data(proxy_protocol_timeout=0.3)
@handler_data(class_=ProxyPeekerHandler)
class TestHandlerAcceptReject:
    # We test *both* Accept *and* reject to ensure that the handshakes are valid
    @parametrize("handler_retval", [True, False])
    @parametrize(
        "handshake",
        [
            b"PROXY TCP4 255.255.255.255 255.255.255.255 65535 65535\r\n",
            TEST_V2_DATA1_EXACT,
        ],
        ids=["v1", "v2"],
    )
    def test_simple(self, plain_controller, handshake, handler_retval):
        assert plain_controller.smtpd._proxy_timeout > 0.0
        assert isinstance(plain_controller.handler, ProxyPeekerHandler)
        plain_controller.handler.retval = handler_retval
        if handler_retval:
            oper = operator.ne
            # See "Parametrizing conditional raising" in
            # https://docs.pytest.org/en/stable/example/parametrize.html
            expect = does_not_raise()
        else:
            oper = operator.eq
            expect = pytest.raises(SMTPServerDisconnected)
        oper = operator.ne if handler_retval else operator.eq
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(Global.SrvAddr)
            sock.sendall(handshake)
            resp = sock.recv(4096)
            assert oper(resp, b"")
            with expect:
                with SMTPClient() as client:
                    client.sock = sock
                    code, mesg = client.ehlo("example.org")
                    assert code == 250
