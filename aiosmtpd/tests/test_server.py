# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Test other aspects of the server implementation."""

import errno
import platform
import socket
import ssl
import time
from contextlib import ExitStack
from functools import partial
from pathlib import Path
from tempfile import mkdtemp
from typing import Generator

import pytest
from pytest_mock import MockFixture

from aiosmtpd.controller import (
    Controller,
    UnixSocketController,
    _FakeServer,
    get_localhost,
)
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server

from .conftest import Global


class SlowStartController(Controller):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("ready_timeout", 0.5)
        super().__init__(*args, **kwargs)

    def _run(self, ready_event):
        time.sleep(self.ready_timeout * 1.5)
        super()._run(ready_event)


class SlowFactoryController(Controller):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("ready_timeout", 0.5)
        super().__init__(*args, **kwargs)

    def factory(self):
        time.sleep(self.ready_timeout * 3)
        return super().factory()

    def _factory_invoker(self):
        time.sleep(self.ready_timeout * 3)
        return super()._factory_invoker()


def in_win32():
    return platform.system().casefold() == "windows"


def in_wsl():
    # WSL 1.0 somehow allows more than one listener on one port.
    # So we have to detect when we're running on WSL so we can skip some tests.

    # On Windows, platform.release() returns the Windows version (e.g., "7" or "10")
    # On Linux (incl. WSL), platform.release() returns the kernel version.
    # As of 2021-02-07, only WSL has a kernel with "Microsoft" in the version.
    return "microsoft" in platform.release().casefold()


def in_cygwin():
    return platform.system().casefold().startswith("cygwin")


@pytest.fixture(scope="module")
def safe_socket_dir() -> Generator[Path, None, None]:
    # See:
    #   - https://github.com/aio-libs/aiohttp/issues/3572
    #   - https://github.com/aio-libs/aiohttp/pull/3832/files
    #   - https://unix.stackexchange.com/a/367012/5589
    tmpdir = Path(mkdtemp()).absolute()
    assert len(str(tmpdir)) <= 87  # 92 (max on HP-UX) minus 5 (allow 4-char fn)
    #
    yield tmpdir
    #
    plist = [p for p in tmpdir.rglob("*")]
    for p in reversed(plist):
        if p.is_dir():
            p.rmdir()
        else:
            p.unlink()
    tmpdir.rmdir()


class TestServer:
    """Tests for the aiosmtpd.smtp.SMTP class"""

    def test_smtp_utf8(self, plain_controller, client):
        code, mesg = client.ehlo("example.com")
        assert code == 250
        assert b"SMTPUTF8" in mesg.splitlines()

    def test_default_max_command_size_limit(self):
        server = Server(Sink())
        assert server.max_command_size_limit == 512

    def test_special_max_command_size_limit(self):
        server = Server(Sink())
        server.command_size_limits["DATA"] = 1024
        assert server.max_command_size_limit == 1024

    def test_warn_authreq_notls(self):
        expectedre = (
            r"Requiring AUTH while not requiring TLS can lead to "
            r"security vulnerabilities!"
        )
        with pytest.warns(UserWarning, match=expectedre):
            Server(Sink(), auth_require_tls=False, auth_required=True)


