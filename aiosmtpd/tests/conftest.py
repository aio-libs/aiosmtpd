# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import ssl
import pytest
import socket
import inspect
import asyncio
import warnings

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server
from contextlib import suppress
from pkg_resources import resource_filename
from smtplib import SMTP as SMTPClient
from typing import Generator, NamedTuple, Optional, Type


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
    the controller is ExposingController, but can be changed via the "class_" parameter
    of @pytest.mark.controller_data
    """
    marker = request.node.get_closest_marker("controller_data")
    if marker:
        markerdata = marker.kwargs or {}
    else:
        markerdata = {}

    def getter(
        handler,
        default_class: Optional[Type[Controller]] = ExposingController,
        hostname: str = None,
        port: int = None,
        ssl_context: ssl.SSLContext = None,
        **server_kwargs,
    ) -> Controller:
        """
        :param handler: The handler object
        :param default_class: If set to None, then the actual class used to instantiate
            the controller *must* be provided via pytest.mark.controller_data
        :param hostname: The hostname (actually, address) for the controller. Defaults
            to HostPort().host
        :param port: The port for the controller. Defaults to HostPort().port
        :param ssl_context: The SSLContext for SMTPS. If provided, will disable
            STARTTLS
        """
        assert not inspect.isclass(handler)
        class_: Optional[Type[Controller]] = markerdata.get("class_", default_class)
        if class_ is None:
            raise RuntimeError(
                f"Fixture '{request.fixturename}' needs controller_data to specify "
                f"what class to use"
            )
        ip_port: HostPort = markerdata.get("host_port", HostPort())
        hostname = ip_port.host if hostname is None else hostname
        port = ip_port.port if port is None else port
        return class_(
            handler,
            hostname=hostname,
            port=port,
            ssl_context=ssl_context,
            **server_kwargs,
        )

    return getter


@pytest.fixture
def get_handler(request):
    """
    Provides a getter that will return an instance of a handler. Default class of
    the handler is Sink, but can be changed via the "class_" parameter of
    @pytest.mark.handler_data
    """
    marker = request.node.get_closest_marker("handler_data")
    default_class = Sink

    def getter() -> object:
        if marker:
            class_ = marker.kwargs.get("class_", default_class)
        else:
            class_ = default_class
        return class_()

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
    Returns a Controller that was invoked with no optional args. Hence the
    moniker "plain". Uses whatever class get_controller() uses as default, with
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


@pytest.fixture
def suppress_userwarning():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
    # with pytest.warns(UserWarning):
    #     yield


@pytest.fixture
def suppress_allwarnings():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield

# endregion
