import os
import asyncio
import threading

from aiosmtpd.smtp import SMTP
from public import public
from typing import Any, Dict


@public
class Controller:
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

    def _run(self, ready_event):
        asyncio.set_event_loop(self.loop)
        try:
            self.server = self.loop.run_until_complete(
                self.loop.create_server(
                    self.factory, host=self.hostname, port=self.port,
                    ssl=self.ssl_context))
        except Exception as error:  # pragma: nowsl
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

    def _stop(self):
        self.loop.stop()
        try:
            _all_tasks = asyncio.Task.all_tasks
        except AttributeError:   # pragma: skipif_lt_py39
            _all_tasks = asyncio.all_tasks
        for task in _all_tasks(self.loop):
            task.cancel()

    def stop(self):
        assert self._thread is not None, 'SMTP daemon not running'
        self.loop.call_soon_threadsafe(self._stop)
        self._thread.join()
        self._thread = None
