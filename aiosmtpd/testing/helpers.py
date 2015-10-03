__all__ = [
    'Controller',
    ]


import socket
import asyncio
import threading

from aiosmtpd.smtp import SMTP


PORT = 9978


class Controller:
    def __init__(self, handler, loop=None, hostname=None, port=None):
        self.handler = handler
        self.hostname = '::0' if hostname is None else hostname
        self.port = PORT if port is None else port
        self.loop = asyncio.get_event_loop() if loop is None else loop

    def factory(self):
        return SMTP(self.handler)

    def _run(self):
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        sock.bind((self.hostname, self.port))
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(
            self.loop.create_server(self.factory, sock=sock))
        self.loop.run_forever()

    def start(self):
        thread = threading.Thread(target=self._run)
        thread.daemon = True
        thread.start()