class TestController:
    """Tests for the aiosmtpd.controller.Controller class"""

    @pytest.mark.filterwarnings("ignore")
    def test_ready_timeout(self):
        cont = SlowStartController(Sink())
        expectre = (
            "SMTP server failed to start within allotted time. "
            "This might happen if the system is too busy. "
            "Try increasing the `ready_timeout` parameter."
        )
        try:
            with pytest.raises(TimeoutError, match=expectre):
                cont.start()
        finally:
            cont.stop()

    @pytest.mark.filterwarnings("ignore")
    def test_factory_timeout(self):
        cont = SlowFactoryController(Sink())
        expectre = (
            r"SMTP server started, but not responding within allotted time. "
            r"This might happen if the system is too busy. "
            r"Try increasing the `ready_timeout` parameter."
        )
        try:
            with pytest.raises(TimeoutError, match=expectre):
                cont.start()
        finally:
            cont.stop()

    def test_reuse_loop(self, temp_event_loop):
        cont = Controller(Sink(), loop=temp_event_loop)
        assert cont.loop is temp_event_loop
        try:
            cont.start()
            assert cont.smtpd.loop is temp_event_loop
        finally:
            cont.stop()

    @pytest.mark.skipif(in_wsl(), reason="WSL prevents socket collision")
    def test_socket_error_dupe(self, plain_controller, client):
        contr2 = Controller(
            Sink(), hostname=Global.SrvAddr.host, port=Global.SrvAddr.port
        )
        try:
            with pytest.raises(socket.error):
                contr2.start()
        finally:
            contr2.stop()

    @pytest.mark.skipif(in_wsl(), reason="WSL prevents socket collision")
    def test_socket_error_default(self):
        contr1 = Controller(Sink())
        contr2 = Controller(Sink())
        expectedre = r"error while attempting to bind on address"
        try:
            with pytest.raises(socket.error, match=expectedre):
                contr1.start()
                contr2.start()
        finally:
            contr2.stop()
            contr1.stop()

    def test_server_attribute(self):
        controller = Controller(Sink())
        assert controller.server is None
        try:
            controller.start()
            assert controller.server is not None
        finally:
            controller.stop()
        assert controller.server is None

    @pytest.mark.filterwarnings(
        "ignore:server_kwargs will be removed:DeprecationWarning"
    )
    def test_enablesmtputf8_flag(self):
        # Default is True
        controller = Controller(Sink())
        assert controller.SMTP_kwargs["enable_SMTPUTF8"]
        # Explicit set must be reflected in server_kwargs
        controller = Controller(Sink(), enable_SMTPUTF8=True)
        assert controller.SMTP_kwargs["enable_SMTPUTF8"]
        controller = Controller(Sink(), enable_SMTPUTF8=False)
        assert not controller.SMTP_kwargs["enable_SMTPUTF8"]
        # Explicit set must override server_kwargs
        kwargs = dict(enable_SMTPUTF8=False)
        controller = Controller(Sink(), enable_SMTPUTF8=True, server_kwargs=kwargs)
        assert controller.SMTP_kwargs["enable_SMTPUTF8"]
        kwargs = dict(enable_SMTPUTF8=True)
        controller = Controller(Sink(), enable_SMTPUTF8=False, server_kwargs=kwargs)
        assert not controller.SMTP_kwargs["enable_SMTPUTF8"]
        # Set through server_kwargs must not be overridden if no explicit set
        kwargs = dict(enable_SMTPUTF8=False)
        controller = Controller(Sink(), server_kwargs=kwargs)
        assert not controller.SMTP_kwargs["enable_SMTPUTF8"]

    @pytest.mark.filterwarnings(
        "ignore:server_kwargs will be removed:DeprecationWarning"
    )
    def test_serverhostname_arg(self):
        contsink = partial(Controller, Sink())
        controller = contsink()
        assert "hostname" not in controller.SMTP_kwargs
        controller = contsink(server_hostname="testhost1")
        assert controller.SMTP_kwargs["hostname"] == "testhost1"
        kwargs = dict(hostname="testhost2")
        controller = contsink(server_kwargs=kwargs)
        assert controller.SMTP_kwargs["hostname"] == "testhost2"
        controller = contsink(server_hostname="testhost3", server_kwargs=kwargs)
        assert controller.SMTP_kwargs["hostname"] == "testhost3"

    def test_hostname_empty(self):
        # WARNING: This test _always_ succeeds in Windows.
        cont = Controller(Sink(), hostname="")
        try:
            cont.start()
        finally:
            cont.stop()

    def test_hostname_none(self):
        cont = Controller(Sink())
        try:
            cont.start()
        finally:
            cont.stop()

    def test_testconn_raises(self, mocker: MockFixture):
        mocker.patch("socket.socket.recv", side_effect=RuntimeError("MockError"))
        cont = Controller(Sink(), hostname="")
        try:
            with pytest.raises(RuntimeError, match="MockError"):
                cont.start()
        finally:
            cont.stop()

    def test_getlocalhost(self):
        assert get_localhost() in ("127.0.0.1", "::1")

    def test_getlocalhost_noipv6(self, mocker):
        mock_hasip6 = mocker.patch("aiosmtpd.controller._has_ipv6", return_value=False)
        assert get_localhost() == "127.0.0.1"
        assert mock_hasip6.called

    def test_getlocalhost_6yes(self, mocker: MockFixture):
        mock_sock = mocker.Mock()
        mock_makesock: mocker.Mock = mocker.patch("aiosmtpd.controller.makesock")
        mock_makesock.return_value.__enter__.return_value = mock_sock
        assert get_localhost() == "::1"
        mock_makesock.assert_called_with(socket.AF_INET6, socket.SOCK_STREAM)
        assert mock_sock.bind.called

    # Apparently errno.E* constants adapts to the OS, so on Windows they will
    # automatically use the analogous WSAE* constants
    @pytest.mark.parametrize(
        "err",
        [errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT]
    )
    def test_getlocalhost_6no(self, mocker, err):
        mock_makesock: mocker.Mock = mocker.patch(
            "aiosmtpd.controller.makesock",
            side_effect=OSError(errno.EADDRNOTAVAIL, "Mock IP4-only"),
        )
        assert get_localhost() == "127.0.0.1"
        mock_makesock.assert_called_with(socket.AF_INET6, socket.SOCK_STREAM)

    def test_getlocalhost_6inuse(self, mocker):
        mock_makesock: mocker.Mock = mocker.patch(
            "aiosmtpd.controller.makesock",
            side_effect=OSError(errno.EADDRINUSE, "Mock IP6 used"),
        )
        assert get_localhost() == "::1"
        mock_makesock.assert_called_with(socket.AF_INET6, socket.SOCK_STREAM)

    def test_getlocalhost_error(self, mocker):
        mock_makesock: mocker.Mock = mocker.patch(
            "aiosmtpd.controller.makesock",
            side_effect=OSError(errno.EFAULT, "Mock Error"),
        )
        with pytest.raises(OSError, match="Mock Error") as exc:
            get_localhost()
        assert exc.value.errno == errno.EFAULT
        mock_makesock.assert_called_with(socket.AF_INET6, socket.SOCK_STREAM)

    def test_stop_default(self):
        controller = Controller(Sink())
        with pytest.raises(AssertionError, match="SMTP daemon not running"):
            controller.stop()

    def test_stop_assert(self):
        controller = Controller(Sink())
        with pytest.raises(AssertionError, match="SMTP daemon not running"):
            controller.stop(no_assert=False)

    def test_stop_noassert(self):
        controller = Controller(Sink())
        controller.stop(no_assert=True)


