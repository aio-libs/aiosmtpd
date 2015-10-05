__all__ = [
    'Controller',
    'ExitableSMTP',
    ]


import time
import socket
import asyncio
import smtplib
import threading

from aiosmtpd.smtp import SMTP
from datetime import datetime, timedelta


PORT = 9978


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


class Controller:
    def __init__(self, handler, loop=None, hostname=None, port=None):
        self.handler = handler
        self.hostname = '::0' if hostname is None else hostname
        self.port = PORT if port is None else port
        self.loop = asyncio.new_event_loop() if loop is None else loop
        self.thread = None

    def factory(self):
        return ExitableSMTP(self.handler)

    def _run(self):
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        sock.bind((self.hostname, self.port))
        asyncio.set_event_loop(self.loop)
        server = self.loop.run_until_complete(
            self.loop.create_server(self.factory, sock=sock))
        self.loop.run_forever()
        server.close()
        self.loop.run_until_complete(server.wait_closed())
        self.loop.close()

    def start(self):
        assert self.thread is None, 'SMTP daemon already running'
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()
        # Wait a while until the server is responding.
        until = datetime.now() + timedelta(days=1)
        while datetime.now() < until:
            try:
                client = smtplib.SMTP()
                client.connect(self.hostname, self.port)
                client.noop()
                client.quit()
                break
            except ConnectionRefusedError:
                time.sleep(1)

    def stop(self):
        assert self.thread is not None, 'SMTP daemon not running'
        client = smtplib.SMTP()
        client.connect(self.hostname, self.port)
        client.docmd('EXIT')
        self.thread.join()
