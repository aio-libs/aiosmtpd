# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
import os
from typing import Generator

import pytest

from aiosmtpd.main import main, parseargs
from aiosmtpd.smtp import __version__

try:
    import pwd
except ImportError:
    pwd = None

HAS_SETUID = hasattr(os, "setuid")
MAIL_LOG = logging.getLogger("mail.log")


class FromCliHandler:
    def __init__(self, called):
        self.called = called

    @classmethod
    def from_cli(cls, parser, *args):
        return cls(*args)


class NullHandler:
    pass


# region ##### Fixtures #######################################################


@pytest.fixture
def autostop_loop(temp_event_loop) -> Generator[asyncio.AbstractEventLoop, None, None]:
    # Create a new event loop, and arrange for that loop to end almost
    # immediately.  This will allow the calls to main() in these tests to
    # also exit almost immediately.  Otherwise, the foreground test
    # process will hang.
    #
    # If less than 1.0, might cause intermittent error if test system
    # is too busy/overloaded.
    temp_event_loop.call_later(1.0, temp_event_loop.stop)
    #
    yield temp_event_loop


@pytest.fixture
def nobody_uid() -> Generator[int, None, None]:
    if pwd is None:
        pytest.skip("No pwd module available")
    try:
        pw = pwd.getpwnam("nobody")
    except KeyError:
        pytest.skip("'nobody' not available")
    else:
        yield pw.pw_uid


@pytest.fixture
def setuid(mocker):
    if not HAS_SETUID:
        pytest.skip("setuid is unavailable")
    mocker.patch("aiosmtpd.main.pwd", None)
    mocker.patch("os.setuid", side_effect=PermissionError)
    mocker.patch("aiosmtpd.main.partial", side_effect=RuntimeError)
    #
    yield


# endregion


@pytest.mark.usefixtures("autostop_loop")
class TestMain:
    def test_setuid(self, nobody_uid, mocker):
        mock = mocker.patch("os.setuid")
        main(args=())
        mock.assert_called_with(nobody_uid)

    def test_setuid_permission_error(self, nobody_uid, mocker, capsys):
        mock = mocker.patch("os.setuid", side_effect=PermissionError)
        with pytest.raises(SystemExit) as excinfo:
            main(args=())
        assert excinfo.value.code == 1
        mock.assert_called_with(nobody_uid)
        assert (
            capsys.readouterr().err
            == 'Cannot setuid "nobody"; try running with -n option.\n'
        )

    def test_setuid_no_pwd_module(self, nobody_uid, mocker, capsys):
        mocker.patch("aiosmtpd.main.pwd", None)
        with pytest.raises(SystemExit) as excinfo:
            main(args=())
        assert excinfo.value.code == 1
        # On Python 3.8 on Linux, a bunch of "RuntimeWarning: coroutine
        # 'AsyncMockMixin._execute_mock_call' was never awaited" messages
        # gets mixed up into stderr causing test fail.
        # Therefore, we use assertIn instead of assertEqual here, because
        # the string DOES appear in stderr, just buried.
        assert (
            'Cannot import module "pwd"; try running with -n option.\n'
            in capsys.readouterr().err
        )

    def test_n(self, setuid):
        with pytest.raises(RuntimeError):
            main(("-n",))

    def test_nosetuid(self, setuid):
        with pytest.raises(RuntimeError):
            main(("--nosetuid",))

    def test_debug_0(self):
        # For this test, the test runner likely has already set the log level
        # so it may not be logging.ERROR.
        default_level = MAIL_LOG.getEffectiveLevel()
        main(("-n",))
        assert MAIL_LOG.getEffectiveLevel() == default_level

    def test_debug_1(self):
        main(("-n", "-d"))
        assert MAIL_LOG.getEffectiveLevel() == logging.INFO

    def test_debug_2(self):
        main(("-n", "-dd"))
        assert MAIL_LOG.getEffectiveLevel() == logging.DEBUG

    def test_debug_3(self):
        main(("-n", "-ddd"))
        assert MAIL_LOG.getEffectiveLevel() == logging.DEBUG
        assert asyncio.get_event_loop().get_debug()


class TestParseArgs:
    def test_handler_from_cli(self):
        parser, args = parseargs(
            ("-c", "aiosmtpd.tests.test_main.FromCliHandler", "--", "FOO")
        )
        assert isinstance(args.handler, FromCliHandler)
        assert args.handler.called == "FOO"

    def test_handler_no_from_cli(self):
        parser, args = parseargs(("-c", "aiosmtpd.tests.test_main.NullHandler"))
        assert isinstance(args.handler, NullHandler)

    def test_handler_from_cli_exception(self):
        with pytest.raises(TypeError):
            parseargs(("-c", "aiosmtpd.tests.test_main.FromCliHandler", "FOO", "BAR"))

    def test_handler_no_from_cli_exception(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            parseargs(("-c", "aiosmtpd.tests.test_main.NullHandler", "FOO", "BAR"))
        assert excinfo.value.code == 2
        assert (
            "Handler class aiosmtpd.tests.test_main takes no arguments"
            in capsys.readouterr().err
        )

    @pytest.mark.parametrize(
        "args, exp_host, exp_port",
        [
            ((), "localhost", 8025),
            (("-l", "foo:25"), "foo", 25),
            (("--listen", "foo:25"), "foo", 25),
            (("-l", "foo"), "foo", 8025),
            (("-l", ":25"), "localhost", 25),
            (("-l", "::0:25"), "::0", 25),
        ],
    )
    def test_host_port(self, args, exp_host, exp_port):
        parser, args_ = parseargs(args=args)
        assert args_.host == exp_host
        assert args_.port == exp_port

    def test_bad_port_number(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            parseargs(("-l", ":foo"))
        assert excinfo.value.code == 2
        assert "Invalid port number: foo" in capsys.readouterr().err

    @pytest.mark.parametrize("opt", ["--version", "-v"])
    def test_version(self, capsys, mocker, opt):
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        with pytest.raises(SystemExit) as excinfo:
            parseargs((opt,))
        assert excinfo.value.code == 0
        assert capsys.readouterr().out == f"smtpd {__version__}\n"


class TestSigint:
    def test_keyboard_interrupt(self, temp_event_loop):
        """main() must close loop gracefully on KeyboardInterrupt."""

        def interrupt():
            raise KeyboardInterrupt

        temp_event_loop.call_later(1.0, interrupt)
        try:
            main(("-n",))
        except Exception:
            pytest.fail("main() should've closed cleanly without exceptions!")
        else:
            assert not temp_event_loop.is_running()
