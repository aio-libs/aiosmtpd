"""Test other aspects of the server implementation."""

import os
import pytest
import socket

from .conftest import Global
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink
from aiosmtpd.smtp import SMTP as Server


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
