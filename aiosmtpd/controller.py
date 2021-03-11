# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import errno
import logging
import os
import ssl
import sys
import threading
import time
from abc import ABCMeta, abstractmethod
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from socket import (
    AF_INET6,
    SOCK_STREAM,
    create_connection,
    has_ipv6,
    socket as makesock,
    timeout as socket_timeout,
)

try:
    from socket import AF_UNIX
except ImportError:  # pragma: on-not-win32
    AF_UNIX = None
from typing import (
    Any,
    Callable,
    Coroutine,
    Deque,
    Dict,
    MutableMapping,
    Optional,
    Tuple,
    Union,
)

if sys.version_info >= (3, 8):
    from typing import Literal  # pragma: py-lt-38
else:  # pragma: py-ge-38
    from typing_extensions import Literal

from warnings import warn

from public import public

from aiosmtpd.smtp import SMTP

AsyncServer = asyncio.base_events.Server
ExceptionHandlerType = Callable[[asyncio.AbstractEventLoop, Dict[str, Any]], None]

DEFAULT_READY_TIMEOUT: float = 5.0


class ContextLoggerAdapter(logging.LoggerAdapter):
    @property
    def context(self):
        return self.extra.get("context")

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> Tuple[Any, MutableMapping[str, Any]]:
        msg = f"[{self.context}] {msg}" if self.context else msg
        return msg, kwargs


log = ContextLoggerAdapter(logging.getLogger("aiosmtpd.controller"), {})


@public
class IP6_IS:
    # Apparently errno.E* constants adapts to the OS, so on Windows they will
    # automatically use the WSAE* constants
    NO = {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT}
    YES = {errno.EADDRINUSE}


def _has_ipv6() -> bool:
    # Helper function to assist in mocking
    return has_ipv6


