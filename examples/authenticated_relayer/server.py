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

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword


DEST_PORT = 25
DB_AUTH = Path("mail.db~")


class Authenticator:
    def __init__(self, auth_database):
        self.auth_db = Path(auth_database)
        with closing(sqlite3.connect(self.auth_db)) as conn:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(userauth)")
            }
        if not {"username", "salt", "hashpass"} <= columns:
            raise RuntimeError(
                "incompatible authentication database; recreate it with "
                "make_user_db.py"
            )

    def __call__(self, server, session, envelope, mechanism, auth_data):
        fail_nothandled = AuthResult(success=False, handled=False)
        if mechanism not in ("LOGIN", "PLAIN"):
            return fail_nothandled
        if not isinstance(auth_data, LoginPassword):
            return fail_nothandled
        try:
            username = auth_data.login.decode("utf-8")
        except UnicodeDecodeError:
            return fail_nothandled
        password = auth_data.password
        with closing(sqlite3.connect(self.auth_db)) as conn:
            credentials = conn.execute(
                "SELECT salt, hashpass FROM userauth WHERE username=?", (username,)
            ).fetchone()
        if not credentials:
            return fail_nothandled
        salt, hash_db = credentials
        hashpass = pbkdf2_hmac("sha256", password, salt, 1000000).hex()
        if not hmac.compare_digest(hashpass, hash_db):
            return fail_nothandled
        return AuthResult(success=True)


@lru_cache(maxsize=256)
def get_mx(domain):
    import dns.resolver

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
        await asyncio.Event().wait()
    finally:
        cont.stop()


if __name__ == '__main__':
    if not DB_AUTH.exists():
        print(f"Please create {DB_AUTH} first using make_user_db.py")
        sys.exit(1)
    logging.basicConfig(level=logging.DEBUG)
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("User abort indicated")
