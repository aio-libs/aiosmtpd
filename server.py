import asyncio
import logging
import socket

from aiosmtpd.smtpd import DebuggingServer, SMTPChannel

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger('mail.log')

loop = asyncio.get_event_loop()
sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, False)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
sock.bind(('::0', 9978))

def factory():
    return SMTPChannel(DebuggingServer('::0', 9978))

server = loop.run_until_complete(loop.create_server(factory, sock=sock))

log.info('Starting asyncio loop')
loop.run_forever()
