import os
import asyncio
import threading

from aiosmtpd.smtp import SMTP
from public import public
from typing import Any, Dict
# Uncomment next line when we hit 1.3
# from warnings import warn


@public
class Controller:
    smtpd: SMTP = None

    def __init__(self, handler, loop=None, hostname=None, port=8025, *,
                 ready_timeout=1.0, enable_SMTPUTF8=None, ssl_context=None,
                 server_kwargs: Dict[str, Any] = None):
        """
        `Documentation can be found here
        <http://aiosmtpd.readthedocs.io/en/latest/aiosmtpd\
/docs/controller.html#controller-api>`_.
        """
        self.handler = handler
        self.hostname = '::1' if hostname is None else hostname
        self.port = port
        self.ssl_context = ssl_context
        self.loop = asyncio.new_event_loop() if loop is None else loop
        self.server = None
        self._thread = None
        self._thread_exception = None
        self.ready_timeout = os.getenv(
            'AIOSMTPD_CONTROLLER_TIMEOUT', ready_timeout)
        self.server_kwargs: Dict[str, Any] = server_kwargs or {}
        if enable_SMTPUTF8 is not None:
            # Uncomment next lines when we hit 1.3
            # warn("enable_SMTPUTF8 will be removed in the future. "
            #      "Please use server_kwargs instead.", DeprecationWarning)
            self.server_kwargs["enable_SMTPUTF8"] = enable_SMTPUTF8
        else:
            # This line emulates previous behavior of defaulting enable_SMTPUTF8 to
            # True. Which actually kinda conflicts with SMTP class's default, but it's
            # explained in the documentation.
            self.server_kwargs.setdefault("enable_SMTPUTF8", True)

    def factory(self):
        """Allow subclasses to customize the handler/server creation."""
        self.smtpd = SMTP(self.handler, **self.server_kwargs)
        return self.smtpd

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
