# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os
import ssl
import threading
from abc import ABCMeta, abstractmethod
from contextlib import ExitStack
from pathlib import Path
from socket import create_connection, socket, SOCK_STREAM
try:
    from socket import AF_UNIX
except ImportError:  # pragma: on-not-win32
    AF_UNIX = None
from typing import Any, Coroutine, Dict, Optional, Union
from warnings import warn

from public import public

from aiosmtpd.smtp import SMTP

AsyncServer = asyncio.base_events.Server


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
    server: Optional[AsyncServer] = None
    server_coro: Coroutine = None
    smtpd = None
    _factory_invoked: Optional[threading.Event] = None
    _thread: Optional[threading.Thread] = None
    _thread_exception: Optional[Exception] = None

    def __init__(
        self,
        handler,
        loop=None,
        *,
        ready_timeout: float = 1.0,
        ssl_context: Optional[ssl.SSLContext] = None,
        # SMTP parameters
        server_hostname: Optional[str] = None,
        **SMTP_parameters,
    ):
        """
        `Documentation can be found here
        <http://aiosmtpd.readthedocs.io/en/latest/aiosmtpd\
/docs/controller.html#controller-api>`_.
        """
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
        ready_event = threading.Event()
        self._factory_invoked = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready_event,))
        self._thread.daemon = True
        self._thread.start()
        # Wait a while until the server is responding.
        ready_event.wait(self.ready_timeout)
        if self._thread_exception is not None:  # pragma: on-wsl
            # See comment about WSL1.0 in the _run() method
            raise self._thread_exception
        if not ready_event.is_set():
            raise TimeoutError("SMTP server failed to start within allotted time")
        # Apparently create_server invokes factory() "lazily", so exceptions in
        # factory() go undetected. To trigger factory() invocation we need to open
        # a connection to the server and 'exchange' some traffic.
        try:
            self._trigger_server()
        except Exception:
            # We totally don't care of exceptions experienced by _trigger_server,
            # which _will_ happen if factory() experienced problems.
            pass
        if not self._factory_invoked.wait(self.ready_timeout):
            raise TimeoutError("SMTP server not responding within allotted time")
        if self._thread_exception is not None:
            raise self._thread_exception
        # Defensive
        if self.smtpd is None:
            raise RuntimeError("Unknown Error, failed to init SMTP server")

    def _stop(self):
        self.loop.stop()
        try:
            _all_tasks = asyncio.all_tasks
        except AttributeError:  # pragma: py-gt-36
            _all_tasks = asyncio.Task.all_tasks
        for task in _all_tasks(self.loop):
            task.cancel()

    def stop(self):
        assert self._thread is not None, "SMTP daemon not running"
        self.loop.call_soon_threadsafe(self._stop)
        self._thread.join()
        self._thread = None
        self._thread_exception = None


@public
class Controller(BaseThreadedController):
    def __init__(
        self,
        handler,
        hostname: Optional[str] = None,
        port: int = 8025,
        loop=None,
        *,
        ready_timeout: float = 1.0,
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
        self.hostname = "::1" if hostname is None else hostname
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
        with ExitStack() as stk:
            s = stk.enter_context(create_connection((self.hostname, self.port), 1.0))
            if self.ssl_context:
                s = stk.enter_context(self.ssl_context.wrap_socket(s))
            _ = s.recv(1024)


class UnixSocketController(BaseThreadedController):  # pragma: on-win32
    def __init__(
        self,
        handler,
        unix_socket: Optional[Union[str, Path]],
        loop=None,
        *,
        ready_timeout=1.0,
        ssl_context=None,
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
            s: socket = stk.enter_context(socket(AF_UNIX, SOCK_STREAM))
            s.connect(self.unix_socket)
            if self.ssl_context:
                s = stk.enter_context(self.ssl_context.wrap_socket(s))
            _ = s.recv(1024)
