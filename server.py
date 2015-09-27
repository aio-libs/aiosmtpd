import asyncio
import logging
import socket

from aiosmtpd import SmtpProtocol

logging.basicConfig(level=logging.DEBUG)

class Handler:

    @asyncio.coroutine
    def message_received(*args, **kw):
        print('message received:', *args, **kw)

    @asyncio.coroutine
    def verify(*args, **kw):
        print('verify:', *args, **kw)


def factory():
    return SmtpProtocol(Handler())

loop = asyncio.get_event_loop()
sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, False)
sock.bind(('::0', 9978))
server = loop.run_until_complete(loop.create_server(factory, sock=sock))
loop.run_forever()
