# Copyright 2014-2026 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Behavioural tests for the ``examples/authenticated_relayer`` example."""

import importlib.util
import os
import runpy
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

import aiosmtpd
from aiosmtpd.smtp import AuthResult, LoginPassword

# The examples are not part of the installed package; they only exist in a
# repo checkout.  Skip cleanly when running from an installed aiosmtpd.
_REPO_ROOT = Path(aiosmtpd.__file__).resolve().parent.parent
RELAYER_DIR = _REPO_ROOT / "examples" / "authenticated_relayer"
if not (RELAYER_DIR / "server.py").exists():
    pytest.skip(
        "examples/authenticated_relayer not present (not a repo checkout)",
        allow_module_level=True,
    )

# server.py imports dns.resolver at module level (for the relaying half,
# which these tests do not touch).
pytest.importorskip("dns.resolver")


def _load_example(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, RELAYER_DIR / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


make_user_db = _load_example("authrelay_make_user_db", "make_user_db.py")
server = _load_example("authrelay_server", "server.py")

USERS: dict = make_user_db.USER_AND_PASSWORD

#: Calls the example Authenticator with a mechanism and auth_data.
Authenticate = Callable[[str, Any], AuthResult]
A_USER, A_PASSWORD = next(iter(USERS.items()))


@pytest.fixture(scope="module")
def user_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the user DB once, with the real make_user_db.py script."""
    db_dir = tmp_path_factory.mktemp("authrelay")
    prev_cwd = os.getcwd()
    os.chdir(db_dir)
    try:
        runpy.run_path(str(RELAYER_DIR / "make_user_db.py"), run_name="__main__")
    finally:
        os.chdir(prev_cwd)
    return db_dir / make_user_db.DB_FILE


@pytest.fixture(scope="module")
def authenticate(user_db: Path) -> Authenticate:
    authenticator = server.Authenticator(user_db)

    def do_auth(mechanism: str, auth_data: Any) -> AuthResult:
        return authenticator(None, None, None, mechanism, auth_data)

    return do_auth


@pytest.mark.parametrize("mechanism", ["LOGIN", "PLAIN"])
def test_correct_password_accepted(authenticate: Authenticate, mechanism: str) -> None:
    result = authenticate(
        mechanism, LoginPassword(A_USER.encode("utf-8"), A_PASSWORD)
    )
    assert result.success is True


def test_all_created_users_can_authenticate(authenticate: Authenticate) -> None:
    # The salt written by make_user_db.py must round-trip through the DB
    # into verification for every user, not just the first row.
    last_user, last_password = list(USERS.items())[-1]
    result = authenticate(
        "LOGIN", LoginPassword(last_user.encode("utf-8"), last_password)
    )
    assert result.success is True


def test_wrong_password_rejected(authenticate: Authenticate) -> None:
    result = authenticate(
        "LOGIN", LoginPassword(A_USER.encode("utf-8"), b"hunter2")
    )
    assert result.success is False
    assert result.handled is False


def test_unknown_user_rejected(authenticate: Authenticate) -> None:
    result = authenticate(
        "LOGIN", LoginPassword(b"nonexistent", A_PASSWORD)
    )
    assert result.success is False
    assert result.handled is False


def test_unsupported_mechanism_rejected(authenticate: Authenticate) -> None:
    result = authenticate(
        "CRAM-MD5", LoginPassword(A_USER.encode("utf-8"), A_PASSWORD)
    )
    assert result.success is False
    assert result.handled is False


def test_non_loginpassword_auth_data_rejected(authenticate: Authenticate) -> None:
    result = authenticate("LOGIN", (A_USER, A_PASSWORD))
    assert result.success is False
    assert result.handled is False


def test_non_utf8_username_rejected_not_crashing(authenticate: Authenticate) -> None:
    result = authenticate(
        "LOGIN", LoginPassword(b"\xff\xfe" + A_USER.encode("utf-8"), A_PASSWORD)
    )
    assert result.success is False
    assert result.handled is False


def test_stale_database_rejects_without_leaking(
    authenticate: Authenticate, tmp_path: Path
) -> None:
    """A DB predating the salt column must not escape as an exception.

    The schema change means anyone with an existing mail.db~ hits this on
    every attempt; letting sqlite3.OperationalError propagate turns it into
    a 500 quoting the schema back at an unauthenticated client.
    """
    stale = tmp_path / "stale.db"
    conn = sqlite3.connect(stale)
    conn.execute("CREATE TABLE userauth (username text, hashpass text)")
    conn.execute("INSERT INTO userauth VALUES (?, ?)", (A_USER, "deadbeef"))
    conn.commit()
    conn.close()

    result = server.Authenticator(stale)(
        None, None, None, "LOGIN", LoginPassword(A_USER.encode("utf-8"), A_PASSWORD)
    )
    assert result.success is False
    assert result.handled is False


def test_unknown_user_still_hashes(
    authenticate: Authenticate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing row must cost a hash, or rejection latency leaks usernames.

    Asserted by counting the hash rather than timing it, so the test cannot
    flake on a loaded machine.
    """
    calls: list[int] = []
    real = server.pbkdf2_hmac

    def counting(*args: Any, **kwargs: Any) -> bytes:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(server, "pbkdf2_hmac", counting)
    result = authenticate("LOGIN", LoginPassword(b"nonexistent", A_PASSWORD))

    assert result.success is False
    assert calls, "unknown user returned without hashing; timing reveals it exists"


def test_corrupt_salt_rejects_without_raising(tmp_path: Path) -> None:
    """A row whose salt is not hex is a data error, not a credential error."""
    corrupt = tmp_path / "corrupt.db"
    conn = sqlite3.connect(corrupt)
    conn.execute("CREATE TABLE userauth (username text, salt text, hashpass text)")
    conn.execute(
        "INSERT INTO userauth VALUES (?, ?, ?)", (A_USER, "not-hex!", "deadbeef")
    )
    conn.commit()
    conn.close()

    result = server.Authenticator(corrupt)(
        None, None, None, "LOGIN", LoginPassword(A_USER.encode("utf-8"), A_PASSWORD)
    )
    assert result.success is False
    assert result.handled is False


def test_undecodable_login_cannot_match_a_replacement_char_user(
    tmp_path: Path,
) -> None:
    """Decoding with errors="replace" would let one login match another user.

    Every invalid byte becomes U+FFFD, so a login of b"\\xff" would match a
    stored user named U+FFFD. Strict decoding rejects it instead.
    """
    salt = bytes(32)
    db = tmp_path / "replacement.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE userauth (username text, salt text, hashpass text)")
    conn.execute(
        "INSERT INTO userauth VALUES (?, ?, ?)",
        (
            "\ufffd",
            salt.hex(),
            server.pbkdf2_hmac(
                "sha256", b"secret", salt, server.HASH_ITERATIONS
            ).hex(),
        ),
    )
    conn.commit()
    conn.close()

    result = server.Authenticator(db)(
        None, None, None, "LOGIN", LoginPassword(b"\xff", b"secret")
    )
    assert result.success is False
    assert result.handled is False
