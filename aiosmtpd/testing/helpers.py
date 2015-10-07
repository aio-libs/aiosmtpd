__all__ = [
    'Controller',
    'ExitableSMTP',
    ]


import socket
import asyncio
import smtplib
import threading

from aiosmtpd.smtp import SMTP


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

    def _run(self, ready_event):
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        sock.bind((self.hostname, self.port))
        asyncio.set_event_loop(self.loop)
        server = self.loop.run_until_complete(
            self.loop.create_server(self.factory, sock=sock))
        self.loop.call_soon(ready_event.set)
        self.loop.run_forever()
        server.close()
        self.loop.run_until_complete(server.wait_closed())
        self.loop.close()

    def start(self):
        assert self.thread is None, 'SMTP daemon already running'
        ready_event = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(ready_event,))
        self.thread.daemon = True
        self.thread.start()
        # Wait a while until the server is responding.
        ready_event.wait()

    def stop(self):
        assert self.thread is not None, 'SMTP daemon not running'
        client = smtplib.SMTP()
        client.connect(self.hostname, self.port)
        client.docmd('EXIT')
        self.thread.join()
