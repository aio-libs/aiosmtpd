# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
import multiprocessing as MP
import os
import sys
import time
from argparse import ArgumentParser
from contextlib import contextmanager
from multiprocessing.synchronize import Event as MP_Event
from smtplib import SMTP as SMTPClient
from smtplib import SMTP_SSL
from typing import Callable, Generator, Tuple

import pytest
from pytest_mock import MockFixture

from aiosmtpd import __version__
from aiosmtpd.handlers import Debugging
from aiosmtpd.main import main, parseargs
from aiosmtpd.testing.helpers import catchup_delay
from aiosmtpd.testing.statuscodes import SMTP_STATUS_CODES as S
from aiosmtpd.tests.conftest import AUTOSTOP_DELAY

try:
    import pwd
except ImportError:
    pwd = None

HAS_SETUID = hasattr(os, "setuid")
MAIL_LOG = logging.getLogger("mail.log")


# region ##### Custom Handlers ########################################################


class FromCliHandler:
    def __init__(self, called: bool) -> None:
        self.called = called

    @classmethod
    def from_cli(cls, parser: ArgumentParser, *args: str) -> "FromCliHandler":
        return cls(*args)


class NullHandler:
    pass


# endregion

# region ##### Fixtures ###############################################################


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
def setuid(mocker: MockFixture) -> None:
    if not HAS_SETUID:
        pytest.skip("setuid is unavailable")
    mocker.patch("aiosmtpd.main.pwd", None)
    mocker.patch("os.setuid", side_effect=PermissionError)
    mocker.patch("aiosmtpd.main.partial", side_effect=RuntimeError)


# endregion

# region ##### Helper Funcs ###########################################################


def watch_for_tls(ready_flag: MP_Event, retq: MP.Queue) -> None:
    has_tls = False
    req_tls = False
    ready_flag.set()
    start = time.monotonic()
    delay = AUTOSTOP_DELAY * 4
    while (time.monotonic() - start) <= delay:
        try:
            with SMTPClient("localhost", 8025, timeout=0.1) as client:
                resp = client.docmd("HELP", "HELO")
                if resp == S.S530_STARTTLS_FIRST:
                    req_tls = True
                client.ehlo("exemple.org")
                if "starttls" in client.esmtp_features:
                    has_tls = True
                break
        except Exception:
            time.sleep(0.05)
    retq.put(has_tls)
    retq.put(req_tls)


def watch_for_smtps(ready_flag: MP_Event, retq: MP.Queue) -> None:
    has_smtps = False
    ready_flag.set()
    start = time.monotonic()
    delay = AUTOSTOP_DELAY * 1.5
    while (time.monotonic() - start) <= delay:
        try:
            with SMTP_SSL("localhost", 8025, timeout=0.1) as client:
                client.ehlo("exemple.org")
                has_smtps = True
                break
        except Exception:
            time.sleep(0.05)
    retq.put(has_smtps)


def main_n(*args) -> None:
    main(("-n",) + args)


@contextmanager
def watcher_process(func: Callable) -> Generator[MP.Queue, None, None]:
    redy = MP.Event()
    retq = MP.Queue()
    proc = MP.Process(target=func, args=(redy, retq))
    proc.start()
    redy.wait()
    yield retq
    proc.join()


# endregion


