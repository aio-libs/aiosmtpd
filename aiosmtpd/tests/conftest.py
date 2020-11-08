import pytest
import asyncio
import inspect

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from contextlib import suppress
from smtplib import SMTP as SMTPClient
from ..testing.helpers import DecodingController
from typing import Any, Callable, Dict, NamedTuple, Optional, Type


class HostPort(NamedTuple):
    host: str = "localhost"
    port: int = 8025


class Global:
    SrvAddr: HostPort = HostPort()

    @classmethod
    def set_addr_from(cls, contr: Controller):
        cls.SrvAddr = HostPort(contr.hostname, contr.port)


@pytest.fixture
def get_controller(request) -> Callable[..., Controller]:
    marker = request.node.get_closest_marker("controller_data")
    if marker:
        markerdata = marker.kwargs or {}
    else:
        markerdata = {}

    def getter(
        handler,
        default: Optional[Type[Controller]] = Controller,
        server_kwargs: Dict[str, Any] = None,
    ) -> Controller:
        assert not inspect.isclass(handler)
        class_: Type[Controller] = markerdata.get("class_", default)
        if class_ is None:
            raise RuntimeError(
                f"Fixture '{request.fixturename}' needs controller_data to specify "
                f"what class to use"
            )
        ip_port: HostPort = markerdata.get("ip_port", HostPort())
        return class_(
            handler,
            hostname=ip_port.host,
            port=ip_port.port,
            server_kwargs=server_kwargs,
        )

    return getter


@pytest.fixture
def get_handler(request) -> Callable[..., object]:
    marker = request.node.get_closest_marker("handler_data")

    def getter(default=Sink):
        if marker:
            class_ = marker.kwargs.get("class_", default)
        else:
            class_ = default
        return class_()

    return getter


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
def base_controller(get_handler, get_controller) -> Controller:
    handler = get_handler()
    controller = get_controller(handler)
    controller.start()
    Global.set_addr_from(controller)
    #
    yield controller
    #
    # Some test cases need to .stop() the controller inside themselves
    # in such cases, we must suppress Controller's raise of AssertionError
    # because Controller doesn't like .stop() to be invoked more than once
    with suppress(AssertionError):
        controller.stop()


@pytest.fixture
def decoding_controller(get_handler) -> DecodingController:
    handler = get_handler()
    controller = DecodingController(handler)
    controller.start()
    Global.set_addr_from(controller)
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
    marker = request.node.get_closest_marker("client_data")
    if marker:
        markerdata = marker.kwargs or {}
    else:
        markerdata = {}
    addrport = markerdata.get("connect_to", Global.SrvAddr)
    with SMTPClient(*addrport) as client:
        yield client
