"""Usage tracking (Phase 88): an append-only ledger of billable/limited
resource consumption - what ``entitlements.py``'s rate limits (Phase 87)
check against.

Deliberately an append-only ledger rather than a running counter column: a
ledger survives a crash mid-write without losing or double-counting an
event (one INSERT either committed or it didn't - there's no separate
"increment this counter" step that could be applied twice or missed), and
it directly supports the "since the start of this calendar month" query
rate limits need without any separate reset/rollover job to get wrong.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from apex_ai.core.errors import DatabaseError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def month_start(reference: datetime | None = None) -> str:
    """The ISO timestamp for 00:00:00 UTC on the 1st of the current (or
    ``reference``'s) month - the fixed period every rate limit resets on."""
    now = reference or datetime.now(timezone.utc)
    return (
        now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class UsageEvent:
    id: str
    user_id: str
    resource: str
    amount: int
    occurred_at: str


class UsageStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.Error as error:
            raise self._error("open", error) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=20, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_usage_user_resource_time
                    ON usage_events(user_id, resource, occurred_at);
                """
            )

    def _error(self, action: str, error: Exception) -> DatabaseError:
        return DatabaseError(
            what=f"Could not {action} the usage database at {self.path}.",
            why=str(error),
            fix="Check disk space and permissions.",
        )

    def record(self, user_id: str, resource: str, amount: int = 1) -> UsageEvent:
        if amount <= 0:
            raise ValueError("amount must be positive.")
        event_id = str(uuid.uuid4())
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO usage_events(id,user_id,resource,amount,occurred_at) "
                    "VALUES (?,?,?,?,?)",
                    (event_id, user_id, resource, amount, now),
                )
        except sqlite3.Error as error:
            raise self._error("write to", error) from error
        return UsageEvent(event_id, user_id, resource, amount, now)

    def total_since(self, user_id: str, resource: str, since: str) -> int:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM usage_events "
                    "WHERE user_id=? AND resource=? AND occurred_at>=?",
                    (user_id, resource, since),
                ).fetchone()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return int(row[0])

    def total_this_month(self, user_id: str, resource: str) -> int:
        return self.total_since(user_id, resource, month_start())


__all__ = ["UsageEvent", "UsageStore", "month_start"]
