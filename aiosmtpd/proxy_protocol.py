# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import re
import struct
from enum import IntEnum
from functools import partial
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any, AnyStr, Awaitable, Dict, Optional, Tuple, Union

import attr
from public import public

try:
    from typing import Protocol
except ImportError:  # pragma: py-ge-38
    from typing_extensions import Protocol

V1_VALID_PROS = {"TCP4", "TCP6", "UNKNOWN", b"TCP4", b"TCP6", b"UNKNOWN"}

V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"


class V2_CMD(IntEnum):
    LOCAL = 0
    PROXY = 1


class V2_FAM(IntEnum):
    UNSPEC = 0
    IP4 = 1
    IP6 = 2
    UNIX = 3


class V2_PRO(IntEnum):
    UNSPEC = 0
    STREAM = 1
    DGRAM = 2


V2_VALID_CMDS = {item.value for item in V2_CMD}
V2_VALID_FAMS = {item.value for item in V2_FAM}
V2_VALID_PROS = {item.value for item in V2_PRO}
V2_PARSE_ADDR_FAMPRO = {
    (V2_FAM.IP4 << 4) | V2_PRO.STREAM,
    (V2_FAM.IP4 << 4) | V2_PRO.DGRAM,
    (V2_FAM.IP6 << 4) | V2_PRO.STREAM,
    (V2_FAM.IP6 << 4) | V2_PRO.DGRAM,
    (V2_FAM.UNIX << 4) | V2_PRO.STREAM,
    (V2_FAM.UNIX << 4) | V2_PRO.DGRAM,
}


__all__ = [
    k for k in globals().keys() if k.startswith("V1_") or k.startswith("V2_")
] + ["struct", "partial", "IPv4Address", "IPv6Address"]


_NOT_FOUND = object()


# region #### Custom Types ############################################################

EndpointAddress = Union[IPv4Address, IPv6Address, AnyStr]


@public
class MalformedTLV(RuntimeError):
    pass


@public
class UnknownTypeTLV(KeyError):
    pass


@public
class AsyncReader(Protocol):  # pragma: nocover
    def read(self, num_bytes: Optional[int] = None) -> Awaitable[bytes]:
        ...

    def readexactly(self, n: int) -> Awaitable[bytes]:
        ...

    def readuntil(self, until_chars: Optional[bytes] = None) -> Awaitable[bytes]:
        ...


_anoinit = partial(attr.ib, init=False)