@pytest.mark.skipif(in_cygwin(), reason="Cygwin AF_UNIX is problematic")
@pytest.mark.skipif(in_win32(), reason="Win32 does not yet fully implement AF_UNIX")
class TestUnixSocketController:
    sockfile: Path = None

    def _assert_good_server(self, ssl_context: ssl.SSLContext = None):
        # Note: all those time.sleep()s are necessary
        # Remember that we're running in "Threaded" mode, and there's the GIL...
        # The time.sleep()s lets go of the GIL allowing the asyncio loop to move
        # forward
        assert self.sockfile.exists()
        with ExitStack() as stk:
            sock: socket.socket = stk.enter_context(
                socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            )
            sock.connect(str(self.sockfile))
            if ssl_context:
                sock = stk.enter_context(ssl_context.wrap_socket(sock))
                time.sleep(0.1)
            resp = sock.recv(1024)
            assert resp.startswith(b"220 ")
            assert resp.endswith(b"\r\n")
            sock.send(b"EHLO socket.test\r\n")
            # We need to "build" resparr because, especially when socket is wrapped
            # in SSL, the SMTP server takes it sweet time responding with the list
            # of ESMTP features ...
            resparr = bytearray()
            while not resparr.endswith(b"250 HELP\r\n"):
                time.sleep(0.1)
                resp = sock.recv(1024)
                if not resp:
                    break
                resparr += resp
            assert resparr.endswith(b"250 HELP\r\n")
            sock.send(b"QUIT\r\n")
            time.sleep(0.1)
            resp = sock.recv(1024)
            assert resp.startswith(b"221")

    def test_server_creation(self, safe_socket_dir):
        self.sockfile = safe_socket_dir / "smtp"
        cont = UnixSocketController(Sink(), unix_socket=self.sockfile)
        try:
            cont.start()
            self._assert_good_server()
        finally:
            cont.stop()

    def test_server_creation_ssl(self, safe_socket_dir, ssl_context_server):
        self.sockfile = safe_socket_dir / "smtp"
        cont = UnixSocketController(
            Sink(), unix_socket=self.sockfile, ssl_context=ssl_context_server
        )
        try:
            cont.start()
            # Allow additional time for SSL to kick in
            time.sleep(0.1)
            self._assert_good_server(ssl_context_server)
        finally:
            cont.stop()


