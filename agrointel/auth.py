"""Sign-up / log-in with a local SQLite user store. Standard library only.

Passwords are never stored: only a per-user random salt and a PBKDF2-HMAC-SHA256 hash.
Repeated failed log-ins lock the account briefly. The store sits behind `UserStore`, so it can be
swapped for Supabase/Firebase/Postgres later without touching the UI.

IMPORTANT: on Streamlit Community Cloud the disk is wiped when the app restarts, so accounts created
there will not survive. For a public deployment point UserStore at a hosted database (see README).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

PBKDF2_ITERATIONS = 600_000
MAX_FAILS = 5
LOCK_SECONDS = 300
MIN_PASSWORD = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    """Message is safe to show to the user."""


@dataclass
class User:
    id: int
    email: str
    name: str


def hash_password(password: str, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    return salt, hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def validate_signup(name: str, email: str, password: str, confirm: str) -> str | None:
    if len(name.strip()) < 2:
        return "Please enter your name."
    if not EMAIL_RE.match(email.strip()):
        return "Please enter a valid email address."
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if password.lower() == password or not any(c.isdigit() for c in password):
        return "Password needs at least one capital letter and one number."
    if password != confirm:
        return "Passwords do not match."
    return None


class UserStore:
    def __init__(self, path: str | os.PathLike | None = None, iterations: int = PBKDF2_ITERATIONS):
        self.path = Path(path or config.USER_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.iterations = iterations
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                salt BLOB NOT NULL, pw_hash BLOB NOT NULL, iterations INTEGER NOT NULL,
                created_utc TEXT NOT NULL, last_login_utc TEXT, fails INTEGER DEFAULT 0, locked_until REAL DEFAULT 0)""")

    def _db(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def signup(self, name: str, email: str, password: str, confirm: str) -> User:
        err = validate_signup(name, email, password, confirm)
        if err:
            raise AuthError(err)
        email = email.strip().lower()
        salt, h = hash_password(password, iterations=self.iterations)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            with self._db() as db:
                cur = db.execute("INSERT INTO users(email,name,salt,pw_hash,iterations,created_utc,last_login_utc)"
                                 " VALUES(?,?,?,?,?,?,?)", (email, name.strip(), salt, h, self.iterations, now, now))
                return User(cur.lastrowid, email, name.strip())
        except sqlite3.IntegrityError:
            raise AuthError("An account with this email already exists. Try logging in.") from None

    def login(self, email: str, password: str) -> User:
        email = email.strip().lower()
        generic = AuthError("Incorrect email or password.")
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if row is None:
                hash_password(password, b"0" * 16, self.iterations)      # keep timing similar
                raise generic
            if row["locked_until"] and row["locked_until"] > time.time():
                mins = int((row["locked_until"] - time.time()) // 60) + 1
                raise AuthError(f"Too many failed attempts. Try again in about {mins} minute(s).")
            _, h = hash_password(password, row["salt"], row["iterations"])
            if not hmac.compare_digest(h, row["pw_hash"]):
                fails = (row["fails"] or 0) + 1
                lock = time.time() + LOCK_SECONDS if fails >= MAX_FAILS else 0
                db.execute("UPDATE users SET fails=?, locked_until=? WHERE id=?",
                           (0 if lock else fails, lock, row["id"]))
                db.commit()                      # raising inside `with` would roll the update back
                raise generic
            db.execute("UPDATE users SET fails=0, locked_until=0, last_login_utc=? WHERE id=?",
                       (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), row["id"]))
            return User(row["id"], row["email"], row["name"])