@public
def get_localhost() -> Literal["::1", "127.0.0.1"]:
    """Returns numeric address to localhost depending on IPv6 availability"""
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
    __slots__ = ()  # 'Finalize' this class

    def __init__(self, loop):
        # Imitate what SMTP does
        super().__init__(
            asyncio.StreamReader(loop=loop),
            client_connected_cb=self._client_connected_cb,
            loop=loop,
        )

    def _client_connected_cb(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        pass


@public
class ExceptionAccumulator:
    """
    Provides a simple asyncio exception handler that only record unhandled exceptions
    and not do anything else.
    """
    __slots__ = ("accumulator", "peaked", "with_log")

    """Indicates if accumulator ever peaked (items appended > maxlen)"""

    def __init__(self, with_log: bool = True, maxlen: int = 20):
        self.accumulator: Deque[Dict[str, str]] = deque(maxlen=maxlen)
        self.peaked: bool = False
        self.with_log: bool = with_log

    @property
    def max_items(self):
        return self.accumulator.maxlen

    @max_items.setter
    def max_items(self, value):
        if not isinstance(value, int) or value < 1:
            raise ValueError("maxlen must be an int > 0")
        accu = self.accumulator
        if value == accu.maxlen:
            return
        self.accumulator = deque(accu, maxlen=value)

    def clear(self):
        self.accumulator.clear()
        self.peaked = False

    def __call__(self, loop, context):
        msg = str(context.get("exception", context["message"]))
        hnd = repr(context.get("handle"))
        fut = repr(context.get("future"))
        if self.with_log:
            log.error("Caught exception %s", msg)
            log.error("  Handle: %s", hnd)
            log.error("  Future: %s", fut)
        accu = self.accumulator
        if len(accu) == accu.maxlen:
            self.peaked = True
        accu.append(dict(msg=msg, hnd=hnd, fut=fut))


class BaseControllerMapping:
    __slots__ = ()

    def __get__(self, instance: "BaseController", owner):
        return {
            "context": instance.name,
        }


@public
class BaseController(metaclass=ABCMeta):
    _mapping = BaseControllerMapping()

    smtpd = None
    server: Optional[AsyncServer] = None
    server_coro: Optional[Coroutine] = None

    def __init__(
        self,
        handler: Any,
        loop: asyncio.AbstractEventLoop = None,
        *,
        name: Optional[str] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
        # SMTP parameters
        server_hostname: Optional[str] = None,
        **SMTP_parameters,
    ):
        self.handler = handler
        handler_name = getattr(handler, "Name", type(handler).__name__)
        self.name = name or f"Controller({handler_name})"
        log.extra = self._mapping
        if loop is None:
            self.loop = asyncio.new_event_loop()
        else:
            self.loop = loop
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
        #
        self._factory_invoked: threading.Event = threading.Event()
        self._cancel_done: threading.Event = threading.Event()

    def factory(self):
        """Subclasses can override this to customize the handler/server creation."""
        return SMTP(self.handler, **self.SMTP_kwargs)

    def _factory_invoker(self):
        """Wraps factory() to catch exceptions during instantiation"""
        try:
            self.smtpd = self.factory()
            if self.smtpd is None:
                raise RuntimeError(f"[{self.name}] factory() returned None")
            return self.smtpd
        except Exception as err:
            self._thread_exception = err
            return _FakeServer(self.loop)
        finally:
            self._factory_invoked.set()

    @abstractmethod
    def _create_server(self) -> Coroutine:
        """
        Overridden by subclasses to actually perform the async binding to the
        listener endpoint. When overridden, MUST refer the _factory_invoker() method.
        """
        raise NotImplementedError

    def _cleanup(self):
        """Reset internal variables to prevent contamination"""
        self._thread_exception = None
        self._factory_invoked.clear()
        if self.server:
            self.server.close()
            self.server = None
        if self.server_coro:
            self.server_coro.close()
        self.server_coro = None
        self.smtpd = None

    def cancel_tasks(self, stop_loop: bool = True):
        """
        Convenience method to stop the loop and cancel all tasks.
        Use loop.call_soon_threadsafe() to invoke this.
        """
        self._cancel_done.clear()
        log.info("cancel_tasks(stop_loop=%s)", stop_loop)
        if stop_loop:  # pragma: nobranch
            self.loop.stop()
        try:
            _all_tasks = asyncio.all_tasks  # pytype: disable=module-attr
        except AttributeError:  # pragma: py-gt-36
            _all_tasks = asyncio.Task.all_tasks
        for task in _all_tasks(self.loop):
            # This needs to be invoked in a thread-safe way
            task.cancel()
        time.sleep(0.1)
        self._cancel_done.set()


@public
class BaseThreadedController(BaseController, metaclass=ABCMeta):
    DefaultExceptionHandler: ExceptionHandlerType = ExceptionAccumulator()

    _thread: Optional[threading.Thread] = None
    _thread_exception: Optional[Exception] = None

    def __init__(
        self,
        handler: Any,
        loop: asyncio.AbstractEventLoop = None,
        *,
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
        ssl_context: Optional[ssl.SSLContext] = None,
        # SMTP parameters
        server_hostname: Optional[str] = None,
        **SMTP_parameters,
    ):
        super().__init__(
            handler,
            loop,
            ssl_context=ssl_context,
            server_hostname=server_hostname,
            **SMTP_parameters,
        )
        self.ready_timeout = float(
            os.getenv("AIOSMTPD_CONTROLLER_TIMEOUT", ready_timeout)
        )

    @abstractmethod
    def _trigger_server(self):
        """
        Overridden by subclasses to trigger asyncio to actually initialize the SMTP
        class (it's lazy initialization, done only on initial connection).
        """
        raise NotImplementedError

    def _run(self, ready_event: threading.Event) -> None:
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
        if (  # pragma: nobranch
            self.loop.get_exception_handler() is None and self.DefaultExceptionHandler
        ):
            self.loop.set_exception_handler(self.DefaultExceptionHandler)
        self.loop.run_forever()
        # We reach this point when loop is ended (by external code)
        # Perform some stoppages to ensure endpoint no longer bound.
        self.server.close()
        self.server_coro.close()
        if not self.loop.is_closed():  # pragma: nobranch
            self.loop.run_until_complete(self.server.wait_closed())
            time.sleep(0.1)
            self.loop.close()
        self.server = None

    def start(self, thread_name: Optional[str] = None):
        """
        Start a thread and run the asyncio event loop in that thread
        """
        if self._thread is not None:
            raise RuntimeError("SMTP daemon already running")
        log.info("Starting")
        self._factory_invoked.clear()
        thread_name = thread_name or f"{self.name}-1"

        ready_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(ready_event,), name=thread_name
        )
        self._thread.daemon = True
        self._thread.start()
        # Wait a while until the server is responding.
        start = time.monotonic()
        log.debug("Waiting for server to start listening")
        if not ready_event.wait(self.ready_timeout):
            # An exception within self._run will also result in ready_event not set
            # So, we first test for that, before raising TimeoutError
            if self._thread_exception is not None:  # pragma: on-wsl
                # See comment about WSL1.0 in the _run() method
                raise self._thread_exception
            else:
                log.critical("Server timeout")
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
        log.debug("Waiting for server to start serving")
        if not self._factory_invoked.wait(respond_timeout):
            log.critical("Server response timeout")
            raise TimeoutError(
                "SMTP server started, but not responding within allotted time. "
                "This might happen if the system is too busy. "
                "Try increasing the `ready_timeout` parameter."
            )
        if self._thread_exception is not None:
            log.exception(
                "The following exception happened:", exc_info=self._thread_exception
            )
            raise self._thread_exception

        # Defensive
        if self.smtpd is None:
            raise RuntimeError("Unknown Error, failed to init SMTP server")
        log.info("Started successfully")

    def stop(self, no_assert: bool = False):
        """
        Stop the loop, the tasks in the loop, and terminate the thread as well.
        """
        assert no_assert or self._thread is not None, "SMTP daemon not running"
        log.info("Stopping")
        self.loop.call_soon_threadsafe(self.cancel_tasks)
        if not self.loop.is_running():
            self.loop.close()
        if self._thread is not None:
            log.debug("Waiting to join thread...")
            self._thread.join()
            self._thread = None
        self._cleanup()
        log.info("Stopped successfully")