class TestFactory:
    def test_normal_situation(self):
        cont = Controller(Sink())
        try:
            cont.start()
            assert cont.smtpd is not None
            assert cont._thread_exception is None
        finally:
            cont.stop()

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    def test_unknown_args_direct(self, silence_event_loop_closed):
        unknown = "this_is_an_unknown_kwarg"
        cont = Controller(Sink(), ready_timeout=0.3, **{unknown: True})
        expectedre = r"__init__.. got an unexpected keyword argument '" + unknown + r"'"
        try:
            with pytest.raises(TypeError, match=expectedre):
                cont.start()
            assert cont.smtpd is None
            assert isinstance(cont._thread_exception, TypeError)
        finally:
            cont.stop()

    @pytest.mark.filterwarnings(
        "ignore:server_kwargs will be removed:DeprecationWarning"
    )
    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    def test_unknown_args_inkwargs(self, silence_event_loop_closed):
        unknown = "this_is_an_unknown_kwarg"
        cont = Controller(Sink(), ready_timeout=0.3, server_kwargs={unknown: True})
        expectedre = r"__init__.. got an unexpected keyword argument '" + unknown + r"'"
        try:
            with pytest.raises(TypeError, match=expectedre):
                cont.start()
            assert cont.smtpd is None
        finally:
            cont.stop()

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    def test_factory_none(self, mocker: MockFixture, silence_event_loop_closed):
        # Hypothetical situation where factory() did not raise an Exception
        # but returned None instead
        mocker.patch("aiosmtpd.controller.SMTP", return_value=None)
        cont = Controller(Sink(), ready_timeout=0.3)
        expectedre = r"factory\(\) returned None"
        try:
            with pytest.raises(RuntimeError, match=expectedre):
                cont.start()
            assert cont.smtpd is None
        finally:
            cont.stop()

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    def test_noexc_smtpd_missing(self, mocker, silence_event_loop_closed):
        # Hypothetical situation where factory() failed but no
        # Exception was generated.
        cont = Controller(Sink())

        def hijacker(*args, **kwargs):
            cont._thread_exception = None
            # Must still return an (unmocked) _FakeServer to prevent a whole bunch
            # of messy exceptions, although they doesn't affect the test at all.
            return _FakeServer(cont.loop)

        mocker.patch("aiosmtpd.controller._FakeServer", side_effect=hijacker)
        mocker.patch(
            "aiosmtpd.controller.SMTP", side_effect=RuntimeError("Simulated Failure")
        )

        expectedre = r"Unknown Error, failed to init SMTP server"
        try:
            with pytest.raises(RuntimeError, match=expectedre):
                cont.start()
            assert cont.smtpd is None
            assert cont._thread_exception is None
        finally:
            cont.stop()


class TestCompat:
    def test_version(self):
        from aiosmtpd import __version__ as init_version
        from aiosmtpd.smtp import __version__ as smtp_version

        assert smtp_version is init_version
