import asyncio
import logging

from aiosmtp import SmtpProtocol

logging.basicConfig(level=logging.DEBUG)

class Handler:

    def message_received(*args, **kw):
        print('message received:', *args, **kw)

    def verify(*args, **kw):
        print('verify:', *args, **kw)


def factory():
    return SmtpProtocol(Handler())

loop = asyncio.get_event_loop()
server = loop.run_until_complete(loop.create_server(factory, '0.0.0.0', 9978))
loop.run_forever()
