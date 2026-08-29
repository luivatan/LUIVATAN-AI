"""Phase 52 — server-side sessions.

An opaque random token (``secrets.token_urlsafe``, stdlib — no JWT library, no
key management, no signing) maps to a row in SQLite. Deliberately simpler than
a signed/stateless token scheme: revoking a session (logout, or an operator
clearing ``users.db``) is one DELETE, not a blocklist to maintain, and nothing
about a session's validity depends on keeping a secret key safe over time.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apex_ai.core.errors import DatabaseError

_TOKEN_BYTES = 32  # 256 bits of entropy


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Session:
    token: str
    user_id: str
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


class SessionStore:
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
                    CREATE TABLE IF NOT EXISTS sessions (
                        token TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                    CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
                    """
                )
        except sqlite3.Error as error:
            raise self._error("initialize", error) from error

    def create(self, user_id: str, *, ttl_days: int) -> Session:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = _now()
        expires_at = now + timedelta(days=max(1, int(ttl_days)))
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES (?,?,?,?)",
                    (token, user_id, _timestamp(now), _timestamp(expires_at)),
                )
        except sqlite3.Error as error:
            raise self._error("create", error) from error
        return Session(token, user_id, _timestamp(now), _timestamp(expires_at))

    def get(self, token: str) -> Session | None:
        """Returns ``None`` for a missing *or* expired session; an expired row
        is deleted as a side effect instead of accumulating forever."""
        if not token:
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM sessions WHERE token=?", (token,)
                ).fetchone()
                if row is None:
                    return None
                session = self._record(row)
                if session.expires_at <= _timestamp(_now()):
                    connection.execute("DELETE FROM sessions WHERE token=?", (token,))
                    return None
                return session
        except sqlite3.Error as error:
            raise self._error("read", error) from error

    def delete(self, token: str) -> bool:
        try:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM sessions WHERE token=?", (token,))
                return cursor.rowcount > 0
        except sqlite3.Error as error:
            raise self._error("delete", error) from error

    def delete_all_for_user(self, user_id: str) -> int:
        try:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                return cursor.rowcount
        except sqlite3.Error as error:
            raise self._error("delete", error) from error

    def delete_expired(self) -> int:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM sessions WHERE expires_at<=?", (_timestamp(_now()),)
                )
                return max(0, cursor.rowcount)
        except sqlite3.Error as error:
            raise self._error("expire", error) from error

    @staticmethod
    def _record(row: sqlite3.Row) -> Session:
        return Session(
            token=row["token"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def _error(self, operation: str, error: Exception) -> DatabaseError:
        return DatabaseError(
            what=f"The sessions database could not {operation} data.",
            why=f"SQLite reported {type(error).__name__} during the operation.",
            fix=f"Check permissions and disk space for {self.path}.",
        )


__all__ = ["Session", "SessionStore"]
