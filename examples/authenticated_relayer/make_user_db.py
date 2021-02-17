# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import sqlite3
from argon2 import PasswordHasher
from pathlib import Path
from typing import Dict


DB_FILE = "mail.db"
USER_AND_PASSWORD: Dict[str, str] = {
    "user1": "not@password",
    "user2": "correctbatteryhorsestaple",
    "user3": "1d0ntkn0w",
    "user4": "password",
    "user5": "password123",
    "user6": "a quick brown fox jumps over a lazy dog"
}


if __name__ == '__main__':
    dbfp = Path(DB_FILE).absolute()
    if dbfp.exists():
        dbfp.unlink()
    conn = sqlite3.connect(DB_FILE)
    curs = conn.cursor()
    curs.execute("CREATE TABLE userauth (username text, hashpass text)")
    ph = PasswordHasher()
    insert_up = "INSERT INTO userauth VALUES (?, ?)"
    for u, p in USER_AND_PASSWORD.items():
        h = ph.hash(p)
        curs.execute(insert_up, (u, h))
    conn.commit()
    conn.close()
    assert dbfp.exists()
    print(f"database created at {dbfp}")
