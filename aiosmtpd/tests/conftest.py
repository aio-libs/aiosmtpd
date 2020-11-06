import pytest
import asyncio

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from collections import namedtuple
from contextlib import suppress
from smtplib import SMTP as SMTPClient
from ..testing.helpers import DecodingController
from typing import Any, Dict, Optional


SRV_ADDR = namedtuple("IPPort", ("ip", "port"))("localhost", 8025)


def _get_marker_data(request, name) -> Dict[str, Any]:
    marker = request.node.get_closest_marker(name)
    if marker:
        return marker.kwargs or {}
    return {}


def _get_controller(
    request,
    handler,
    default: Optional[type(Controller)] = Controller,
    server_kwargs=None,
) -> Controller:
    markerdata = _get_marker_data(request, "controller_data")
    class_: type(Controller) = markerdata.get("class_")
    if class_ is None:
        if default is not None:
            class_ = default
        else:
            raise RuntimeError(
                f"Fixture '{request.fixturename}' needs controller_data to specify "
                f"what class to use"
            )
    return class_(handler, server_kwargs=server_kwargs)


def _get_handler(request, default=Sink):
    marker = request.node.get_closest_marker("handler_data")
    if marker:
        class_ = marker.kwargs.get("class_", Sink)
    else:
        class_ = Sink
    return class_()


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


@pytest.fixture
def base_controller(request) -> Controller:
    handler = _get_handler(request)
    controller = Controller(handler)
    controller.start()
    #
    yield controller
    #
    # Some test cases need to .stop() the controller inside themselves
    # in such cases, we must suppress Controller's raise of AssertionError
    # because Controller doesn't like .stop() to be invoked more than once
    with suppress(AssertionError):
        controller.stop()


@pytest.fixture
def decoding_controller(request) -> DecodingController:
    handler = _get_handler(request)
    controller = DecodingController(handler)
    controller.start()
    #
    yield controller
    #
    # Some test cases need to .stop() the controller inside themselves
    # in such cases, we must suppress Controller's raise of AssertionError
    # because Controller doesn't like .stop() to be invoked more than once
    with suppress(AssertionError):
        controller.stop()


@pytest.fixture
def client(request) -> SMTPClient:
    markerdata = _get_marker_data(request, "client_data")
    addrport = markerdata.get("connect_to", SRV_ADDR)
    with SMTPClient(*addrport) as client:
        yield client
