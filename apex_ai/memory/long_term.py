"""Separate persistence for long-term memory and pending confirmations.

Confirmed preferences/context enter ``long_term_memories`` only through explicit
creation or approval. Safe chat candidates may wait briefly in the independent
pending table. Both are prompt context only via Phase 47's relevance-filtered
injection — this store itself does not decide what reaches a prompt.

Phase 55: every row (confirmed, pending, and decision-dedup) belongs to exactly
one account (``user_id``), following the same "check ownership in every method,
not just the top layer" discipline ``memory/conversations.py`` uses.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apex_ai.core.errors import DatabaseError
from apex_ai.security.memory import MemorySafetyPolicy

ALLOWED_MEMORY_KINDS = frozenset({"preference", "ongoing_context"})
PENDING_MEMORY_DAYS = 7
_PROPOSAL_ID = re.compile(r"^memcand_[0-9a-f]{24}$")


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now() -> str:
    return _timestamp(datetime.now(timezone.utc))


def _expires_at() -> str:
    return _timestamp(datetime.now(timezone.utc) + timedelta(days=PENDING_MEMORY_DAYS))


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


def _validate_proposal_id(proposal_id: str) -> str:
    clean = str(proposal_id or "").strip()
    if not _PROPOSAL_ID.fullmatch(clean):
        raise ValueError("Invalid memory proposal ID.")
    return clean


def _validate_rule(rule: str) -> str:
    clean = str(rule or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", clean):
        raise ValueError("Invalid memory proposal rule.")
    return clean


def _equivalent_content(first: str, second: str) -> bool:
    return " ".join(first.split()).casefold() == " ".join(second.split()).casefold()


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


@dataclass(frozen=True)
class PendingMemory:
    id: str
    kind: str
    content: str
    rule: str
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "rule": self.rule,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
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
        self.expired_proposals_on_startup = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
            self.removed_unsafe_on_startup = self._remove_unsafe_existing()
            self.expired_proposals_on_startup = self._delete_expired_proposals()
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

                    CREATE TABLE IF NOT EXISTS pending_memories (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL
                            CHECK(kind IN ('preference','ongoing_context')),
                        content TEXT NOT NULL,
                        rule TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_pending_memories_expiry
                        ON pending_memories(expires_at, created_at, id);

                    CREATE TABLE IF NOT EXISTS memory_candidate_decisions (
                        candidate_id TEXT PRIMARY KEY,
                        decision TEXT NOT NULL
                            CHECK(decision IN ('approved','rejected')),
                        memory_id TEXT,
                        decided_at TEXT NOT NULL
                    );
                    """
                )
                # Phase 55: ownership on every table, including the proposal-dedup
                # table — without it, one user's reject/approve decision on a
                # candidate would silently suppress the same phrase for every
                # other user too, since candidate IDs are content-derived hashes
                # (Phase 43) with no user component of their own.
                self._add_owner_column(connection, "long_term_memories", "idx_long_term_memories_user")
                self._add_owner_column(connection, "pending_memories", "idx_pending_memories_user")
                self._add_owner_column(
                    connection, "memory_candidate_decisions", "idx_memory_candidate_decisions_user"
                )
                # The dedup table's PRIMARY KEY was candidate_id alone; a
                # content-derived ID can now legitimately repeat across users
                # (Phase 43 IDs have no user component), so a table still on the
                # old single-column key gets rebuilt with a composite one.
                pk_columns = [
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(memory_candidate_decisions)")
                    if row["pk"] > 0
                ]
                if pk_columns == ["candidate_id"]:
                    connection.executescript(
                        """
                        CREATE TABLE memory_candidate_decisions_v2 (
                            candidate_id TEXT NOT NULL,
                            user_id TEXT NOT NULL DEFAULT '',
                            decision TEXT NOT NULL
                                CHECK(decision IN ('approved','rejected')),
                            memory_id TEXT,
                            decided_at TEXT NOT NULL,
                            PRIMARY KEY (candidate_id, user_id)
                        );
                        INSERT INTO memory_candidate_decisions_v2
                            SELECT candidate_id, user_id, decision, memory_id, decided_at
                            FROM memory_candidate_decisions;
                        DROP TABLE memory_candidate_decisions;
                        ALTER TABLE memory_candidate_decisions_v2
                            RENAME TO memory_candidate_decisions;
                        CREATE INDEX IF NOT EXISTS idx_memory_candidate_decisions_user
                            ON memory_candidate_decisions(user_id);
                        """
                    )
        except sqlite3.Error as error:
            raise self._error("initialize", error) from error

    @staticmethod
    def _add_owner_column(connection: sqlite3.Connection, table: str, index_name: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "user_id" not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
            connection.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}(user_id)")

    def backfill_owner(self, user_id: str) -> int:
        """Phase 55: assign every not-yet-owned row (confirmed memories, pending
        proposals, and decisions from before this phase) to ``user_id``. Same
        idempotent, additive-migration precedent as
        ``ConversationStore.backfill_owner``."""
        total = 0
        with self._connect() as connection:
            for table in ("long_term_memories", "pending_memories", "memory_candidate_decisions"):
                cursor = connection.execute(
                    f"UPDATE {table} SET user_id=? WHERE user_id=''", (user_id,)
                )
                total += cursor.rowcount
        return total

    def _remove_unsafe_existing(self) -> int:
        """Delete recognized unsafe legacy rows without exposing their content."""
        try:
            with self._connect() as connection:
                memory_rows = connection.execute(
                    "SELECT id,content FROM long_term_memories"
                ).fetchall()
                proposal_rows = connection.execute(
                    "SELECT id,content FROM pending_memories"
                ).fetchall()
                unsafe_memory_ids = [
                    (row["id"],)
                    for row in memory_rows
                    if not self.safety_policy.inspect(row["content"]).safe
                ]
                unsafe_proposal_ids = [
                    (row["id"],)
                    for row in proposal_rows
                    if not self.safety_policy.inspect(row["content"]).safe
                ]
                if unsafe_memory_ids:
                    connection.executemany(
                        "DELETE FROM long_term_memories WHERE id=?",
                        unsafe_memory_ids,
                    )
                if unsafe_proposal_ids:
                    connection.executemany(
                        "DELETE FROM pending_memories WHERE id=?",
                        unsafe_proposal_ids,
                    )
                return len(unsafe_memory_ids) + len(unsafe_proposal_ids)
        except sqlite3.Error as error:
            raise self._error("apply safety checks to", error) from error

    def _delete_expired_proposals(self) -> int:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM pending_memories WHERE expires_at<=?",
                    (_now(),),
                )
                return max(0, cursor.rowcount)
        except sqlite3.Error as error:
            raise self._error("expire pending", error) from error

    def propose_candidate(
        self,
        user_id: str,
        proposal_id: str,
        *,
        content: str,
        kind: str,
        rule: str,
    ) -> PendingMemory | None:
        """Persist a safe proposal, never a confirmed memory, for user review."""
        clean_id = _validate_proposal_id(proposal_id)
        clean_content = _validate_content(content)
        clean_kind = _validate_kind(kind)
        clean_rule = _validate_rule(rule)
        self.safety_policy.require_safe(clean_content)
        self._delete_expired_proposals()
        now = _now()
        expires_at = _expires_at()
        try:
            with self._connect() as connection:
                decision = connection.execute(
                    "SELECT 1 FROM memory_candidate_decisions WHERE candidate_id=? AND user_id=?",
                    (clean_id, user_id),
                ).fetchone()
                if decision is not None:
                    return None

                existing_memories = connection.execute(
                    "SELECT id,content FROM long_term_memories WHERE kind=? AND user_id=?",
                    (clean_kind, user_id),
                ).fetchall()
                for memory in existing_memories:
                    if _equivalent_content(memory["content"], clean_content):
                        connection.execute(
                            """INSERT OR REPLACE INTO memory_candidate_decisions
                               (candidate_id,user_id,decision,memory_id,decided_at)
                               VALUES (?,?,'approved',?,?)""",
                            (clean_id, user_id, memory["id"], now),
                        )
                        return None

                existing = connection.execute(
                    "SELECT * FROM pending_memories WHERE id=? AND user_id=?",
                    (clean_id, user_id),
                ).fetchone()
                if existing is not None:
                    proposal = self._pending_record(existing)
                    if (
                        proposal.kind != clean_kind
                        or not _equivalent_content(proposal.content, clean_content)
                        or proposal.rule != clean_rule
                    ):
                        raise ValueError(
                            "Memory proposal ID conflicts with existing data."
                        )
                    return proposal

                connection.execute(
                    """INSERT INTO pending_memories
                       (id,user_id,kind,content,rule,created_at,expires_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (clean_id, user_id, clean_kind, clean_content, clean_rule, now, expires_at),
                )
        except sqlite3.Error as error:
            raise self._error("create a pending", error) from error
        return PendingMemory(
            clean_id,
            clean_kind,
            clean_content,
            clean_rule,
            now,
            expires_at,
        )

    def pending(self, user_id: str, *, limit: int = 100) -> list[PendingMemory]:
        limit = max(1, min(int(limit), 500))
        self._remove_unsafe_existing()
        self._delete_expired_proposals()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT * FROM pending_memories WHERE user_id=?
                       ORDER BY created_at ASC,id ASC LIMIT ?""",
                    (user_id, limit),
                ).fetchall()
        except sqlite3.Error as error:
            raise self._error("list pending", error) from error
        return [self._pending_record(row) for row in rows]

    def approve_candidate(self, user_id: str, proposal_id: str) -> LongTermMemory:
        """Move one still-safe proposal to confirmed memory atomically."""
        clean_id = _validate_proposal_id(proposal_id)
        self._delete_expired_proposals()
        now = _now()
        blocked_content: str | None = None
        memory: LongTermMemory | None = None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM pending_memories WHERE id=? AND user_id=?",
                    (clean_id, user_id),
                ).fetchone()
                if row is None:
                    raise KeyError(clean_id)
                proposal = self._pending_record(row)
                safety = self.safety_policy.inspect(proposal.content)
                if not safety.safe:
                    connection.execute(
                        "DELETE FROM pending_memories WHERE id=? AND user_id=?",
                        (clean_id, user_id),
                    )
                    connection.execute(
                        """INSERT OR REPLACE INTO memory_candidate_decisions
                           (candidate_id,user_id,decision,memory_id,decided_at)
                           VALUES (?,?,'rejected',NULL,?)""",
                        (clean_id, user_id, now),
                    )
                    blocked_content = proposal.content
                else:
                    existing_rows = connection.execute(
                        "SELECT * FROM long_term_memories WHERE kind=? AND user_id=?",
                        (proposal.kind, user_id),
                    ).fetchall()
                    memory = next(
                        (
                            self._record(item)
                            for item in existing_rows
                            if _equivalent_content(item["content"], proposal.content)
                        ),
                        None,
                    )
                    if memory is None:
                        memory = LongTermMemory(
                            id=str(uuid.uuid4()),
                            kind=proposal.kind,
                            content=proposal.content,
                            created_at=now,
                            updated_at=now,
                        )
                        connection.execute(
                            """INSERT INTO long_term_memories
                               (id,user_id,kind,content,created_at,updated_at)
                               VALUES (?,?,?,?,?,?)""",
                            (
                                memory.id,
                                user_id,
                                memory.kind,
                                memory.content,
                                memory.created_at,
                                memory.updated_at,
                            ),
                        )
                    connection.execute(
                        "DELETE FROM pending_memories WHERE id=? AND user_id=?",
                        (clean_id, user_id),
                    )
                    connection.execute(
                        """INSERT OR REPLACE INTO memory_candidate_decisions
                           (candidate_id,user_id,decision,memory_id,decided_at)
                           VALUES (?,?,'approved',?,?)""",
                        (clean_id, user_id, memory.id, now),
                    )
        except sqlite3.Error as error:
            raise self._error("approve pending", error) from error

        if blocked_content is not None:
            self.safety_policy.require_safe(blocked_content)
        if memory is None:  # pragma: no cover - guarded by the branches above
            raise RuntimeError("Memory approval produced no result.")
        return memory

    def reject_candidate(self, user_id: str, proposal_id: str) -> bool:
        """Delete pending content and retain only its content-derived ID tombstone."""
        clean_id = _validate_proposal_id(proposal_id)
        self._delete_expired_proposals()
        now = _now()
        try:
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM pending_memories WHERE id=? AND user_id=?",
                    (clean_id, user_id),
                ).fetchone()
                if exists is None:
                    return False
                connection.execute(
                    "DELETE FROM pending_memories WHERE id=? AND user_id=?",
                    (clean_id, user_id),
                )
                connection.execute(
                    """INSERT OR REPLACE INTO memory_candidate_decisions
                       (candidate_id,user_id,decision,memory_id,decided_at)
                       VALUES (?,?,'rejected',NULL,?)""",
                    (clean_id, user_id, now),
                )
                return True
        except sqlite3.Error as error:
            raise self._error("reject pending", error) from error

    def create(self, user_id: str, content: str, *, kind: str) -> LongTermMemory:
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
                       (id,user_id,kind,content,created_at,updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    (memory_id, user_id, clean_kind, clean_content, now, now),
                )
        except sqlite3.Error as error:
            raise self._error("create", error) from error
        return LongTermMemory(memory_id, clean_kind, clean_content, now, now)

    def get(self, user_id: str, memory_id: str) -> LongTermMemory | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM long_term_memories WHERE id=? AND user_id=?",
                    (memory_id, user_id),
                ).fetchone()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return self._record(row) if row else None

    def list(
        self,
        user_id: str,
        *,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[LongTermMemory]:
        clean_kind = _validate_kind(kind) if kind is not None else None
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM long_term_memories WHERE user_id=?"
        params: list[Any] = [user_id]
        if clean_kind is not None:
            sql += " AND kind=?"
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
        user_id: str,
        memory_id: str,
        *,
        content: str | None = None,
        kind: str | None = None,
    ) -> LongTermMemory:
        """Update explicit fields without changing the stable memory ID."""
        existing = self.get(user_id, memory_id)
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
                       SET kind=?,content=?,updated_at=? WHERE id=? AND user_id=?""",
                    (clean_kind, clean_content, updated_at, memory_id, user_id),
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

    def delete(self, user_id: str, memory_id: str) -> bool:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM long_term_memories WHERE id=? AND user_id=?",
                    (memory_id, user_id),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as error:
            raise self._error("delete", error) from error

    def clear(self, user_id: str, *, kind: str | None = None) -> int:
        clean_kind = _validate_kind(kind) if kind is not None else None
        try:
            with self._connect() as connection:
                if clean_kind is None:
                    count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM long_term_memories WHERE user_id=?",
                            (user_id,),
                        ).fetchone()[0]
                    )
                    connection.execute(
                        "DELETE FROM long_term_memories WHERE user_id=?", (user_id,)
                    )
                else:
                    count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM long_term_memories WHERE kind=? AND user_id=?",
                            (clean_kind, user_id),
                        ).fetchone()[0]
                    )
                    connection.execute(
                        "DELETE FROM long_term_memories WHERE kind=? AND user_id=?",
                        (clean_kind, user_id),
                    )
                return count
        except sqlite3.Error as error:
            raise self._error("clear", error) from error

    def count(self, user_id: str, *, kind: str | None = None) -> int:
        clean_kind = _validate_kind(kind) if kind is not None else None
        try:
            with self._connect() as connection:
                if clean_kind is None:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM long_term_memories WHERE user_id=?",
                        (user_id,),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM long_term_memories WHERE kind=? AND user_id=?",
                        (clean_kind, user_id),
                    ).fetchone()
                return int(row[0])
        except sqlite3.Error as error:
            raise self._error("count", error) from error

    @staticmethod
    def _pending_record(row: sqlite3.Row) -> PendingMemory:
        return PendingMemory(
            id=row["id"],
            kind=row["kind"],
            content=row["content"],
            rule=row["rule"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

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