@pytest.mark.usefixtures("autostop_loop")
class TestMain:
    def test_setuid(self, nobody_uid: int, mocker: MockFixture) -> None:
        mock = mocker.patch("os.setuid")
        main(args=())
        mock.assert_called_with(nobody_uid)

    def test_setuid_permission_error(
        self, nobody_uid: int, mocker: MockFixture, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock = mocker.patch("os.setuid", side_effect=PermissionError)
        with pytest.raises(SystemExit) as excinfo:
            main(args=())
        assert excinfo.value.code == 1
        mock.assert_called_with(nobody_uid)
        assert (
            capsys.readouterr().err
            == 'Cannot setuid "nobody"; try running with -n option.\n'
        )

    def test_setuid_no_pwd_module(
        self, nobody_uid: int, mocker: MockFixture, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mocker.patch("aiosmtpd.main.pwd", None)
        with pytest.raises(SystemExit) as excinfo:
            main(args=())
        assert excinfo.value.code == 1
        assert capsys.readouterr().err == 'Cannot import module "pwd"; try running with -n option.\n'

    def test_n(self, setuid: None) -> None:
        with pytest.raises(RuntimeError):
            main_n()

    def test_nosetuid(self, setuid: None) -> None:
        with pytest.raises(RuntimeError):
            main(("--nosetuid",))

    def test_debug_0(self) -> None:
        # For this test, the test runner likely has already set the log level
        # so it may not be logging.ERROR.
        default_level = MAIL_LOG.getEffectiveLevel()
        main_n()
        assert MAIL_LOG.getEffectiveLevel() == default_level

    def test_debug_1(self) -> None:
        main_n("-d")
        assert MAIL_LOG.getEffectiveLevel() == logging.INFO

    def test_debug_2(self) -> None:
        main_n("-dd")
        assert MAIL_LOG.getEffectiveLevel() == logging.DEBUG

    def test_debug_3(self) -> None:
        main_n("-ddd")
        assert MAIL_LOG.getEffectiveLevel() == logging.DEBUG
        assert asyncio.get_event_loop().get_debug()


@pytest.mark.skipif(sys.platform == "darwin", reason="No idea why these are failing")
class TestMainByWatcher:
    def test_tls(
        self,
        temp_event_loop: asyncio.AbstractEventLoop,
        tls_cert_pem_path: str,
        tls_key_pem_path: str,
    ) -> None:
        with watcher_process(watch_for_tls) as retq:
            temp_event_loop.call_later(AUTOSTOP_DELAY, temp_event_loop.stop)
            main_n("--tlscert", tls_cert_pem_path, "--tlskey", tls_key_pem_path)
            catchup_delay()
        has_starttls = retq.get()
        assert has_starttls is True
        require_tls = retq.get()
        assert require_tls is True

    def test_tls_noreq(
        self,
        temp_event_loop: asyncio.AbstractEventLoop,
        tls_cert_pem_path: str,
        tls_key_pem_path: str,
    ) -> None:
        with watcher_process(watch_for_tls) as retq:
            temp_event_loop.call_later(AUTOSTOP_DELAY, temp_event_loop.stop)
            main_n(
                "--tlscert",
                tls_cert_pem_path,
                "--tlskey",
                tls_key_pem_path,
                "--no-requiretls",
            )
            catchup_delay()
        has_starttls = retq.get()
        assert has_starttls is True
        require_tls = retq.get()
        assert require_tls is False

    def test_smtps(
        self,
        temp_event_loop: asyncio.AbstractEventLoop,
        tls_cert_pem_path: str,
        tls_key_pem_path: str,
    ) -> None:
        with watcher_process(watch_for_smtps) as retq:
            temp_event_loop.call_later(AUTOSTOP_DELAY, temp_event_loop.stop)
            main_n("--smtpscert", tls_cert_pem_path, "--smtpskey", tls_key_pem_path)
            catchup_delay()
        has_smtps = retq.get()
        assert has_smtps is True


class TestParseArgs:
    def test_defaults(self) -> None:
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

    def test_handler_from_cli(self) -> None:
        parser, args = parseargs(
            ("-c", "aiosmtpd.tests.test_main.FromCliHandler", "--", "FOO")
        )
        assert isinstance(args.handler, FromCliHandler)
        assert args.handler.called == "FOO"

    def test_handler_no_from_cli(self) -> None:
        parser, args = parseargs(("-c", "aiosmtpd.tests.test_main.NullHandler"))
        assert isinstance(args.handler, NullHandler)

    def test_handler_from_cli_exception(self) -> None:
        with pytest.raises(TypeError):
            parseargs(("-c", "aiosmtpd.tests.test_main.FromCliHandler", "FOO", "BAR"))

    def test_handler_no_from_cli_exception(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            parseargs(("-c", "aiosmtpd.tests.test_main.NullHandler", "FOO", "BAR"))
        assert excinfo.value.code == 2
        assert (
            "Handler class aiosmtpd.tests.test_main takes no arguments"
            in capsys.readouterr().err
        )

    @pytest.mark.parametrize(
        ("args", "exp_host", "exp_port"),
        [
            ((), "localhost", 8025),
            (("-l", "foo:25"), "foo", 25),
            (("--listen", "foo:25"), "foo", 25),
            (("-l", "foo"), "foo", 8025),
            (("-l", ":25"), "localhost", 25),
            (("-l", "::0:25"), "::0", 25),
        ],
    )
    def test_host_port(self, args: Tuple, exp_host: str, exp_port: int) -> None:
        parser, args_ = parseargs(args=args)
        assert args_.host == exp_host
        assert args_.port == exp_port

    def test_bad_port_number(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            parseargs(("-l", ":foo"))
        assert excinfo.value.code == 2
        assert "Invalid port number: foo" in capsys.readouterr().err

    @pytest.mark.parametrize("opt", ["--version", "-v"])
    def test_version(self, capsys: pytest.CaptureFixture[str], mocker: MockFixture, opt: str) -> None:
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        with pytest.raises(SystemExit) as excinfo:
            parseargs((opt,))
        assert excinfo.value.code == 0
        assert capsys.readouterr().out == f"smtpd {__version__}\n"

    @pytest.mark.parametrize("args", [("--smtpscert", "x"), ("--smtpskey", "x")])
    def test_smtps(self, capsys: pytest.CaptureFixture[str], mocker: MockFixture, args: Tuple) -> None:
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        with pytest.raises(SystemExit) as exc:
            parseargs(args)
        assert exc.value.code == 2
        assert (
            "--smtpscert and --smtpskey must be specified together"
            in capsys.readouterr().err
        )

    @pytest.mark.parametrize("args", [("--tlscert", "x"), ("--tlskey", "x")])
    def test_tls(self, capsys: pytest.CaptureFixture[str], mocker: MockFixture, args: Tuple) -> None:
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        with pytest.raises(SystemExit) as exc:
            parseargs(args)
        assert exc.value.code == 2
        assert (
            "--tlscert and --tlskey must be specified together"
            in capsys.readouterr().err
        )

    def test_norequiretls(self, capsys: pytest.CaptureFixture[str], mocker: MockFixture) -> None:
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        parser, args = parseargs(("--no-requiretls",))
        assert args.requiretls is False

    @pytest.mark.parametrize(
        ("certfile_present", "keyfile_present", "expect"),
        [
            (False, False, "Cert file x not found"),
            (True, False, "Key file x not found"),
            (False, True, "Cert file x not found"),
        ],
        ids=["x-x", "cert-x", "x-key"],
    )
    @pytest.mark.parametrize("meth", ["smtps", "tls"])
    def test_ssl_files_err(
        self,
        capsys: pytest.CaptureFixture[str],
        mocker: MockFixture,
        meth: str,
        certfile_present: bool,
        keyfile_present: bool,
        expect: str,
        request: pytest.FixtureRequest,
    ) -> None:
        certfile = request.getfixturevalue("tls_cert_pem_path") if certfile_present else "x"
        keyfile = request.getfixturevalue("tls_key_pem_path") if keyfile_present else "x"
        mocker.patch("aiosmtpd.main.PROGRAM", "smtpd")
        with pytest.raises(SystemExit) as exc:
            parseargs((f"--{meth}cert", certfile, f"--{meth}key", keyfile))
        assert exc.value.code == 2
        assert expect in capsys.readouterr().err


class TestSigint:
    def test_keyboard_interrupt(self, temp_event_loop: asyncio.AbstractEventLoop) -> None:
        """main() must close loop gracefully on KeyboardInterrupt."""

        def interrupt() -> None:
            raise KeyboardInterrupt

        temp_event_loop.call_later(1.0, interrupt)
        try:
            main_n()
        except Exception:
            pytest.fail("main() should've closed cleanly without exceptions!")
        else:
            assert not temp_event_loop.is_running()
