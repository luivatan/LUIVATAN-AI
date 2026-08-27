"""Small, dependency-free account service for the Apex AI foundation.

The service is intentionally framework-neutral so it can be mounted behind
Gradio, FastAPI, or a desktop shell in a later phase. Tokens are returned to
the caller for delivery; this module never sends email or logs secrets.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

PBKDF2_ROUNDS = 310_000
TOKEN_TTL = 60 * 60
RESET_TTL = 60 * 30


class AccountError(ValueError):
    pass


class AuthService:
    def __init__(self, database: str | Path = "accounts.sqlite3"):
        self.database = str(database)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL, display_name TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0, role TEXT NOT NULL DEFAULT 'user',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tokens (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                kind TEXT NOT NULL, expires_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS token_expiry ON tokens(expires_at);
            """)

    @staticmethod
    def _hash_password(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
        return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"

    @staticmethod
    def _check_password(password: str, encoded: str) -> bool:
        scheme, rounds, salt, expected = encoded.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return scheme == "pbkdf2_sha256" and hmac.compare_digest(actual, expected)

    @staticmethod
    def _token() -> tuple[str, str]:
        raw = secrets.token_urlsafe(32)
        return raw, hashlib.sha256(raw.encode()).hexdigest()

    def register(self, email: str, password: str, display_name: str) -> str:
        email = email.strip().lower()
        if "@" not in email or len(email) > 254:
            raise AccountError("Enter a valid email address.")
        if len(password) < 12:
            raise AccountError("Password must be at least 12 characters.")
        if not display_name.strip() or len(display_name.strip()) > 80:
            raise AccountError("Enter a display name between 1 and 80 characters.")
        try:
            with self._connect() as db:
                user = db.execute("INSERT INTO users(email,password_hash,display_name,created_at) VALUES(?,?,?,?) RETURNING id", (email, self._hash_password(password), display_name.strip(), int(time.time()))).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AccountError("Unable to create account with those details.") from exc
        return self._issue(int(user["id"]), "verify", TOKEN_TTL)

    def login(self, email: str, password: str) -> str:
        with self._connect() as db:
            user = db.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        if not user or not self._check_password(password, user["password_hash"]):
            raise AccountError("Invalid email or password.")
        return self._issue(user["id"], "session", TOKEN_TTL)

    def _issue(self, user_id: int, kind: str, ttl: int) -> str:
        raw, digest = self._token()
        with self._connect() as db:
            db.execute("INSERT INTO tokens VALUES (?,?,?,?)", (digest, user_id, kind, int(time.time()) + ttl))
        return raw

    def _consume(self, raw: str, kind: str) -> sqlite3.Row:
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self._connect() as db:
            row = db.execute("SELECT u.*, t.token_hash, t.expires_at FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.kind=?", (digest, kind)).fetchone()
            if not row or row["expires_at"] < time.time():
                raise AccountError("This link or session is invalid or expired.")
            db.execute("DELETE FROM tokens WHERE token_hash=?", (digest,))
        return row

    def verify_email(self, token: str) -> None:
        user = self._consume(token, "verify")
        with self._connect() as db:
            db.execute("UPDATE users SET verified=1 WHERE id=?", (user["id"],))

    def request_password_reset(self, email: str) -> Optional[str]:
        with self._connect() as db:
            user = db.execute("SELECT id FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        # Return no signal about account existence to callers in production.
        return self._issue(user["id"], "reset", RESET_TTL) if user else None

    def reset_password(self, token: str, new_password: str) -> None:
        if len(new_password) < 12:
            raise AccountError("Password must be at least 12 characters.")
        user = self._consume(token, "reset")
        with self._connect() as db:
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (self._hash_password(new_password), user["id"]))
            db.execute("DELETE FROM tokens WHERE user_id=? AND kind='session'", (user["id"],))

    def logout(self, session: str) -> None:
        digest = hashlib.sha256(session.encode()).hexdigest()
        with self._connect() as db:
            db.execute("DELETE FROM tokens WHERE token_hash=? AND kind='session'", (digest,))

    def current_user(self, session: str) -> dict:
        row = self._consume(session, "session")
        # Rotate sessions on each authenticated request: callers must store the new token.
        fresh = self._issue(row["id"], "session", TOKEN_TTL)
        return {"id": row["id"], "email": row["email"], "display_name": row["display_name"], "verified": bool(row["verified"]), "role": row["role"], "session": fresh}

    def set_role(self, session: str, role: str) -> None:
        user = self.current_user(session)
        if user["role"] != "admin":
            raise AccountError("Administrator permission required.")
        if role not in {"user", "admin"}:
            raise AccountError("Unknown role.")
        # Role changes are intentionally not exposed as a user-facing operation.
