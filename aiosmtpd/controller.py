import os
import ssl
import asyncio
import threading

from aiosmtpd.smtp import SMTP
from contextlib import ExitStack
from public import public
from socket import create_connection
from typing import Any, Dict


class _FakeServer(asyncio.StreamReaderProtocol):
    """
    Returned by _factory_invoker() in lieu of an SMTP instance in case
    factory() fails with an exception.
    """
    def __init__(self, loop):
        super().__init__(
            asyncio.StreamReader(loop=loop),
            client_connected_cb=self._client_connected_cb,
            loop=loop)

    def _client_connected_cb(self, reader, writer):
        pass


@public
class Controller:
    smtpd = None

    def __init__(self, handler, loop=None, hostname=None, port=8025, *,
                 ready_timeout=1.0, enable_SMTPUTF8=True, ssl_context=None,
                 server_kwargs: Dict[str, Any] = None):
        """
        `Documentation can be found here
        <http://aiosmtpd.readthedocs.io/en/latest/aiosmtpd\
/docs/controller.html#controller-api>`_.
        """
        self.handler = handler
        self.hostname = '::1' if hostname is None else hostname
        self.port = port
        self.enable_SMTPUTF8 = enable_SMTPUTF8
        self.ssl_context = ssl_context
        self.loop = asyncio.new_event_loop() if loop is None else loop
        self.server = None
        self._thread = None
        self._thread_exception = None
        self.ready_timeout = os.getenv(
            'AIOSMTPD_CONTROLLER_TIMEOUT', ready_timeout)
        self.server_kwargs: Dict[str, Any] = server_kwargs or {}

    def factory(self):
        """Allow subclasses to customize the handler/server creation."""
        return SMTP(self.handler, enable_SMTPUTF8=self.enable_SMTPUTF8,
                    **self.server_kwargs)

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

    def _run(self, ready_event):
        asyncio.set_event_loop(self.loop)
        try:
            self.server = self.loop.run_until_complete(
                self.loop.create_server(
                    self._factory_invoker, host=self.hostname, port=self.port,
                    ssl=self.ssl_context))
        except Exception as error:  # pragma: nowsl
            # Will enter this part _only_ if create_server cannot bind to
            # specified host:port
            # Somehow WSL1.0 (Windows Subsystem for Linux) allows multiple
            # listeners on one port?!
            # That is why we add "pragma: nowsl" there, so when testing on
            # WSL we can specify "PLATFORM=wsl".
            self._thread_exception = error
            return
        self.loop.call_soon(ready_event.set)
        self.loop.run_forever()
        self.server.close()
        self.loop.run_until_complete(self.server.wait_closed())
        self.loop.close()
        self.server = None

    def _testconn(self):
        with ExitStack() as stk:
            s = stk.enter_context(
                create_connection((self.hostname, self.port), 1.0)
            )
            if self.ssl_context:
                context = ssl.SSLContext()
                s = stk.enter_context(
                    context.wrap_socket(s)
                )
            # Need to perform socket read, else create_server won't call
            # _factory_invoker
            _ = s.recv(1024)

    def start(self):
        assert self._thread is None, 'SMTP daemon already running'
        ready_event = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready_event,))
        self._thread.daemon = True
        self._thread.start()
        # Wait a while until the server is responding.
        ready_event.wait(self.ready_timeout)
        if self._thread_exception is not None:  # pragma: nowsl
            # See comment about WSL1.0 in the _run() method
            raise self._thread_exception
        # Apparently create_server invokes the passed-in factory method
        # "lazily", resulting in exceptions in factory() to go undetected.
        # So we open a connection to trigger create_server to actually in-
        # voke factory (via _factory_invoker)
        try:
            self._testconn()
        except Exception:
            # We totally don't care of exceptions experienced by _testconn,
            # which _will_ happen if factory() experienced problems.
            pass
        if self._thread_exception is not None:
            raise self._thread_exception
        # Defensive
        if self.smtpd is None:
            raise RuntimeError("Unknown Error, failed to init SMTP server")

    def _stop(self):
        self.loop.stop()
        try:
            _all_tasks = asyncio.all_tasks
        except AttributeError:   # pragma: skipif_gt_py36
            _all_tasks = asyncio.Task.all_tasks
        for task in _all_tasks(self.loop):
            task.cancel()

    def stop(self):
        assert self._thread is not None, 'SMTP daemon not running'
        self.loop.call_soon_threadsafe(self._stop)
        self._thread.join()
        self._thread = None
