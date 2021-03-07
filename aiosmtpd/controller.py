# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import errno
import os
import ssl
import threading
import time
from abc import ABCMeta, abstractmethod
from contextlib import ExitStack
from pathlib import Path
from socket import AF_INET6, SOCK_STREAM, create_connection, has_ipv6
from socket import socket as makesock
from socket import timeout as socket_timeout

try:
    from socket import AF_UNIX
except ImportError:  # pragma: on-not-win32
    AF_UNIX = None
from typing import Any, Coroutine, Dict, Optional, Union
from warnings import warn

from public import public

from aiosmtpd.smtp import SMTP

AsyncServer = asyncio.base_events.Server

DEFAULT_READY_TIMEOUT: float = 5.0


@public
class IP6_IS:
    # Apparently errno.E* constants adapts to the OS, so on Windows they will
    # automatically use the WSAE* constants
    NO = {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT}
    YES = {errno.EADDRINUSE}


def _has_ipv6():
    # Helper function to assist in mocking
    return has_ipv6


@public
def get_localhost() -> str:
    # Ref:
    #  - https://github.com/urllib3/urllib3/pull/611#issuecomment-100954017
    #  - https://github.com/python/cpython/blob/ :
    #    - v3.6.13/Lib/test/support/__init__.py#L745-L758
    #    - v3.9.1/Lib/test/support/socket_helper.py#L124-L137
    if not _has_ipv6():
        # socket.has_ipv6 only tells us of current Python's IPv6 support, not the
        # system's. But if the current Python does not support IPv6, it's pointless to
        # explore further.
        return "127.0.0.1"
    try:
        with makesock(AF_INET6, SOCK_STREAM) as sock:
            sock.bind(("::1", 0))
        # If we reach this point, that means we can successfully bind ::1 (on random
        # unused port), so IPv6 is definitely supported
        return "::1"
    except OSError as e:
        if e.errno in IP6_IS.NO:
            return "127.0.0.1"
        if e.errno in IP6_IS.YES:
            # We shouldn't ever get these errors, but if we do, that means IPv6 is
            # supported
            return "::1"
        # Other kinds of errors MUST be raised so we can inspect
        raise


class _FakeServer(asyncio.StreamReaderProtocol):
    """
    Returned by _factory_invoker() in lieu of an SMTP instance in case
    factory() failed to instantiate an SMTP instance.
    """

    def __init__(self, loop):
        # Imitate what SMTP does
        super().__init__(
            asyncio.StreamReader(loop=loop),
            client_connected_cb=self._client_connected_cb,
            loop=loop,
        )

    def _client_connected_cb(self, reader, writer):
        pass


@public
class BaseThreadedController(metaclass=ABCMeta):
    """
    `Documentation can be found here
    <https://aiosmtpd.readthedocs.io/en/latest/controller.html>`_.
    """
    server: Optional[AsyncServer] = None
    server_coro: Optional[Coroutine] = None
    smtpd = None
    _factory_invoked: Optional[threading.Event] = None
    _thread: Optional[threading.Thread] = None
    _thread_exception: Optional[Exception] = None

    def __init__(
        self,
        handler,
        loop=None,
        *,
        ready_timeout: float,
        ssl_context: Optional[ssl.SSLContext] = None,
        # SMTP parameters
        server_hostname: Optional[str] = None,
        **SMTP_parameters,
    ):
        self.handler = handler
        if loop is None:
            self.loop = asyncio.new_event_loop()
        else:
            self.loop = loop
        self.ready_timeout = float(
            os.getenv("AIOSMTPD_CONTROLLER_TIMEOUT", ready_timeout)
        )
        self.ssl_context = ssl_context
        self.SMTP_kwargs: Dict[str, Any] = {}
        if "server_kwargs" in SMTP_parameters:
            warn(
                "server_kwargs will be removed in version 2.0. "
                "Just specify the keyword arguments to forward to SMTP "
                "as kwargs to this __init__ method.",
                DeprecationWarning,
            )
            self.SMTP_kwargs = SMTP_parameters.pop("server_kwargs")
        self.SMTP_kwargs.update(SMTP_parameters)
        if server_hostname:
            self.SMTP_kwargs["hostname"] = server_hostname
        # Emulate previous behavior of defaulting enable_SMTPUTF8 to True
        # It actually conflicts with SMTP class's default, but the reasoning is
        # discussed in the docs.
        self.SMTP_kwargs.setdefault("enable_SMTPUTF8", True)

    def factory(self):
        """Allow subclasses to customize the handler/server creation."""
        return SMTP(self.handler, **self.SMTP_kwargs)

    def _factory_invoker(self):
        """Wraps factory() to catch exceptions during instantiation"""
        try:
            self.smtpd = self.factory()
            if self.smtpd is None:
                raise RuntimeError("factory() returned None")
            return self.smtpd
        except Exception as err:
            self._thread_exception = err
            return _FakeServer(self.loop)
        finally:
            self._factory_invoked.set()

    @abstractmethod
    def _create_server(self) -> Coroutine:
        raise NotImplementedError  # pragma: nocover

    @abstractmethod
    def _trigger_server(self):
        raise NotImplementedError  # pragma: nocover

    def _run(self, ready_event):
        asyncio.set_event_loop(self.loop)
        try:
            # Need to do two-step assignments here to ensure IDEs can properly
            # detect the types of the vars. Cannot use `assert isinstance`, because
            # Python 3.6 in asyncio debug mode has a bug wherein CoroWrapper is not
            # an instance of Coroutine
            self.server_coro = self._create_server()
            srv: AsyncServer = self.loop.run_until_complete(self.server_coro)
            self.server = srv
        except Exception as error:  # pragma: on-wsl
            # Usually will enter this part only if create_server() cannot bind to the
            # specified host:port.
            #
            # Somehow WSL 1.0 (Windows Subsystem for Linux) allows multiple
            # listeners on one port?!
            # That is why we add "pragma: on-wsl" there, so this block will not affect
            # coverage on WSL 1.0.
            self._thread_exception = error
            return
        self.loop.call_soon(ready_event.set)
        self.loop.run_forever()
        self.server.close()
        self.loop.run_until_complete(self.server.wait_closed())
        self.loop.close()
        self.server = None

    def start(self):
        assert self._thread is None, "SMTP daemon already running"
        self._factory_invoked = threading.Event()

        ready_event = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready_event,))
        self._thread.daemon = True
        self._thread.start()
        # Wait a while until the server is responding.
        start = time.monotonic()
        if not ready_event.wait(self.ready_timeout):
            # An exception within self._run will also result in ready_event not set
            # So, we first test for that, before raising TimeoutError
            if self._thread_exception is not None:  # pragma: on-wsl
                # See comment about WSL1.0 in the _run() method
                raise self._thread_exception
            else:
                raise TimeoutError(
                    "SMTP server failed to start within allotted time. "
                    "This might happen if the system is too busy. "
                    "Try increasing the `ready_timeout` parameter."
                )
        respond_timeout = self.ready_timeout - (time.monotonic() - start)

        # Apparently create_server invokes factory() "lazily", so exceptions in
        # factory() go undetected. To trigger factory() invocation we need to open
        # a connection to the server and 'exchange' some traffic.
        try:
            self._trigger_server()
        except socket_timeout:
            # We totally don't care of timeout experienced by _testconn,
            pass
        except Exception:
            # Raise other exceptions though
            raise
        if not self._factory_invoked.wait(respond_timeout):
            raise TimeoutError(
                "SMTP server started, but not responding within allotted time. "
                "This might happen if the system is too busy. "
                "Try increasing the `ready_timeout` parameter."
            )
        if self._thread_exception is not None:
            raise self._thread_exception

        # Defensive
        if self.smtpd is None:
            raise RuntimeError("Unknown Error, failed to init SMTP server")

    def _stop(self):
        self.loop.stop()
        try:
            _all_tasks = asyncio.all_tasks  # pytype: disable=module-attr
        except AttributeError:  # pragma: py-gt-36
            _all_tasks = asyncio.Task.all_tasks
        for task in _all_tasks(self.loop):
            task.cancel()

    def stop(self, no_assert=False):
        assert no_assert or self._thread is not None, "SMTP daemon not running"
        self.loop.call_soon_threadsafe(self._stop)
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self._thread_exception = None
        self._factory_invoked = None
        self.server_coro = None
        self.server = None
        self.smtpd = None


