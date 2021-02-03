import re
import struct

from ipaddress import ip_address, IPv4Address, IPv6Address
from public import public

from typing import AnyStr, Awaitable, Optional, Union

try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol


__ALL__ = ["INVALID_PROXY"]  # Will be added to by @public


# region #### Custom Types ############################################################

EndpointAddress = Union[IPv4Address, IPv6Address, AnyStr]


@public
class AsyncReader(Protocol):
    def read(self, num_bytes: Optional[int] = None) -> Awaitable[bytes]:
        ...

    def readuntil(self, until_chars: Optional[bytes] = None) -> Awaitable[bytes]:
        ...


class _InvalidProxy:
    def __repr__(self):
        return "InvalidProxy"


@public
class ProxyData:
    version: int = None
    error: str = ""
    src_addr: EndpointAddress = None
    dst_addr: EndpointAddress = None
    src_port: int = None
    dst_port: int = None
    rest: Union[bytes, bytearray] = None
    family: int = None
    protocol: Union[int, AnyStr] = None

    def __init__(self, *, version: Optional[int]):
        self.version = version

    @property
    def valid(self) -> bool:
        return not self.error

    def with_error(self, error_msg: str) -> "ProxyData":
        self.error = error_msg
        return self


# endregion


INVALID_PROXY = _InvalidProxy()

RE_PROXYv1 = re.compile(br"PROXY (?P<proto>TCP4\b|TCP6\b|UNKNOWN)(?P<rest>.*)\r\n")
RE_PROXYv1_ADDR = re.compile(
    # Every piece below MUST start with b" "
    br" (?P<srcip>[0-9a-fA-F.:]+)"  # Validation done by ipaddress.ip_address
    br" (?P<dstip>[0-9a-fA-F.:]+)"
    br" (?P<srcport>[1-9]\d{0,4}|0)"  # 1-5 digits not starting with 0, or 0
    br" (?P<dstport>[1-9]\d{0,4}|0)"
    br"$"
)

V2_FAM_UNSPEC = 0
V2_FAM_IP4 = 1
V2_FAM_IP6 = 2
V2_FAM_UNIX = 3


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
    elif proto in (b"TCP4", b"TCP6"):
        mr = RE_PROXYv1_ADDR.match(rest)
        if not mr:
            return proxy_data.with_error("PROXYv1 malformed")
        try:
            srcip = ip_address(mr.group("srcip").decode("latin-1"))
            dstip = ip_address(mr.group("dstip").decode("latin-1"))
            srcport = int(mr.group("srcport"))
            dstport = int(mr.group("dstport"))
        except ValueError:
            return proxy_data.with_error("PROXYv1 malformed")
        if proto == b"TCP4" and not srcip.version == dstip.version == 4:
            return proxy_data.with_error("PROXYv1 address type mismatch")
        if proto == b"TCP6" and not srcip.version == dstip.version == 6:
            return proxy_data.with_error("PROXYv1 address type mismatch")
        if not 0 <= srcport <= 65535:
            return proxy_data.with_error("PROXYv1 port out of bounds")
        if not 0 <= dstport <= 65535:
            return proxy_data.with_error("PROXYv1 port out of bounds")
        proxy_data.src_addr = srcip
        proxy_data.dst_addr = dstip
        proxy_data.src_port = srcport
        proxy_data.dst_port = dstport
    else:
        proxy_data.error = "PROXYv1 unknown protocol"
    return proxy_data


async def _get_v2(reader: AsyncReader, initial=b"") -> Optional[ProxyData]:
    proxy_data = ProxyData(version=2)
    header = bytearray(initial)
    hdr_left = 16 - len(initial)
    if hdr_left > 0:
        rest = bytearray()
        header += await reader.read(hdr_left)
    else:
        rest = header[16:0]
        header = header[0:16]

    signature, ver_cmd, fam_proto, len_ = struct.unpack("!12sBBH", header)
    if signature != "\r\n\r\n\x00\r\nQUIT\n":
        return proxy_data.with_error("PROXYv2 wrong signature")

    if (ver_cmd & 0xF0) != 0x20:
        return proxy_data.with_error("PROXYv2 illegal version")

    proxy_data.command = ver_cmd & 0x0F
    if proxy_data.command not in (0, 1):
        return proxy_data.with_error("PROXYv2 unsupported command")

    proxy_data.family = (fam_proto & 0xF0) >> 4
    if proxy_data.family not in (V2_FAM_UNSPEC, V2_FAM_IP4, V2_FAM_IP6, V2_FAM_UNIX):
        return proxy_data.with_error("PROXYv2 unsupported family")

    proxy_data.protocol = fam_proto & 0x0F
    if proxy_data.protocol not in (0, 1, 2):
        return proxy_data.with_error("PROXYv2 unsupported protocol")

    rest_left = len_ - len(rest)
    if rest_left > 0:
        rest += await reader.read(rest_left)

    if fam_proto not in (0x11, 0x12, 0x21, 0x22, 0x31, 0x32):
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
    if rest:
        proxy_data.rest = rest

    return proxy_data


@public
async def get_proxy(reader_func: AsyncReader) -> ProxyData:
    """
    :param reader_func: Async function that implements the AsyncReader protocol.
    :return: Proxy Data if valid
    """
    signature = await reader_func.read(5)
    if signature == b"PROXY":
        return await _get_v1(reader_func, signature)
    elif signature == b"\r\n\r\n\x00":
        return await _get_v2(reader_func, signature)
    else:
        return ProxyData(version=None).with_error("PROXY unrecognized signature")
