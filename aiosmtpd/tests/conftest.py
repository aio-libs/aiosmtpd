# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import ssl
import pytest
import socket
import inspect
import asyncio

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server
from contextlib import suppress
from functools import wraps
from pkg_resources import resource_filename
from smtplib import SMTP as SMTPClient
from typing import Generator, NamedTuple, Optional, Type

try:
    from asyncio.proactor_events import _ProactorBasePipeTransport
    HAS_PROACTOR = True
except ImportError:
    _ProactorBasePipeTransport = None
    HAS_PROACTOR = False


# region #### Custom datatypes ########################################################


class HostPort(NamedTuple):
    host: str = "localhost"
    port: int = 8025


# endregion


# region #### Constants & Global Vars #################################################


class Global:
    SrvAddr: HostPort = HostPort()
    FQDN: str = socket.getfqdn()

    @classmethod
    def set_addr_from(cls, contr: Controller):
        cls.SrvAddr = HostPort(contr.hostname, contr.port)


# endregion


# region #### Custom Behavior Controllers #############################################


class ExposingController(Controller):
    """
    A subclass of Controller that 'exposes' the inner SMTP object for inspection.
    """

    smtpd: Server

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def factory(self):
        self.smtpd = super().factory()
        return self.smtpd


# endregion


# region #### Optimizing Fixtures #####################################################


# autouse=True and scope="session" automatically apply this fixture to ALL test cases
@pytest.fixture(autouse=True, scope="session")
def cache_fqdn(session_mocker):
    """
    This fixture "caches" the socket.getfqdn() call. VERY necessary to prevent
    situations where quick repeated getfqdn() causes extreme slowdown. Probably due to
    the DNS server thinking it was an attack or something.
    """
    session_mocker.patch("socket.getfqdn", return_value=Global.FQDN)
    #
    yield


# endregion


# region #### Common Fixtures #########################################################


@pytest.fixture
def get_controller(request):
    """
    Provides a function that will return an instance of a controller. Default class of
    the controller is Controller, but can be changed via the "class_" parameter
    of @pytest.mark.controller_data
    """
    default_class = Controller
    marker = request.node.get_closest_marker("controller_data")
    if marker:
        markerdata = marker.kwargs or {}
    else:
        markerdata = {}

    def getter(
        handler,
        class_: Optional[Type[Controller]] = None,
        **server_kwargs,
    ) -> Controller:
        """
        :param handler: The handler object
        :param class_: If set to None, check controller_data(class_).
            If both are none, defaults to Controller.
        """
        assert not inspect.isclass(handler)
        marker_class: Optional[Type[Controller]]
        marker_class = markerdata.pop("class_", default_class)
        class_ = class_ or marker_class
        if class_ is None:
            raise RuntimeError(
                f"Fixture '{request.fixturename}' needs controller_data to specify "
                f"what class to use"
            )
        ip_port: HostPort = markerdata.pop("host_port", HostPort())
        # server_kwargs takes precedence, so it's rightmost (PEP448)
        server_kwargs = {**markerdata, **server_kwargs}
        server_kwargs.setdefault("hostname", ip_port.host)
        server_kwargs.setdefault("port", ip_port.port)
        return class_(
            handler,
            **server_kwargs,
        )

    return getter


@pytest.fixture
def get_handler(request):
    """
    Provides a getter that, when invoked, will return an instance of a handler.
    Default class of the handler is Sink, but can be changed via the "class_"
    parameter of @pytest.mark.handler_data
    """
    marker = request.node.get_closest_marker("handler_data")
    default_class = Sink

    def getter(*args, **kwargs):
        if marker:
            class_ = marker.kwargs.pop("class_", default_class)
            # *args overrides args_ in handler_data()
            args_ = marker.kwargs.pop("args_", tuple())
            # Do NOT inline the above into the line below! We *need* to pop "args_"!
            args = args or args_
            # **kwargs override marker.kwargs, so it's rightmost (PEP448)
            kwargs = {**marker.kwargs, **kwargs}
        else:
            class_ = default_class
        # noinspection PyArgumentList
        return class_(*args, **kwargs)

    return getter


@pytest.fixture
def temp_event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    default_loop = asyncio.get_event_loop()
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    #
    yield new_loop
    #
    new_loop.close()
    asyncio.set_event_loop(default_loop)


@pytest.fixture
def plain_controller(
        get_handler, get_controller
) -> Generator[ExposingController, None, None]:
    """
    Returns a Controller that, by default, gets invoked with no optional args. Hence
    the moniker "plain". Uses whatever class get_controller() uses as default, with
    Sink as the handler class (changeable using pytest.mark.handler_data).
    """
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
def nodecode_controller(
        get_handler, get_controller
) -> Generator[ExposingController, None, None]:
    handler = get_handler()
    controller = get_controller(handler, decode_data=False)
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
def decoding_controller(
        get_handler, get_controller
) -> Generator[ExposingController, None, None]:
    handler = get_handler()
    controller = get_controller(handler, decode_data=True)
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
def client(request) -> Generator[SMTPClient, None, None]:
    """
    Generic SMTP Client, will connect to the host:port defined in Global.SrvAddr
    unless overriden using @pytest.mark.client_data(connect_to: HostPort = ...)
    """
    marker = request.node.get_closest_marker("client_data")
    if marker:
        markerdata = marker.kwargs or {}
    else:
        markerdata = {}
    addrport = markerdata.get("connect_to", Global.SrvAddr)
    with SMTPClient(*addrport) as client:
        yield client


@pytest.fixture
def ssl_context_server() -> Generator[ssl.SSLContext, None, None]:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.check_hostname = False
    context.load_cert_chain(
        resource_filename("aiosmtpd.tests.certs", "server.crt"),
        resource_filename("aiosmtpd.tests.certs", "server.key"),
    )
    #
    yield context


@pytest.fixture
def ssl_context_client() -> Generator[ssl.SSLContext, None, None]:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.check_hostname = False
    context.load_verify_locations(
        resource_filename("aiosmtpd.tests.certs", "server.crt")
    )
    #
    yield context


# Please keep the scope as "module"; setting it as "function" (the default) somehow
# causes the 'hidden' exception to be detected when the loop starts over in the next
# test case, defeating the silencing.
@pytest.fixture(scope="module")
def silence_event_loop_closed():
    """
    Mostly used to suppress "unhandled exception" error due to
    _ProactorBasePipeTransport raising an exception when doing __del__.
    """
    if not HAS_PROACTOR:
        return False
    assert _ProactorBasePipeTransport is not None
    if hasattr(_ProactorBasePipeTransport, "old_del"):
        return True

    # From: https://github.com/aio-libs/aiohttp/issues/4324#issuecomment-733884349
    def silencer(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except RuntimeError as e:
                if str(e) != "Event loop is closed":
                    raise

        return wrapper

    # noinspection PyUnresolvedReferences
    old_del = _ProactorBasePipeTransport.__del__
    _ProactorBasePipeTransport._old_del = old_del
    _ProactorBasePipeTransport.__del__ = silencer(old_del)
    return True

# endregion
