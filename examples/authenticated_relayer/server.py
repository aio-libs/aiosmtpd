# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import hmac
import logging
import sqlite3
import sys
from contextlib import closing
from functools import lru_cache
from hashlib import pbkdf2_hmac
from pathlib import Path
from smtplib import SMTP as SMTPCLient
from typing import Dict

import dns.resolver
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword


DEST_PORT = 25
DB_AUTH = Path("mail.db~")

log = logging.getLogger("mail.log")

# Must match make_user_db.py, or nothing will ever verify.
HASH_ITERATIONS = 1000000

# Hashed against when the username is unknown, so that case costs the same as a
# known username with the wrong password. Without it the early return is fast
# enough to enumerate valid usernames by timing the rejection.
DUMMY_SALT = bytes(32)


class Authenticator:
    def __init__(self, auth_database):
        self.auth_db = Path(auth_database)

    def __call__(self, server, session, envelope, mechanism, auth_data):
        fail_nothandled = AuthResult(success=False, handled=False)
        if mechanism not in ("LOGIN", "PLAIN"):
            return fail_nothandled
        if not isinstance(auth_data, LoginPassword):
            return fail_nothandled
        # auth_data fields are bytes; the username is stored as text in the DB.
        # Reject a login that is not valid UTF-8 rather than mangling it, which
        # would fold distinct logins onto one name.
        try:
            username = auth_data.login.decode("utf-8")
        except UnicodeDecodeError:
            return fail_nothandled

        try:
            with closing(sqlite3.connect(self.auth_db)) as conn:
                row = conn.execute(
                    "SELECT salt, hashpass FROM userauth WHERE username=?",
                    (username,),
                ).fetchone()
        except sqlite3.Error:
            # A configuration problem, not a bad password. Say so in the log;
            # the client still learns nothing beyond "rejected".
            log.exception(
                "cannot read %s. If it predates the salt column, recreate it "
                "with make_user_db.py",
                self.auth_db,
            )
            return fail_nothandled

        salt, hash_db = row if row else (DUMMY_SALT.hex(), "")
        try:
            expected = bytes.fromhex(hash_db)
            digest = pbkdf2_hmac(
                "sha256", auth_data.password, bytes.fromhex(salt), HASH_ITERATIONS
            )
        except (ValueError, TypeError):
            log.error(
                "salt or hashpass stored for %r is not valid hex; recreate %s "
                "with make_user_db.py",
                username,
                self.auth_db,
            )
            return fail_nothandled

        # Compared after hashing either way, so the unknown-user path costs the
        # same as a wrong password.
        if row is None or not hmac.compare_digest(digest, expected):
            return fail_nothandled
        return AuthResult(success=True)


@lru_cache(maxsize=256)
def get_mx(domain):
    records = dns.resolver.resolve(domain, "MX")
    if not records:
        return None
    result = max(records, key=lambda r: r.preference)
    return str(result.exchange)


class RelayHandler:
    def handle_data(self, server, session, envelope, data):
        mx_rcpt: Dict[str, list[str]] = {}
        for rcpt in envelope.rcpt_tos:
            _, _, domain = rcpt.partition("@")
            mx = get_mx(domain)
            if mx is None:
                continue
            mx_rcpt.setdefault(mx, []).append(rcpt)

        for mx, rcpts in mx_rcpt.items():
            with SMTPCLient(mx, 25) as client:
                client.sendmail(
                    from_addr=envelope.mail_from,
                    to_addrs=rcpts,
                    msg=envelope.original_content
                )


# noinspection PyShadowingNames
async def amain():
    handler = RelayHandler()
    cont = Controller(
        handler,
        hostname='',
        port=8025,
        authenticator=Authenticator(DB_AUTH)
    )
    try:
        cont.start()
    finally:
        cont.stop()


if __name__ == '__main__':
    if not DB_AUTH.exists():
        print(f"Please create {DB_AUTH} first using make_user_db.py")
        sys.exit(1)
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(amain())  # type: ignore[unused-awaitable]
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("User abort indicated")
