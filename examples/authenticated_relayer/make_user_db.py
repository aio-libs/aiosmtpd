# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import secrets
import sqlite3
from hashlib import pbkdf2_hmac
from pathlib import Path


DB_FILE = "mail.db~"
USER_AND_PASSWORD = {
    "user1": b"not@password",
    "user2": b"correctbatteryhorsestaple",
    "user3": b"1d0ntkn0w",
    "user4": b"password",
    "user5": b"password123",
    "user6": b"a quick brown fox jumps over a lazy dog"
}


if __name__ == '__main__':
    dbfp = Path(DB_FILE).absolute()
    if dbfp.exists():
        dbfp.unlink()
    conn = sqlite3.connect(DB_FILE)
    curs = conn.cursor()
    curs.execute("CREATE TABLE userauth (username text, hashpass text)")
    insert_up = "INSERT INTO userauth VALUES (?, ?)"
    for u, p in USER_AND_PASSWORD.items():
        h = pbkdf2_hmac("sha256", p, secrets.token_bytes(), 1000000).hex()
        curs.execute(insert_up, (u, h))
    conn.commit()
    conn.close()
    assert dbfp.exists()
    print(f"database created at {dbfp}")
