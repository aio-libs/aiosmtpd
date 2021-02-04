# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import re
import struct
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import AnyStr, Awaitable, Optional, Union

import attr
from public import public

try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol


__ALL__ = ["INVALID_PROXY"]  # Will be added to by @public

V1_VALID_PROS = {"TCP4", "TCP6", "UNKNOWN", b"TCP4", b"TCP6", b"UNKNOWN"}

V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"

V2_CMD_LOCAL = 0
V2_CMD_PROXY = 0

V2_FAM_UNSPEC = 0
V2_FAM_IP4 = 1
V2_FAM_IP6 = 2
V2_FAM_UNIX = 3

V2_PRO_UNSPEC = 0
V2_PRO_STREAM = 1
V2_PRO_DGRAM = 2

V2_VALID_CMDS = {V2_CMD_LOCAL, V2_CMD_PROXY}
V2_VALID_FAMS = {V2_FAM_UNSPEC, V2_FAM_IP4, V2_FAM_IP6, V2_FAM_UNIX}
V2_VALID_PROS = {V2_PRO_UNSPEC, V2_PRO_STREAM, V2_PRO_DGRAM}
V2_PARSE_ADDR_FAMPRO = {
    (V2_FAM_IP4 << 4) | V2_PRO_STREAM,
    (V2_FAM_IP4 << 4) | V2_PRO_DGRAM,
    (V2_FAM_IP6 << 4) | V2_PRO_STREAM,
    (V2_FAM_IP6 << 4) | V2_PRO_DGRAM,
    (V2_FAM_UNIX << 4) | V2_PRO_STREAM,
    (V2_FAM_UNIX << 4) | V2_PRO_DGRAM,
}

# region #### Custom Types ############################################################

EndpointAddress = Union[IPv4Address, IPv6Address, AnyStr]


@public
class AsyncReader(Protocol):  # pragma: nocover
    def read(self, num_bytes: Optional[int] = None) -> Awaitable[bytes]:
        ...

    def readexactly(self, n: int) -> Awaitable[bytes]:
        ...

    def readuntil(self, until_chars: Optional[bytes] = None) -> Awaitable[bytes]:
        ...


@public
@attr.s(slots=True, auto_attribs=True)
class ProxyData:
    version: Optional[int] = attr.ib(kw_only=True)
    error: str = ""
    src_addr: Optional[EndpointAddress] = None
    dst_addr: Optional[EndpointAddress] = None
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    rest: Union[bytes, bytearray] = b""
    family: Optional[int] = None
    protocol: Optional[Union[int, AnyStr]] = None
    command: Optional[int] = None

    @property
    def valid(self) -> bool:
        return not (self.error or self.version is None or self.protocol is None)

    def with_error(self, error_msg: str) -> "ProxyData":
        self.error = error_msg
        return self

    def check(self, **kwargs) -> bool:
        for k, v in kwargs.items():
            try:
                if getattr(self, k) != v:
                    return False
            except AttributeError:
                return False
        return True

    def __bool__(self):
        return self.valid


# endregion


RE_PROXYv1 = re.compile(br"PROXY (?P<proto>TCP4\b|TCP6\b|UNKNOWN)(?P<rest>.*)\r\n")
RE_PROXYv1_ADDR = re.compile(
    # Every piece below MUST start with b" "
    br" (?P<srcip>[0-9a-fA-F.:]+)"  # Validation done by ipaddress.ip_address
    br" (?P<dstip>[0-9a-fA-F.:]+)"
    br" (?P<srcport>[1-9]\d{0,4}|0)"  # 1-5 digits not starting with 0, or 0
    br" (?P<dstport>[1-9]\d{0,4}|0)"
    br"$"
)

# Reference: https://github.com/haproxy/haproxy/blob/v2.3.0/doc/proxy-protocol.txt


