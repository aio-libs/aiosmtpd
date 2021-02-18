# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import errno
import os
import ssl
import threading
import time
from contextlib import ExitStack
from socket import AF_INET6, SOCK_STREAM, create_connection, has_ipv6
from socket import socket as makesock
from socket import timeout as socket_timeout
from typing import Any, Coroutine, Dict, Optional
from warnings import warn

from public import public

from aiosmtpd.smtp import SMTP

AsyncServer = asyncio.base_events.Server


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
class Controller:
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
        hostname=None,
        port=8025,
        *,
        ready_timeout=1.0,
        ssl_context: ssl.SSLContext = None,
        # SMTP parameters
        server_hostname: str = None,
        server_kwargs: Dict[str, Any] = None,
        **SMTP_parameters,
    ):
        """
        `Documentation can be found here
        <http://aiosmtpd.readthedocs.io/en/latest/aiosmtpd\
/docs/controller.html#controller-api>`_.
        """
        self.handler = handler
        self.hostname = get_localhost() if hostname is None else hostname
        self.port = port
        self.ssl_context = ssl_context
        self.loop = asyncio.new_event_loop() if loop is None else loop
        self.ready_timeout = float(
            os.getenv("AIOSMTPD_CONTROLLER_TIMEOUT", ready_timeout)
        )
        if server_kwargs:
            warn(
                "server_kwargs will be removed in version 2.0. "
                "Just specify the keyword arguments to forward to SMTP "
                "as kwargs to this __init__ method.",
                DeprecationWarning,
            )
        self.SMTP_kwargs: Dict[str, Any] = server_kwargs or {}
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

    def _run(self, ready_event):
        asyncio.set_event_loop(self.loop)
        try:
            # Need to do two-step assignments here to ensure IDEs can properly
            # detect the types of the vars. Cannot use `assert isinstance`, because
            # Python 3.6 in asyncio debug mode has a bug wherein CoroWrapper is not
            # an instance of Coroutine
            srv_coro: Coroutine = self.loop.create_server(
                self._factory_invoker,
                host=self.hostname,
                port=self.port,
                ssl=self.ssl_context,
            )
            self.server_coro = srv_coro
            srv: AsyncServer = self.loop.run_until_complete(srv_coro)
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

    def _testconn(self):
        """
        Opens a socket connection to the newly launched server, wrapping in an SSL
        Context if necessary, and read some data from it to ensure that factory()
        gets invoked.
        """
        # IMPORTANT: Windows does not need the next line; for some reasons,
        # create_connection is happy with hostname="" on Windows, but screams murder
        # in Linux.
        # At this point, if self.hostname is Falsy, it most likely is "" (bind to all
        # addresses). In such case, it should be safe to connect to localhost)
        hostname = self.hostname or get_localhost()
        with ExitStack() as stk:
            s = stk.enter_context(create_connection((hostname, self.port), 1.0))
            if self.ssl_context:
                s = stk.enter_context(self.ssl_context.wrap_socket(s))
            _ = s.recv(1024)

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
                raise TimeoutError("SMTP server failed to start within allotted time")
        respond_timeout = self.ready_timeout - (time.monotonic() - start)

        # Apparently create_server invokes factory() "lazily", so exceptions in
        # factory() go undetected. To trigger factory() invocation we need to open
        # a connection to the server and 'exchange' some traffic.
        try:
            self._testconn()
        except socket_timeout:
            # We totally don't care of timeout experienced by _testconn,
            # which _will_ happen if factory() experienced problems.
            pass
        except Exception:
            # Raise other exceptions though
            raise
        if not self._factory_invoked.wait(respond_timeout):
            raise TimeoutError("SMTP server not responding within allotted time")
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

    def stop(self):
        assert self._thread is not None, "SMTP daemon not running"
        self.loop.call_soon_threadsafe(self._stop)
        self._thread.join()
        self._thread = None
        self._thread_exception = None
