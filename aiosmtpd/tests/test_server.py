# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Test other aspects of the server implementation."""

import os
import pytest
import socket

from .conftest import Global
from aiosmtpd.controller import Controller, _FakeServer
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server
from functools import partial, wraps
from pytest_mock import MockFixture

try:
    from asyncio.proactor_events import _ProactorBasePipeTransport
    HAS_PROACTOR = True
except ImportError:
    _ProactorBasePipeTransport = None
    HAS_PROACTOR = False


def in_wsl():
    # WSL 1.0 somehow allows more than one listener on one port.
    # So when testing on WSL, we must set PLATFORM=wsl and skip the
    # "test_socket_error" test.
    return os.environ.get("PLATFORM") == "wsl"


# From: https://github.com/aio-libs/aiohttp/issues/4324#issuecomment-733884349
def _silencer(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except RuntimeError as e:
            if str(e) != "Event loop is closed":
                raise
    return wrapper


@pytest.fixture(scope="module")
def silence_event_loop_closed():
    if not HAS_PROACTOR:
        return False
    assert _ProactorBasePipeTransport is not None
    if hasattr(_ProactorBasePipeTransport, "old_del"):
        return True
    # noinspection PyUnresolvedReferences
    old_del = _ProactorBasePipeTransport.__del__
    _ProactorBasePipeTransport._old_del = old_del
    _ProactorBasePipeTransport.__del__ = _silencer(old_del)
    return True


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
        with pytest.warns(UserWarning, match=expectedre) as record:
            Server(Sink(), auth_require_tls=False, auth_required=True)


class TestController:
    """Tests for the aiosmtpd.controller.Controller class"""

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
        expectedre = (
            r"error while attempting to bind on address.*?"
            r"only one usage of each socket address"
        )
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

    def test_enablesmtputf8_flag(self, suppress_allwarnings):
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

    def test_serverhostname_arg(self, suppress_allwarnings):
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
        cont = Controller(Sink(), **{unknown: True})
        expectedre = (
            r"__init__.. got an unexpected keyword argument '"
            + unknown
            + r"'"
        )
        try:
            with pytest.raises(TypeError, match=expectedre):
                cont.start()
            assert cont.smtpd is None
            assert isinstance(cont._thread_exception, TypeError)
        finally:
            cont.stop()

    def test_unknown_args_inkwargs(
            self, suppress_allwarnings, silence_event_loop_closed
    ):
        unknown = "this_is_an_unknown_kwarg"
        cont = Controller(Sink(), server_kwargs={unknown: True})
        expectedre = (
            r"__init__.. got an unexpected keyword argument '"
            + unknown
            + r"'"
        )
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
        cont = Controller(Sink())
        expectedre = r"factory\(\) returned None"
        try:
            with pytest.raises(RuntimeError, match=expectedre) as exc:
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
            with pytest.raises(RuntimeError, match=expectedre) as exc:
                cont.start()
            assert cont.smtpd is None
            assert cont._thread_exception is None
        finally:
            cont.stop()
