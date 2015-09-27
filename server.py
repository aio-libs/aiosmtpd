import asyncio
import logging

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
server = loop.run_until_complete(loop.create_server(factory, '::0', 9978))
loop.run_forever()