async def _get_v1(reader: AsyncReader, initial=b"") -> ProxyData:
    proxy_data = ProxyData(version=1)
    proxyline = bytearray(initial)
    proxyline += await reader.readuntil()
    if len(proxyline) > 107:
        return proxy_data.with_error("PROXYv1 too long")
    mp = RE_PROXYv1.match(proxyline)
    if not mp:
        return proxy_data.with_error("PROXYv1 malformed")
    proto = mp.group("proto")
    proxy_data.protocol = proto
    rest = mp.group("rest")
    if proto == b"UNKNOWN":
        proxy_data.rest = rest
    else:
        mr = RE_PROXYv1_ADDR.match(rest)
        if not mr:
            return proxy_data.with_error("PROXYv1 address malformed")
        try:
            srcip = ip_address(mr.group("srcip").decode("latin-1"))
            dstip = ip_address(mr.group("dstip").decode("latin-1"))
            srcport = int(mr.group("srcport"))
            dstport = int(mr.group("dstport"))
        except ValueError:
            return proxy_data.with_error("PROXYv1 address parse error")
        if proto == b"TCP4" and not srcip.version == dstip.version == 4:
            return proxy_data.with_error("PROXYv1 address not IPv4")
        if proto == b"TCP6" and not srcip.version == dstip.version == 6:
            return proxy_data.with_error("PROXYv1 address not IPv6")
        if not 0 <= srcport <= 65535:
            return proxy_data.with_error("PROXYv1 src port out of bounds")
        if not 0 <= dstport <= 65535:
            return proxy_data.with_error("PROXYv1 dst port out of bounds")
        proxy_data.src_addr = srcip
        proxy_data.dst_addr = dstip
        proxy_data.src_port = srcport
        proxy_data.dst_port = dstport
    return proxy_data


async def _get_v2(reader: AsyncReader, initial=b"") -> ProxyData:
    proxy_data = ProxyData(version=2)

    signature = bytearray(initial)
    sig_left = 12 - len(signature)
    if sig_left > 0:  # pragma: no branch
        signature += await reader.read(sig_left)
    header = signature[12:]
    signature = signature[0:12]
    if signature != V2_SIGNATURE:
        return proxy_data.with_error("PROXYv2 wrong signature")

    hdr_left = 4 - len(header)
    if hdr_left > 0:  # pragma: no branch
        header += await reader.read(hdr_left)
    rest = header[4:]
    header = header[0:4]

    try:
        ver_cmd, fam_proto, len_ = struct.unpack("!BBH", header)
    except struct.error:
        return proxy_data.with_error("PROXYv2 malformed header")

    if (ver_cmd & 0xF0) != 0x20:
        return proxy_data.with_error("PROXYv2 illegal version")

    proxy_data.command = ver_cmd & 0x0F
    if proxy_data.command not in V2_VALID_CMDS:
        return proxy_data.with_error("PROXYv2 unsupported command")

    proxy_data.family = (fam_proto & 0xF0) >> 4
    if proxy_data.family not in V2_VALID_FAMS:
        return proxy_data.with_error("PROXYv2 unsupported family")

    proxy_data.protocol = fam_proto & 0x0F
    if proxy_data.protocol not in V2_VALID_PROS:
        return proxy_data.with_error("PROXYv2 unsupported protocol")

    rest_left = len_ - len(rest)
    if rest_left > 0:
        rest += await reader.read(rest_left)

    if fam_proto not in V2_PARSE_ADDR_FAMPRO:
        proxy_data.rest = rest
        return proxy_data

    if proxy_data.family == V2_FAM_IP4:
        unpacker = "!4s4sHH"
    elif proxy_data.family == V2_FAM_IP6:
        unpacker = "!16s16sHH"
    else:
        assert proxy_data.family == V2_FAM_UNIX
        unpacker = "108s108s0s0s"

    addr_len = struct.calcsize(unpacker)
    addr_struct = rest[0:addr_len]
    if len(addr_struct) < addr_len:
        return proxy_data.with_error("PROXYv2 truncated address")
    rest = addr_struct[addr_len:]
    s_addr, d_addr, s_port, d_port = struct.unpack(unpacker, addr_struct)

    if proxy_data.family == V2_FAM_IP4:
        proxy_data.src_addr = IPv4Address(s_addr)
        proxy_data.dst_addr = IPv4Address(d_addr)
        proxy_data.src_port = s_port
        proxy_data.dst_port = d_port
    elif proxy_data.family == V2_FAM_IP6:
        proxy_data.src_addr = IPv6Address(s_addr)
        proxy_data.dst_addr = IPv6Address(d_addr)
        proxy_data.src_port = s_port
        proxy_data.dst_port = d_port
    else:
        assert proxy_data.family == V2_FAM_UNIX
        proxy_data.src_addr = s_addr
        proxy_data.dst_addr = d_addr

    # We'll not attempt to interpret the TLV
    proxy_data.rest = rest

    return proxy_data


@public
async def get_proxy(reader_func: AsyncReader) -> ProxyData:
    """
    :param reader_func: Async function that implements the AsyncReader protocol.
    :return: Proxy Data if valid
    """
    signature = await reader_func.read(5)
    try:
        if signature == b"PROXY":
            return await _get_v1(reader_func, signature)
        elif signature == b"\r\n\r\n\x00":
            return await _get_v2(reader_func, signature)
        else:
            return ProxyData(version=None).with_error("PROXY unrecognized signature")
    except Exception as e:
        return ProxyData(version=None).with_error(f"PROXY exception: {str(e)}")