@public
class BaseUnthreadedController(BaseController, metaclass=ABCMeta):
    def __init__(
        self,
        handler: Any,
        loop: asyncio.AbstractEventLoop = None,
        *,
        name: str = None,
        ssl_context: Optional[ssl.SSLContext] = None,
        # SMTP parameters
        server_hostname: Optional[str] = None,
        **SMTP_parameters,
    ):
        super().__init__(
            handler,
            loop,
            name=name,
            ssl_context=ssl_context,
            server_hostname=server_hostname,
            **SMTP_parameters,
        )
        self.ended = threading.Event()

    def begin(self):
        """
        Sets up the asyncio server task and inject it into the asyncio event loop.
        Does NOT actually start the event loop itself.
        """
        log.info("Begins")
        asyncio.set_event_loop(self.loop)
        # Need to do two-step assignments here to ensure IDEs can properly
        # detect the types of the vars. Cannot use `assert isinstance`, because
        # Python 3.6 in asyncio debug mode has a bug wherein CoroWrapper is not
        # an instance of Coroutine
        self.server_coro = self._create_server()
        srv: AsyncServer = self.loop.run_until_complete(self.server_coro)
        self.server = srv

    async def finalize(self):
        """
        Perform orderly closing of the server listener.
        NOTE: This is an async method; await this from an async or use
        loop.create_task() (if loop is still running), or
        loop.run_until_complete() (if loop has stopped)
        """
        self.ended.clear()
        log.info("Finalizing")
        server = self.server
        server.close()
        await server.wait_closed()
        self.server_coro.close()
        self._cleanup()
        self.ended.set()
        log.info("Finalized")

    def end(self):
        """
        Convenience method to asynchronously invoke finalize().
        Consider using loop.call_soon_threadsafe to invoke this method, especially
        if your loop is running in a different thread. You can afterwards .wait() on
        ended attribute (a threading.Event) to check for completion, if needed.
        """
        log.info("Ending")
        self.ended.clear()
        if self.loop.is_running():
            self.loop.create_task(self.finalize())
        else:
            self.loop.run_until_complete(self.finalize())
        log.info("Ended")


