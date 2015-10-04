import sys
import socket
import asyncio
import logging

from aiosmtpd.events import Debugging
from aiosmtpd.smtp import SMTP

class ExitableSMTP(SMTP):
    @asyncio.coroutine
    def smtp_EXIT(self, arg):
        if arg:
            yield from self.push('501 Syntax: NOOP')
        else:
            yield from self.push('250 OK')
            self.loop.stop()
            self._connection_closed = True
            self._handler_coroutine.cancel()


logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger('mail.log')

loop = asyncio.get_event_loop()
sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, False)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
sock.bind(('::0', 9978))

def factory():
    return ExitableSMTP(Debugging(sys.stderr))

server = loop.run_until_complete(loop.create_server(factory, sock=sock))

log.info('Starting asyncio loop')
try:
    loop.run_forever()
except KeyboardInterrupt:
    pass
server.close()
log.info('Completed asyncio loop')
loop.run_until_complete(server.wait_closed())
loop.close()
