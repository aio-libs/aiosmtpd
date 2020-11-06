import pytest
import asyncio

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from typing import Optional


@pytest.fixture
def temp_event_loop() -> asyncio.AbstractEventLoop:
    default_loop = asyncio.get_event_loop()
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    #
    yield new_loop
    #
    new_loop.close()
    asyncio.set_event_loop(default_loop)


def _get_controller(
    request, handler, default: Optional[Controller] = Controller
) -> Controller:
    marker = request.node.get_closest_marker("controller_data")
    class_: type(Controller)
    if marker:
        class_ = marker.kwargs.get("class_", default)
    else:
        if default is not None:
            class_ = Controller
        else:
            raise RuntimeError(
                f"Fixture '{request.fixturename}' needs controller_data to specify "
                f"what class to use"
            )
    return class_(handler)


def _get_handler(request, default=Sink):
    marker = request.node.get_closest_marker("handler_data")
    if marker:
        class_ = marker.kwargs.get("class_", Sink)
    else:
        class_ = Sink
    return class_()