@public
class InetMixin(BaseController, metaclass=ABCMeta):
    def __init__(
        self,
        handler: Any,
        hostname: Optional[str] = None,
        port: int = 8025,
        loop: asyncio.AbstractEventLoop = None,
        **kwargs,
    ):
        super().__init__(
            handler,
            loop,
            **kwargs,
        )
        self._localhost = get_localhost()
        self.hostname = self._localhost if hostname is None else hostname
        self.port = port

    def _create_server(self) -> Coroutine:
        """
        Creates a 'server task' that listens on an INET host:port.
        Does NOT actually start the protocol object itself;
        _factory_invoker() is only called upon fist connection attempt.
        """
        log.debug("Creating listener on %s:%s", self.hostname, self.port)
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
        hostname = self.hostname or self._localhost
        log.debug("Trying to trigger server on %s:%s", hostname, self.port)
        with ExitStack() as stk:
            s = stk.enter_context(create_connection((hostname, self.port), 1.0))
            if self.ssl_context:
                s = stk.enter_context(self.ssl_context.wrap_socket(s))
            s.recv(1024)


@public
class UnixSocketMixin(BaseController, metaclass=ABCMeta):  # pragma: no-unixsock
    def __init__(
        self,
        handler: Any,
        unix_socket: Union[str, Path],
        loop: asyncio.AbstractEventLoop = None,
        **kwargs,
    ):
        super().__init__(
            handler,
            loop,
            **kwargs,
        )
        self.unix_socket = str(unix_socket)

    def _create_server(self) -> Coroutine:
        """
        Creates a 'server task' that listens on a Unix Socket file.
        Does NOT actually start the protocol object itself;
        _factory_invoker() is only called upon fist connection attempt.
        """
        log.debug("Creating listener on %s", self.unix_socket)
        return self.loop.create_unix_server(
            self._factory_invoker,
            path=self.unix_socket,
            ssl=self.ssl_context,
        )

    def _trigger_server(self):
        """
        Opens a socket connection to the newly launched server, wrapping in an SSL
        Context if necessary, and read some data from it to ensure that factory()
        gets invoked.
        """
        log.debug("Trying to trigger server on %s", self.unix_socket)
        with ExitStack() as stk:
            s: makesock = stk.enter_context(makesock(AF_UNIX, SOCK_STREAM))
            s.connect(self.unix_socket)
            if self.ssl_context:
                s = stk.enter_context(self.ssl_context.wrap_socket(s))
            s.recv(1024)


@public
class Controller(InetMixin, BaseThreadedController):
    """Provides a multithreaded controller that listens on an INET endpoint"""

    def _trigger_server(self):
        # Prevent confusion on which _trigger_server() to invoke.
        # Or so LGTM.com claimed
        InetMixin._trigger_server(self)


@public
class UnixSocketController(  # pragma: no-unixsock
    UnixSocketMixin, BaseThreadedController
):
    """Provides a multithreaded controller that listens on a Unix Socket file"""

    def _trigger_server(self):  # pragma: no-unixsock
        # Prevent confusion on which _trigger_server() to invoke.
        # Or so LGTM.com claimed
        UnixSocketMixin._trigger_server(self)


@public
class UnthreadedController(InetMixin, BaseUnthreadedController):
    """Provides an unthreaded controller that listens on an INET endpoint"""

    pass


@public
class UnixSocketUnthreadedController(  # pragma: no-unixsock
    UnixSocketMixin, BaseUnthreadedController
):
    """Provides an unthreaded controller that listens on a Unix Socket file"""

    pass