@public
class ProxyTLV(dict):
    __slots__ = ("tlv_loc",)

    PP2_TYPENAME: Dict[int, str] = {
        0x01: "ALPN",
        0x02: "AUTHORITY",
        0x03: "CRC32C",
        0x04: "NOOP",
        0x05: "UNIQUE_ID",
        0x20: "SSL",
        0x21: "SSL_VERSION",
        0x22: "SSL_CN",
        0x23: "SSL_CIPHER",
        0x24: "SSL_SIG_ALG",
        0x25: "SSL_KEY_ALG",
        0x30: "NETNS",
    }

    def __init__(self, *args, _tlv_loc: Dict[str, int], **kwargs):
        super().__init__(*args, **kwargs)
        self.tlv_loc = _tlv_loc

    def __getattr__(self, item):
        return self.get(item)

    def same_attribs(self, _raises: bool = False, **kwargs) -> bool:
        for k, v in kwargs.items():
            actual = self.get(k, _NOT_FOUND)
            if actual != v:
                if _raises:
                    raise ValueError(f"mismatch:{k} actual={actual!r} expect={v!r}")
                return False
        return True

    @classmethod
    def parse(
        cls,
        data: Union[bytes, bytearray],
        partial_ok: bool = True,
        strict: bool = False,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        rslt: Dict[str, Any] = {}
        tlv_loc: Dict[str, int] = {}

        def _pars(chunk: Union[bytes, bytearray], *, offset: int):
            i = 0
            while i < len(chunk):
                typ = chunk[i]
                len_ = int.from_bytes(chunk[i + 1 : i + 3], "big")
                val = chunk[i + 3 : i + 3 + len_]
                if len(val) < len_:
                    raise MalformedTLV(f"TLV 0x{typ:02X} is malformed!")
                typ_name = cls.PP2_TYPENAME.get(typ)
                if typ_name is None:
                    typ_name = f"x{typ:02X}"
                    if strict:
                        raise UnknownTypeTLV(typ_name)
                tlv_loc[typ_name] = offset + i
                if typ_name == "SSL":
                    rslt["SSL_CLIENT"] = val[0]
                    rslt["SSL_VERIFY"] = int.from_bytes(val[1:5], "big")
                    try:
                        _pars(val[5:], offset=i)
                        rslt["SSL"] = True
                    except MalformedTLV:
                        rslt["SSL"] = False
                        if not partial_ok:
                            raise
                        else:
                            return
                else:
                    rslt[typ_name] = val
                i += 3 + len_

        try:
            _pars(data, offset=0)
        except MalformedTLV:
            if not partial_ok:
                raise
        return rslt, tlv_loc

    @classmethod
    def from_raw(
        cls, raw: Union[bytes, bytearray], strict: bool = False
    ) -> Optional["ProxyTLV"]:
        """
        Parses raw bytes for TLV Vectors, decode them and giving them human-readable
        name if applicable, and returns a ProxyTLV object.
        """
        if len(raw) == 0:
            return None
        parsed, tlv_loc = cls.parse(raw, partial_ok=False, strict=strict)
        return cls(parsed, _tlv_loc=tlv_loc)

    @classmethod
    def name_to_num(cls, name: str) -> Optional[int]:
        for k, v in cls.PP2_TYPENAME.items():
            if name == v:
                return k
        return None


@public
@attr.s(slots=True)
class ProxyData:
    version: Optional[int] = attr.ib(kw_only=True, init=True)
    """PROXY Protocol version; None if not recognized/malformed"""
    command: Optional[int] = _anoinit(default=None)
    """PROXYv2 command"""
    family: Optional[int] = _anoinit(default=None)
    """PROXYv2 protocol family"""
    protocol: Optional[Union[int, AnyStr]] = _anoinit(default=None)
    src_addr: Optional[EndpointAddress] = _anoinit(default=None)
    dst_addr: Optional[EndpointAddress] = _anoinit(default=None)
    src_port: Optional[int] = _anoinit(default=None)
    dst_port: Optional[int] = _anoinit(default=None)
    rest: Union[bytes, bytearray] = _anoinit(default=b"")
    """
    Rest of PROXY Protocol data following UNKNOWN (v1) or UNSPEC (v2), or containing
    undecoded TLV (v2). If the latter, you can use the ProxyTLV class to parse the
    binary data.
    """
    error: str = _anoinit(default="")
    """If not an empty string, contains the error encountered when parsing"""
    _tlv: Optional[ProxyTLV] = _anoinit(default=None)

    @property
    def valid(self) -> bool:
        return not (self.error or self.version is None or self.protocol is None)

    @property
    def tlv(self):
        if self._tlv is None:
            try:
                self._tlv = ProxyTLV.from_raw(self.rest)
            except MalformedTLV:
                pass
        return self._tlv

    def with_error(self, error_msg: str) -> "ProxyData":
        self.error = error_msg
        return self

    def same_attribs(self, **kwargs) -> bool:
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
        header += await reader.readexactly(hdr_left)
    rest = header[4:]
    header = header[0:4]

    ver_cmd, fam_proto, len_ = struct.unpack("!BBH", header)

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
        rest += await reader.readexactly(rest_left)

    if fam_proto not in V2_PARSE_ADDR_FAMPRO:
        proxy_data.rest = rest
        return proxy_data

    if proxy_data.family == V2_FAM.IP4:
        unpacker = "!4s4sHH"
    elif proxy_data.family == V2_FAM.IP6:
        unpacker = "!16s16sHH"
    else:
        assert proxy_data.family == V2_FAM.UNIX
        unpacker = "108s108s0s0s"

    addr_len = struct.calcsize(unpacker)
    addr_struct = rest[0:addr_len]
    if len(addr_struct) < addr_len:
        return proxy_data.with_error("PROXYv2 truncated address")
    rest = rest[addr_len:]
    s_addr, d_addr, s_port, d_port = struct.unpack(unpacker, addr_struct)

    if proxy_data.family == V2_FAM.IP4:
        proxy_data.src_addr = IPv4Address(s_addr)
        proxy_data.dst_addr = IPv4Address(d_addr)
        proxy_data.src_port = s_port
        proxy_data.dst_port = d_port
    elif proxy_data.family == V2_FAM.IP6:
        proxy_data.src_addr = IPv6Address(s_addr)
        proxy_data.dst_addr = IPv6Address(d_addr)
        proxy_data.src_port = s_port
        proxy_data.dst_port = d_port
    else:
        assert proxy_data.family == V2_FAM.UNIX
        proxy_data.src_addr = s_addr
        proxy_data.dst_addr = d_addr

    proxy_data.rest = rest

    return proxy_data


@public
async def get_proxy(reader_func: AsyncReader) -> ProxyData:
    """
    :param reader_func: Async function that implements the AsyncReader protocol.
    :return: Proxy Data if valid
    """
    signature = await reader_func.readexactly(5)
    try:
        if signature == b"PROXY":
            return await _get_v1(reader_func, signature)
        elif signature == b"\r\n\r\n\x00":
            return await _get_v2(reader_func, signature)
        else:
            return ProxyData(version=None).with_error("PROXY unrecognized signature")
    except Exception as e:  # pragma: nocover
        return ProxyData(version=None).with_error(f"PROXY exception: {str(e)}")
