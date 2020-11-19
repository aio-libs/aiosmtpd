import os
import ssl
import pytest
import socket
import asyncio
import inspect

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server
from contextlib import suppress
from smtplib import SMTP as SMTPClient
from typing import NamedTuple, Optional, Type


# region #### Custom datatypes ########################################################


class HostPort(NamedTuple):
    host: str = "localhost"
    port: int = 8025


# endregion


# region #### Constants & Global Vars #################################################

ASYNCIO_CATCHUP_DELAY = float(os.environ.get("ASYNCIO_CATCHUP_DELAY", 0.1))
"""
Delay (in seconds) to give asyncio event loop time to catch up and do things. May need
to be increased for slow and/or overburdened test systems.
"""


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
    Provides a getter that will return an instance of a controller. Default class of
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
        class_: Type[Controller] = markerdata.get("class_", default_class)
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
            server_kwargs=server_kwargs,
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

    def getter(default_class=Sink) -> object:
        if marker:
            class_ = marker.kwargs.get("class_", default_class)
        else:
            class_ = default_class
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
def plain_controller(get_handler, get_controller) -> ExposingController:
    """
    Returns a Controller that was invoked with as few args as allowed. Hence the
    moniker "plain". By default, uses Sink as the handler class and ExposingController
    as the controller class, but changeable using pytest.mark.handler_data and
    .controller_data, respectively.
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
def decoding_controller(get_handler, get_controller) -> ExposingController:
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
def client(request) -> SMTPClient:
    marker = request.node.get_closest_marker("client_data")
    if marker:
        markerdata = marker.kwargs or {}
    else:
        markerdata = {}
    addrport = markerdata.get("connect_to", Global.SrvAddr)
    with SMTPClient(*addrport) as client:
        yield client


# endregion