@public
class Controller(BaseThreadedController):
    """
    `Documentation can be found here
    <https://aiosmtpd.readthedocs.io/en/latest/controller.html>`_.
    """
    def __init__(
        self,
        handler,
        hostname: Optional[str] = None,
        port: int = 8025,
        loop=None,
        *,
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
        ssl_context: ssl.SSLContext = None,
        # SMTP parameters
        server_hostname: Optional[str] = None,
        **SMTP_parameters,
    ):
        super().__init__(
            handler,
            loop,
            ready_timeout=ready_timeout,
            server_hostname=server_hostname,
            **SMTP_parameters
        )
        self.hostname = get_localhost() if hostname is None else hostname
        self.port = port
        self.ssl_context = ssl_context

    def _create_server(self) -> Coroutine:
        return self.loop.create_server(
            self._factory_invoker,
            host=self.hostname,
            port=self.port,
            ssl=self.ssl_context,
        )

    def _trigger_server(self):
        """
        Opens a socket connection to the newly launched server, wrapping in an SSL
        Context if necessary, and read some data from it to ensure that factory()
        gets invoked.
        """
        # At this point, if self.hostname is Falsy, it most likely is "" (bind to all
        # addresses). In such case, it should be safe to connect to localhost)
        hostname = self.hostname or get_localhost()
        with ExitStack() as stk:
            s = stk.enter_context(create_connection((hostname, self.port), 1.0))
            if self.ssl_context:
                s = stk.enter_context(self.ssl_context.wrap_socket(s))
            _ = s.recv(1024)


class UnixSocketController(BaseThreadedController):  # pragma: on-win32 on-cygwin
    """
    `Documentation can be found here
    <https://aiosmtpd.readthedocs.io/en/latest/controller.html>`_.
    """
    def __init__(
        self,
        handler,
        unix_socket: Optional[Union[str, Path]],
        loop=None,
        *,
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
        ssl_context: ssl.SSLContext = None,
        # SMTP parameters
        server_hostname: str = None,
        **SMTP_parameters,
    ):
        super().__init__(
            handler,
            loop,
            ready_timeout=ready_timeout,
            ssl_context=ssl_context,
            server_hostname=server_hostname,
            **SMTP_parameters
        )
        self.unix_socket = str(unix_socket)

    def _create_server(self) -> Coroutine:
        return self.loop.create_unix_server(
            self._factory_invoker,
            path=self.unix_socket,
            ssl=self.ssl_context,
        )

    def _trigger_server(self):
        with ExitStack() as stk:
            s: makesock = stk.enter_context(makesock(AF_UNIX, SOCK_STREAM))
            s.connect(self.unix_socket)
            if self.ssl_context:
                s = stk.enter_context(self.ssl_context.wrap_socket(s))
            _ = s.recv(1024)
