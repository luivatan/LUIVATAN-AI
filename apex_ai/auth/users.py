"""Phase 51 — user accounts.

A real, persistent identity per account: SQLite-backed, one process-local
database (``data/users.db``), following the same one-store-per-concern
pattern as ``conversations.db`` and ``long_term_memory.db``.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apex_ai.auth.passwords import hash_password, verify_password
from apex_ai.core.errors import DatabaseError

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# A real, validly-formatted hash (computed once) to run verify_password against
# when the looked-up email doesn't exist, so that path takes about as long as a
# genuine wrong-password check instead of returning near-instantly - a login
# response shouldn't let a timing difference reveal whether an email is registered.
_DUMMY_HASH = hash_password("not-a-real-password-used-only-for-constant-time-lookup")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_email(email: str) -> str:
    clean = str(email or "").strip().lower()
    if not clean or len(clean) > 254 or not _EMAIL.match(clean):
        raise ValueError("Enter a valid email address.")
    return clean


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str
    display_name: str
    created_at: str
    updated_at: str
    is_default_local: bool

    def to_dict(self) -> dict[str, Any]:
        """Never includes ``password_hash`` — this is the shape returned to
        clients; nothing in the API layer should reach for the raw row."""
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "created_at": self.created_at,
            "is_default_local": self.is_default_local,
        }


class EmailAlreadyRegisteredError(ValueError):
    pass


class UserStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=20, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        display_name TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        is_default_local INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
                    """
                )
        except sqlite3.Error as error:
            raise self._error("initialize", error) from error

    def create(
        self,
        email: str,
        password: str,
        *,
        display_name: str = "",
        is_default_local: bool = False,
    ) -> User:
        clean_email = normalize_email(email)
        password_hash = hash_password(password)  # raises ValueError on a weak password
        user_id = str(uuid.uuid4())
        now = _now()
        try:
            with self._connect() as connection:
                try:
                    connection.execute(
                        """INSERT INTO users
                           (id,email,password_hash,display_name,created_at,updated_at,is_default_local)
                           VALUES (?,?,?,?,?,?,?)""",
                        (
                            user_id,
                            clean_email,
                            password_hash,
                            display_name.strip()[:100],
                            now,
                            now,
                            int(is_default_local),
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise EmailAlreadyRegisteredError(
                        "An account with that email already exists."
                    ) from error
        except sqlite3.Error as error:
            raise self._error("create", error) from error
        return User(user_id, clean_email, password_hash, display_name.strip()[:100], now, now, is_default_local)

    def get(self, user_id: str) -> User | None:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return self._record(row) if row else None

    def get_by_email(self, email: str) -> User | None:
        try:
            clean_email = normalize_email(email)
        except ValueError:
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM users WHERE email=?", (clean_email,)
                ).fetchone()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return self._record(row) if row else None

    def get_default_local(self) -> User | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM users WHERE is_default_local=1 ORDER BY created_at LIMIT 1"
                ).fetchone()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return self._record(row) if row else None

    def count(self) -> int:
        try:
            with self._connect() as connection:
                return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        except sqlite3.Error as error:
            raise self._error("count", error) from error

    def verify_credentials(self, email: str, password: str) -> User | None:
        """Constant-shape failure: a wrong password and a nonexistent email
        both simply return ``None`` — callers must not distinguish them in
        anything user-visible (don't leak which part of a login was wrong)."""
        user = self.get_by_email(email)
        if user is None:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    @staticmethod
    def _record(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            display_name=row["display_name"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_default_local=bool(row["is_default_local"]),
        )

    def _error(self, operation: str, error: Exception) -> DatabaseError:
        return DatabaseError(
            what=f"The accounts database could not {operation} data.",
            why=f"SQLite reported {type(error).__name__} during the operation.",
            fix=f"Check permissions and disk space for {self.path}.",
        )


__all__ = ["EmailAlreadyRegisteredError", "User", "UserStore", "normalize_email"]
