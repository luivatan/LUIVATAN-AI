"""Separate persistence for explicit long-term preferences and ongoing context.

Phase 42 intentionally provides storage only. It does not extract memories from
chat, write automatically, inject records into prompts, or expose management UI;
those behaviors belong to later roadmap phases and require separate safety and
confirmation work.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apex_ai.core.errors import DatabaseError
from apex_ai.security.memory import MemorySafetyPolicy

ALLOWED_MEMORY_KINDS = frozenset({"preference", "ongoing_context"})


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _validate_kind(kind: str) -> str:
    clean = str(kind or "").strip().lower()
    if clean not in ALLOWED_MEMORY_KINDS:
        allowed = ", ".join(sorted(ALLOWED_MEMORY_KINDS))
        raise ValueError(f"Memory kind must be one of: {allowed}.")
    return clean


def _validate_content(content: str) -> str:
    clean = str(content or "").strip()
    if not clean:
        raise ValueError("Memory content cannot be empty.")
    return clean


@dataclass(frozen=True)
class LongTermMemory:
    id: str
    kind: str
    content: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class LongTermMemoryStore:
    """SQLite CRUD foundation, intentionally disconnected from model prompts."""

    def __init__(
        self,
        path: str | Path,
        *,
        safety_policy: MemorySafetyPolicy | None = None,
    ) -> None:
        self.path = Path(path)
        self.safety_policy = safety_policy or MemorySafetyPolicy()
        self.removed_unsafe_on_startup = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
            self.removed_unsafe_on_startup = self._remove_unsafe_existing()
        except DatabaseError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise self._error("initialize", error) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path), timeout=20, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS long_term_memories (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL
                            CHECK(kind IN ('preference','ongoing_context')),
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_long_term_memories_updated
                        ON long_term_memories(updated_at DESC, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_long_term_memories_kind
                        ON long_term_memories(kind, updated_at DESC, id DESC);
                    """
                )
        except sqlite3.Error as error:
            raise self._error("initialize", error) from error

    def _remove_unsafe_existing(self) -> int:
        """Delete recognized unsafe legacy rows without exposing their content."""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id,content FROM long_term_memories"
                ).fetchall()
                unsafe_ids = [
                    (row["id"],)
                    for row in rows
                    if not self.safety_policy.inspect(row["content"]).safe
                ]
                if unsafe_ids:
                    connection.executemany(
                        "DELETE FROM long_term_memories WHERE id=?",
                        unsafe_ids,
                    )
                return len(unsafe_ids)
        except sqlite3.Error as error:
            raise self._error("apply safety checks to", error) from error

    def create(self, content: str, *, kind: str) -> LongTermMemory:
        """Persist one explicit memory; callers must choose its declared kind."""
        clean_kind = _validate_kind(kind)
        clean_content = _validate_content(content)
        self.safety_policy.require_safe(clean_content)
        memory_id = str(uuid.uuid4())
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO long_term_memories
                       (id,kind,content,created_at,updated_at)
                       VALUES (?,?,?,?,?)""",
                    (memory_id, clean_kind, clean_content, now, now),
                )
        except sqlite3.Error as error:
            raise self._error("create", error) from error
        return LongTermMemory(memory_id, clean_kind, clean_content, now, now)

    def get(self, memory_id: str) -> LongTermMemory | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM long_term_memories WHERE id=?",
                    (memory_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return self._record(row) if row else None

    def list(
        self,
        *,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[LongTermMemory]:
        clean_kind = _validate_kind(kind) if kind is not None else None
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM long_term_memories"
        params: list[Any] = []
        if clean_kind is not None:
            sql += " WHERE kind=?"
            params.append(clean_kind)
        sql += " ORDER BY updated_at DESC,id DESC LIMIT ?"
        params.append(limit)
        try:
            with self._connect() as connection:
                rows = connection.execute(sql, params).fetchall()
        except sqlite3.Error as error:
            raise self._error("list", error) from error
        return [self._record(row) for row in rows]

    def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        kind: str | None = None,
    ) -> LongTermMemory:
        """Update explicit fields without changing the stable memory ID."""
        existing = self.get(memory_id)
        if existing is None:
            raise KeyError(memory_id)
        clean_content = (
            existing.content if content is None else _validate_content(content)
        )
        self.safety_policy.require_safe(clean_content)
        clean_kind = existing.kind if kind is None else _validate_kind(kind)
        updated_at = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """UPDATE long_term_memories
                       SET kind=?,content=?,updated_at=? WHERE id=?""",
                    (clean_kind, clean_content, updated_at, memory_id),
                )
        except sqlite3.Error as error:
            raise self._error("update", error) from error
        return LongTermMemory(
            memory_id,
            clean_kind,
            clean_content,
            existing.created_at,
            updated_at,
        )

    def delete(self, memory_id: str) -> bool:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM long_term_memories WHERE id=?",
                    (memory_id,),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as error:
            raise self._error("delete", error) from error

    def clear(self, *, kind: str | None = None) -> int:
        clean_kind = _validate_kind(kind) if kind is not None else None
        try:
            with self._connect() as connection:
                if clean_kind is None:
                    count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM long_term_memories"
                        ).fetchone()[0]
                    )
                    connection.execute("DELETE FROM long_term_memories")
                else:
                    count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM long_term_memories WHERE kind=?",
                            (clean_kind,),
                        ).fetchone()[0]
                    )
                    connection.execute(
                        "DELETE FROM long_term_memories WHERE kind=?",
                        (clean_kind,),
                    )
                return count
        except sqlite3.Error as error:
            raise self._error("clear", error) from error

    def count(self, *, kind: str | None = None) -> int:
        clean_kind = _validate_kind(kind) if kind is not None else None
        try:
            with self._connect() as connection:
                if clean_kind is None:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM long_term_memories"
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM long_term_memories WHERE kind=?",
                        (clean_kind,),
                    ).fetchone()
                return int(row[0])
        except sqlite3.Error as error:
            raise self._error("count", error) from error

    @staticmethod
    def _record(row: sqlite3.Row) -> LongTermMemory:
        return LongTermMemory(
            id=row["id"],
            kind=row["kind"],
            content=row["content"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _error(self, operation: str, error: Exception) -> DatabaseError:
        return DatabaseError(
            what=f"The long-term memory database could not {operation} data.",
            why=f"SQLite reported {type(error).__name__} during the operation.",
            fix=(
                f"Check permissions and disk space for {self.path}. If the file is "
                "corrupted, back it up before rebuilding it. Existing chat and document "
                "data use separate stores."
            ),
        )
