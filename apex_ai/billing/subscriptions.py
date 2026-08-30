"""Per-account subscription state (Phase 81): which plan an account is on.

Every account is implicitly on the free plan until a row exists here - the
same "no row = the safe default" precedent conversations/collections use
for pre-existing data, applied here to something that simply never had a
row yet rather than data predating a migration. No real payment provider
is connected (Phase 85 is deliberately declined this pass - see
docs/PHASE85_BILLING_INTEGRATION_DECISION.md); ``set_plan()`` is the one
mutating operation, callable by an administrator today and by a real
webhook handler once Phase 85 exists.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apex_ai.billing.plans import DEFAULT_PLAN_ID, PLANS, get_plan
from apex_ai.core.errors import DatabaseError

_STATUSES = {"active", "canceled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Subscription:
    user_id: str
    plan_id: str
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "plan": get_plan(self.plan_id).to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SubscriptionStore:
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
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _error(self, action: str, error: Exception) -> DatabaseError:
        return DatabaseError(
            what=f"Could not {action} the subscriptions database at {self.path}.",
            why=str(error),
            fix="Check disk space and permissions.",
        )

    def get(self, user_id: str) -> Subscription:
        """Never ``None``: an account with no row is on the free plan."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM subscriptions WHERE user_id=?", (user_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        if row is None:
            now = _now()
            return Subscription(user_id, DEFAULT_PLAN_ID, "active", now, now)
        return self._subscription(row)

    def set_plan(self, user_id: str, plan_id: str, status: str = "active") -> Subscription:
        if plan_id not in PLANS:
            raise ValueError(f"Unknown plan '{plan_id}'.")
        if status not in _STATUSES:
            raise ValueError(f"Unknown subscription status '{status}'.")
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO subscriptions(user_id,plan_id,status,created_at,updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(user_id) DO UPDATE SET
                           plan_id=excluded.plan_id,
                           status=excluded.status,
                           updated_at=excluded.updated_at""",
                    (user_id, plan_id, status, now, now),
                )
        except sqlite3.Error as error:
            raise self._error("write to", error) from error
        return self.get(user_id)

    def cancel(self, user_id: str) -> Subscription:
        """Immediately reverts to the free plan. There is no real billing
        engine here tracking a paid period's end date (Phase 85 declined
        this pass), so this deliberately does not pretend to keep paid
        access active until one - see the phase doc."""
        return self.set_plan(user_id, DEFAULT_PLAN_ID, "active")

    @staticmethod
    def _subscription(row: sqlite3.Row) -> Subscription:
        return Subscription(
            user_id=row["user_id"],
            plan_id=row["plan_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


__all__ = ["Subscription", "SubscriptionStore"]
