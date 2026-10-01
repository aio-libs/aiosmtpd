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


def make_user_db(db_file=DB_FILE, users=USER_AND_PASSWORD):
    dbfp = Path(db_file).absolute()
    if dbfp.exists():
        dbfp.unlink()
    conn = sqlite3.connect(dbfp)
    curs = conn.cursor()
    curs.execute("CREATE TABLE userauth (username text, salt blob, hashpass text)")
    insert_up = "INSERT INTO userauth VALUES (?, ?, ?)"
    for username, password in users.items():
        salt = secrets.token_bytes()
        hashpass = pbkdf2_hmac("sha256", password, salt, 1000000).hex()
        curs.execute(insert_up, (username, salt, hashpass))
    conn.commit()
    conn.close()
    assert dbfp.exists()
    print(f"database created at {dbfp}")


if __name__ == '__main__':
    make_user_db()
