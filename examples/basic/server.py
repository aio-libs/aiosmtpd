# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink


async def amain(loop):
    cont = Controller(Sink(), hostname='', port=8025)
    cont.start()


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(amain(loop=loop))  # type: ignore[unused-awaitable]
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("User abort indicated")
