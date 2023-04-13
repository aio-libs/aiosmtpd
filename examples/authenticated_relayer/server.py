# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import dns.resolver
import logging
import sqlite3
import sys

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword
from argon2 import PasswordHasher
from functools import lru_cache
from pathlib import Path
from smtplib import SMTP as SMTPCLient


DEST_PORT = 25
DB_AUTH = Path("mail.db~")


class Authenticator:
    def __init__(self, auth_database):
        self.auth_db = Path(auth_database)
        self.ph = PasswordHasher()

    def __call__(self, server, session, envelope, mechanism, auth_data):
        fail_nothandled = AuthResult(success=False, handled=False)
        if mechanism not in ("LOGIN", "PLAIN"):
            return fail_nothandled
        if not isinstance(auth_data, LoginPassword):
            return fail_nothandled
        username = auth_data.login
        password = auth_data.password
        hashpass = self.ph.hash(password)
        conn = sqlite3.connect(self.auth_db)
        curs = conn.execute(
            "SELECT hashpass FROM userauth WHERE username=?", (username,)
        )
        hash_db = curs.fetchone()
        conn.close()
        if not hash_db:
            return fail_nothandled
        if hashpass != hash_db[0]:
            return fail_nothandled
        return AuthResult(success=True)


@lru_cache(maxsize=256)
def get_mx(domain):
    records = dns.resolver.resolve(domain, "MX")
    if not records:
        return None
    records = sorted(records, key=lambda r: r.preference)
    return str(records[0].exchange)


class RelayHandler:
    def handle_data(self, server, session, envelope, data):
        mx_rcpt = {}
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
        authenticator=Authenticator(DB_AUTH),
        auth_required=True,
    )
    cont.start()


if __name__ == '__main__':
    if not DB_AUTH.exists():
        print(f"Please create {DB_AUTH} first using make_user_db.py")
        sys.exit(1)
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(loop.create_task(amain()))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("User abort indicated")
