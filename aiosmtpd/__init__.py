# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0
import asyncio
import warnings


__version__ = "1.4.4a0"


def _get_or_new_eventloop() -> asyncio.AbstractEventLoop:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            loop = asyncio.get_event_loop()
        except (DeprecationWarning, RuntimeError):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    return loop
