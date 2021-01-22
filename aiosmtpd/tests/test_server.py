"""Test other aspects of the server implementation."""

import os
import pytest
import socket

from .conftest import Global
from aiosmtpd.controller import Controller, _FakeServer
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server

from pytest_mock import MockFixture


def in_wsl():
    # WSL 1.0 somehow allows more than one listener on one port.
    # So when testing on WSL, we must set PLATFORM=wsl and skip the
    # "test_socket_error" test.
    return os.environ.get("PLATFORM") == "wsl"


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
        with pytest.warns(UserWarning) as record:
            Server(Sink(), auth_require_tls=False, auth_required=True)
        assert len(record) == 1
        assert (
            record[0].message.args[0]
            == "Requiring AUTH while not requiring TLS can lead to "
            "security vulnerabilities!"
        )


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
        try:
            with pytest.raises(socket.error):
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


class TestFactory:
    def test_normal_situation(self):
        cont = Controller(Sink())
        try:
            cont.start()
            assert cont.smtpd is not None
            assert cont._thread_exception is None
        finally:
            cont.stop()

    def test_unknown_args(self):
        unknown = "this_is_an_unknown_kwarg"
        cont = Controller(Sink(), **{unknown: True})
        try:
            with pytest.raises(TypeError) as exc:
                cont.start()
            assert cont.smtpd is None
            excm = str(exc.value)
            assert "unexpected keyword" in excm
            assert unknown in excm
        finally:
            cont.stop()

    def test_factory_none(self, mocker: MockFixture):
        # Hypothetical situation where factory() did not raise an Exception
        # but returned None instead
        mocker.patch("aiosmtpd.controller.SMTP", return_value=None)
        cont = Controller(Sink())
        try:
            with pytest.raises(RuntimeError) as exc:
                cont.start()
            assert cont.smtpd is None
            assert str(exc.value) == "factory() returned None"
        finally:
            cont.stop()

    def test_noexc_smtpd_missing(self, mocker):
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

        try:
            with pytest.raises(RuntimeError) as exc:
                cont.start()
            assert cont.smtpd is None
            assert cont._thread_exception is None
            assert str(exc.value) == "Unknown Error, failed to init SMTP server"
        finally:
            cont.stop()
