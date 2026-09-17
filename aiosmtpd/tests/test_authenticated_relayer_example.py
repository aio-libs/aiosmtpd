# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from examples.authenticated_relayer import server as relayer
from examples.authenticated_relayer.make_user_db import make_user_db

from aiosmtpd.smtp import LoginPassword


def test_authenticator_uses_stored_salt(tmp_path: Path) -> None:
    auth_db = tmp_path / "mail.db"
    make_user_db(auth_db, {"alice": b"correct password"})
    authenticator = relayer.Authenticator(auth_db)

    accepted = authenticator(
        None, None, None, "PLAIN", LoginPassword(b"alice", b"correct password")
    )
    wrong_password = authenticator(
        None, None, None, "PLAIN", LoginPassword(b"alice", b"wrong password")
    )
    unknown_user = authenticator(
        None, None, None, "PLAIN", LoginPassword(b"bob", b"correct password")
    )
    invalid_username = authenticator(
        None, None, None, "PLAIN", LoginPassword(b"\xff", b"correct password")
    )

    assert accepted.success
    assert not wrong_password.success
    assert not unknown_user.success
    assert not invalid_username.success


def test_authenticator_rejects_legacy_database(tmp_path: Path) -> None:
    auth_db = tmp_path / "mail.db"
    with closing(sqlite3.connect(auth_db)) as conn:
        conn.execute("CREATE TABLE userauth (username text, hashpass text)")
        conn.commit()

    with pytest.raises(RuntimeError, match="recreate it with make_user_db.py"):
        relayer.Authenticator(auth_db)


def test_main_task_keeps_controller_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_db = tmp_path / "mail.db"
    make_user_db(auth_db, {"alice": b"correct password"})
    monkeypatch.setattr(relayer, "DB_AUTH", auth_db)
    started = False
    stopped = False

    class DummyController:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            nonlocal started
            started = True

        def stop(self) -> None:
            nonlocal stopped
            stopped = True

    monkeypatch.setattr(relayer, "Controller", DummyController)

    async def exercise() -> None:
        task = asyncio.create_task(relayer.amain())
        await asyncio.sleep(0)
        assert started
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert stopped


def test_main_task_cleans_up_controller_when_start_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_db = tmp_path / "mail.db"
    make_user_db(auth_db, {"alice": b"correct password"})
    monkeypatch.setattr(relayer, "DB_AUTH", auth_db)
    stopped = False

    class DummyController:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("partial startup failure")

        def stop(self) -> None:
            nonlocal stopped
            stopped = True

    monkeypatch.setattr(relayer, "Controller", DummyController)

    with pytest.raises(RuntimeError, match="partial startup failure"):
        asyncio.run(relayer.amain())

    assert stopped
