# Copyright 2014-2026 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

"""Behavioural tests for the ``examples/authenticated_relayer`` example."""

import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

from aiosmtpd.smtp import AuthResult, LoginPassword

#: Calls the example Authenticator with a mechanism and auth_data.
Authenticate = Callable[[str, Any], AuthResult]


# Outside a repo checkout there are no examples to import, and server.py also
# needs dnspython for its relaying half; both skip. Any other import error in
# the examples still fails the tests.
@pytest.fixture(scope="module")
def make_user_db() -> ModuleType:
    pytest.importorskip("examples.authenticated_relayer")
    from examples.authenticated_relayer import make_user_db as module

    return module


@pytest.fixture(scope="module")
def server() -> ModuleType:
    pytest.importorskip("examples.authenticated_relayer")
    pytest.importorskip("dns.resolver")
    from examples.authenticated_relayer import server as module

    return module


@pytest.fixture(scope="module")
def users(make_user_db: ModuleType) -> dict[str, bytes]:
    return make_user_db.USER_AND_PASSWORD


@pytest.fixture(scope="module")
def known_user(users: dict[str, bytes]) -> tuple[str, bytes]:
    return next(iter(users.items()))


@pytest.fixture(scope="module")
def user_db(make_user_db: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the user DB once, with the real make_user_db.py script."""
    db_dir = tmp_path_factory.mktemp("authrelay")
    # Run it the way the example tells you to, in its own process, so the
    # suite never has to chdir out from under the tests that follow. The
    # command is this interpreter and the example's own file, hence the nosec.
    subprocess.run(  # nosec B603
        [sys.executable, make_user_db.__file__],
        cwd=db_dir,
        check=True,
        capture_output=True,
    )
    return db_dir / make_user_db.DB_FILE


@pytest.fixture(scope="module")
def authenticate(server: ModuleType, user_db: Path) -> Authenticate:
    authenticator = server.Authenticator(user_db)

    def do_auth(mechanism: str, auth_data: Any) -> AuthResult:
        return authenticator(None, None, None, mechanism, auth_data)

    return do_auth


@pytest.mark.parametrize("mechanism", ["LOGIN", "PLAIN"])
def test_correct_password_accepted(
    authenticate: Authenticate, known_user: tuple[str, bytes], mechanism: str
) -> None:
    user, password = known_user
    result = authenticate(mechanism, LoginPassword(user.encode("utf-8"), password))
    assert result.success is True


def test_all_created_users_can_authenticate(
    authenticate: Authenticate, users: dict[str, bytes]
) -> None:
    # The salt written by make_user_db.py must round-trip through the DB
    # into verification for every user, not just the first row.
    last_user, last_password = list(users.items())[-1]
    result = authenticate(
        "LOGIN", LoginPassword(last_user.encode("utf-8"), last_password)
    )
    assert result.success is True


def test_wrong_password_rejected(
    authenticate: Authenticate, known_user: tuple[str, bytes]
) -> None:
    user, _ = known_user
    result = authenticate("LOGIN", LoginPassword(user.encode("utf-8"), b"hunter2"))
    assert result.success is False
    assert result.handled is False


def test_unknown_user_rejected(
    authenticate: Authenticate, known_user: tuple[str, bytes]
) -> None:
    _, password = known_user
    result = authenticate("LOGIN", LoginPassword(b"nonexistent", password))
    assert result.success is False
    assert result.handled is False


def test_unsupported_mechanism_rejected(
    authenticate: Authenticate, known_user: tuple[str, bytes]
) -> None:
    user, password = known_user
    result = authenticate("CRAM-MD5", LoginPassword(user.encode("utf-8"), password))
    assert result.success is False
    assert result.handled is False


@pytest.mark.parametrize(
    "auth_data",
    [
        pytest.param(("user1", b"not@password"), id="not-a-LoginPassword"),
        # user1's real password behind invalid UTF-8, so a decoder that drops
        # the invalid bytes would log in instead of rejecting.
        pytest.param(
            LoginPassword(b"\xff\xfeuser1", b"not@password"), id="non-utf8-login"
        ),
    ],
)
def test_malformed_auth_data_rejected(
    authenticate: Authenticate, auth_data: Any
) -> None:
    """Malformed auth data is rejected rather than raising."""
    result = authenticate("LOGIN", auth_data)
    assert result.success is False
    assert result.handled is False


def test_stale_database_rejects_without_leaking(
    server: ModuleType, known_user: tuple[str, bytes], tmp_path: Path
) -> None:
    """A DB predating the salt column must not escape as an exception.

    The schema change means anyone with an existing mail.db~ hits this on
    every attempt; letting sqlite3.OperationalError propagate turns it into
    a 500 quoting the schema back at an unauthenticated client.
    """
    user, password = known_user
    stale = tmp_path / "stale.db"
    with closing(sqlite3.connect(stale)) as conn:
        conn.execute("CREATE TABLE userauth (username text, hashpass text)")
        conn.execute("INSERT INTO userauth VALUES (?, ?)", (user, "deadbeef"))
        conn.commit()

    result = server.Authenticator(stale)(
        None, None, None, "LOGIN", LoginPassword(user.encode("utf-8"), password)
    )
    assert result.success is False
    assert result.handled is False


def test_unknown_user_still_hashes(
    authenticate: Authenticate,
    server: ModuleType,
    known_user: tuple[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
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
    result = authenticate("LOGIN", LoginPassword(b"nonexistent", known_user[1]))

    assert result.success is False
    assert calls, "unknown user returned without hashing; timing reveals it exists"


def test_corrupt_salt_rejects_without_raising(
    server: ModuleType, known_user: tuple[str, bytes], tmp_path: Path
) -> None:
    """A row whose salt is not hex is a data error, not a credential error."""
    user, password = known_user
    corrupt = tmp_path / "corrupt.db"
    with closing(sqlite3.connect(corrupt)) as conn:
        conn.execute("CREATE TABLE userauth (username text, salt text, hashpass text)")
        conn.execute(
            "INSERT INTO userauth VALUES (?, ?, ?)", (user, "not-hex!", "deadbeef")
        )
        conn.commit()

    result = server.Authenticator(corrupt)(
        None, None, None, "LOGIN", LoginPassword(user.encode("utf-8"), password)
    )
    assert result.success is False
    assert result.handled is False


def test_undecodable_login_cannot_match_a_replacement_char_user(
    server: ModuleType, tmp_path: Path
) -> None:
    """Decoding with errors="replace" would let one login match another user.

    Every invalid byte becomes U+FFFD, so a login of b"\\xff" would match a
    stored user named U+FFFD. Strict decoding rejects it instead.
    """
    salt = bytes(32)
    db = tmp_path / "replacement.db"
    with closing(sqlite3.connect(db)) as conn:
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

    result = server.Authenticator(db)(
        None, None, None, "LOGIN", LoginPassword(b"\xff", b"secret")
    )
    assert result.success is False
    assert result.handled is False
