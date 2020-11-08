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


class TestServerNieuw:
    def test_smtp_utf8(self, base_controller, client):
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

    @pytest.mark.skipif(in_wsl(), reason="WSL prevents socket collision")
    def test_socket_error_dupe(self, base_controller, client):
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
