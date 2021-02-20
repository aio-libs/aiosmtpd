# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
import multiprocessing as MP
import os
import time
from ctypes import c_bool
from smtplib import SMTP as SMTPClient
from smtplib import SMTP_SSL
from typing import Generator

import pytest

from aiosmtpd.handlers import Debugging
from aiosmtpd.main import main, parseargs
from aiosmtpd.smtp import __version__
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S
from aiosmtpd.tests.conftest import SERVER_CRT, SERVER_KEY

try:
    import pwd
except ImportError:
    pwd = None

HAS_SETUID = hasattr(os, "setuid")
MAIL_LOG = logging.getLogger("mail.log")

# If less than 1.0, might cause intermittent error if test system
# is too busy/overloaded.
AUTOSTOP_DELAY = 2.0


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
    temp_event_loop.call_later(AUTOSTOP_DELAY, temp_event_loop.stop)
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


def watch_for_tls(has_tls, req_tls):
    has_tls.value = False
    req_tls.value = False
    start = time.monotonic()
    while (time.monotonic() - start) <= AUTOSTOP_DELAY:
        try:
            with SMTPClient("localhost", 8025) as client:
                resp = client.docmd("HELP", "HELO")
                if resp == S.S530_STARTTLS_FIRST:
                    req_tls.value = True
                client.ehlo("exemple.org")
                if "starttls" in client.esmtp_features:
                    has_tls.value = True
                return
        except Exception:
            time.sleep(0.05)


def watch_for_smtps(result):
    start = time.monotonic()
    while (time.monotonic() - start) <= AUTOSTOP_DELAY:
        try:
            with SMTP_SSL("localhost", 8025) as client:
                client.ehlo("exemple.org")
                result.value = True
                return
        except Exception:
            time.sleep(0.05)


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

    def test_tls(self):
        has_starttls = MP.Value(c_bool)
        require_tls = MP.Value(c_bool)
        p = MP.Process(target=watch_for_tls, args=(has_starttls, require_tls))
        p.start()
        main(("-n", "--tlscert", str(SERVER_CRT), "--tlskey", str(SERVER_KEY)))
        p.join()
        assert has_starttls.value is True
        assert require_tls.value is True

    def test_tls_noreq(self):
        has_starttls = MP.Value(c_bool)
        require_tls = MP.Value(c_bool)
        p = MP.Process(target=watch_for_tls, args=(has_starttls, require_tls))
        p.start()
        main(
            (
                "-n",
                "--tlscert",
                str(SERVER_CRT),
                "--tlskey",
                str(SERVER_KEY),
                "--no-requiretls",
            )
        )
        p.join()
        assert has_starttls.value is True
        assert require_tls.value is False

    def test_smtps(self):
        has_smtps = MP.Value(c_bool)
        p = MP.Process(target=watch_for_smtps, args=(has_smtps,))
        p.start()
        main(("-n", "--smtpscert", str(SERVER_CRT), "--smtpskey", str(SERVER_KEY)))
        p.join()
        assert has_smtps.value is True


class TestParseArgs:
    def test_defaults(self):
        parser, args = parseargs(tuple())
        assert args.classargs == tuple()
        assert args.classpath == "aiosmtpd.handlers.Debugging"
        assert args.debug == 0
        assert isinstance(args.handler, Debugging)
        assert args.host == "localhost"
        assert args.listen is None
        assert args.port == 8025
        assert args.setuid is True
        assert args.size is None
        assert args.smtputf8 is False
        assert args.smtpscert is None
        assert args.smtpskey is None
        assert args.tlscert is None
        assert args.tlskey is None
        assert args.requiretls is True

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

    @pytest.mark.parametrize("args", [("--smtpscert", "x"), ("--smtpskey", "x")])
    def test_smtps(self, capsys, mocker, args):
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        with pytest.raises(SystemExit) as exc:
            parseargs(args)
        assert exc.value.code == 2
        assert (
            "--smtpscert and --smtpskey must be specified together"
            in capsys.readouterr().err
        )

    @pytest.mark.parametrize("args", [("--tlscert", "x"), ("--tlskey", "x")])
    def test_tls(self, capsys, mocker, args):
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        with pytest.raises(SystemExit) as exc:
            parseargs(args)
        assert exc.value.code == 2
        assert (
            "--tlscert and --tlskey must be specified together"
            in capsys.readouterr().err
        )

    def test_norequiretls(self, capsys, mocker):
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        parser, args = parseargs(("--no-requiretls",))
        assert args.requiretls is False

    @pytest.mark.parametrize(
        "certfile, keyfile, expect",
        [
            ("x", "x", "Cert file x not found"),
            (SERVER_CRT, "x", "Key file x not found"),
            ("x", SERVER_KEY, "Cert file x not found"),
        ],
        ids=["x-x", "cert-x", "x-key"],
    )
    @pytest.mark.parametrize("meth", ["smtps", "tls"])
    def test_ssl_files_err(self, capsys, mocker, meth, certfile, keyfile, expect):
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        with pytest.raises(SystemExit) as exc:
            parseargs((f"--{meth}cert", certfile, f"--{meth}key", keyfile))
        assert exc.value.code == 2
        assert expect in capsys.readouterr().err


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
