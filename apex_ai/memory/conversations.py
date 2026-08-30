"""Persistent multi-conversation history for the web interface.

This store is deliberately separate from document evidence. It supplies recent turns
only as conversational context; the RAG engine still retrieves every factual source
from ChromaDB and citations still come only from chunks sent to the model.

Phase 55: every conversation belongs to exactly one account (``user_id``). Every
method that reads or writes conversation/message data takes the caller's
``user_id`` and filters or checks ownership against it — not only the top-level
``get``/``list``/``delete``, so a future call site can't accidentally bypass
isolation by reaching for a message-level method directly. A missing or mismatched
owner is treated the same as "does not exist" (``None``/``KeyError``/no rows
affected), never a distinct "forbidden" signal — the API layer must not let a
caller learn that a conversation exists at all if it isn't theirs.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apex_ai.core.logging import get_logger

log = get_logger("memory.conversations")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _title_from(text: str) -> str:
    title = " ".join(text.strip().split())
    if len(title) <= 52:
        return title or "New conversation"
    return title[:49].rstrip() + "…"


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0
    preview: str = ""
    # Phase 67: "" means no knowledge-base restriction (retrieval searches the
    # whole account's library, the pre-Phase-67 default). A real collection ID
    # scopes this conversation's retrieval to that collection only.
    collection_id: str = ""
    # Phase 71: "" means this conversation is not in a project (the default,
    # and every conversation before this phase). A real project ID means the
    # project's own collection_id and instructions govern retrieval scoping
    # and prompt instructions instead of this conversation's own collection_id
    # (see stream_chat's document_ids resolution in api/chat.py).
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
            "preview": self.preview,
            "collection_id": self.collection_id or None,
            "project_id": self.project_id or None,
        }


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    role: str
    content: str
    citations: tuple[dict, ...]
    status: str
    created_at: str
    feedback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "citations": list(self.citations),
            "status": self.status,
            "created_at": self.created_at,
            "feedback": self.feedback,
        }


class ConversationStore:
    """SQLite-backed, per-account conversation repository."""

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
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'complete'
                        CHECK(status IN ('complete','stopped','error')),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_updated
                    ON conversations(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, created_at);
                """
            )
            # Phase 17: added after the original schema shipped. SQLite has no
            # portable "ADD COLUMN IF NOT EXISTS", so check first; this keeps
            # existing conversations.db files (with real user history) working
            # without a destructive rebuild.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
            if "feedback" not in columns:
                connection.execute(
                    "ALTER TABLE messages ADD COLUMN feedback TEXT "
                    "CHECK(feedback IN ('up','down') OR feedback IS NULL)"
                )
            # Phase 50: a rolling summary of turns that have fallen out of the
            # live short-term context window (see memory/summarization.py),
            # plus how many messages it already covers so later turns only
            # summarize what's newly fallen out, not the whole conversation
            # again. Not part of Conversation.to_dict() / the public API -
            # this is prompt-construction state, not a user-facing field.
            conversation_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(conversations)")
            }
            if "summary" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN summary TEXT NOT NULL DEFAULT ''"
                )
            if "summarized_message_count" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN "
                    "summarized_message_count INTEGER NOT NULL DEFAULT 0"
                )
            # Phase 55: ownership. Empty string (not NULL) is the "not yet
            # backfilled" marker for a pre-existing single-tenant database;
            # backfill_owner() assigns those rows to a real account.
            if "user_id" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_conversations_user "
                    "ON conversations(user_id, updated_at DESC)"
                )
            # Phase 67: which knowledge-base collection (if any) this
            # conversation's retrieval is scoped to. "" = unscoped (searches
            # the whole account library), same as every conversation before
            # this phase - no backfill needed, the default is the old behavior.
            if "collection_id" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN collection_id TEXT NOT NULL DEFAULT ''"
                )
            # Phase 71: which project (if any) this conversation belongs to.
            # "" = no project, same as every conversation before this phase -
            # no backfill needed, the default is the old (unscoped) behavior.
            if "project_id" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN project_id TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_conversations_project "
                    "ON conversations(user_id, project_id)"
                )

    def backfill_owner(self, user_id: str) -> int:
        """Phase 55: assign every not-yet-owned conversation (from before this
        phase, or any future never-owned row) to ``user_id``. Idempotent — a
        conversation that already has an owner is left untouched. Intended to
        run once at startup against the default local account, exactly the
        "existing data keeps working" precedent Phase 17/46/50 already set for
        additive schema changes."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET user_id=? WHERE user_id=''", (user_id,)
            )
            return cursor.rowcount

    def create(
        self,
        user_id: str,
        title: str = "New conversation",
        collection_id: str = "",
        project_id: str = "",
    ) -> Conversation:
        conversation_id = str(uuid.uuid4())
        now = _now()
        clean_title = title.strip()[:100] or "New conversation"
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO conversations"
                "(id,user_id,title,created_at,updated_at,collection_id,project_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (conversation_id, user_id, clean_title, now, now, collection_id, project_id),
            )
        return Conversation(
            conversation_id, clean_title, now, now,
            collection_id=collection_id, project_id=project_id,
        )

    def get(self, user_id: str, conversation_id: str) -> Conversation | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT c.*,(SELECT COUNT(*) FROM messages m
                   WHERE m.conversation_id=c.id) AS message_count
                   FROM conversations c WHERE c.id=? AND c.user_id=?""",
                (conversation_id, user_id),
            ).fetchone()
        return self._conversation(row)

    def _owns(self, connection: sqlite3.Connection, user_id: str, conversation_id: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user_id),
        ).fetchone()
        return row is not None

    def list(
        self,
        user_id: str,
        search: str = "",
        limit: int = 100,
        project_id: str | None = None,
    ) -> list[Conversation]:
        """``project_id=None`` (default) does not filter by project at all;
        ``""`` returns only conversations with no project, and a real ID
        returns only that project's conversations (Phase 71 — mirrors
        ``IngestionService.list_documents``'s ``collection_id`` semantics)."""
        limit = max(1, min(int(limit), 200))
        term = search.strip()
        params: list[Any] = [user_id]
        where = "WHERE c.user_id=?"
        if project_id is not None:
            where += " AND c.project_id=?"
            params.append(project_id)
        if term:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            where += (
                " AND (c.title LIKE ? ESCAPE '\\' OR EXISTS "
                "(SELECT 1 FROM messages sm WHERE sm.conversation_id=c.id "
                "AND sm.content LIKE ? ESCAPE '\\'))"
            )
            params.extend([like, like])
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT c.*,
                    (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id)
                        AS message_count,
                    COALESCE((SELECT substr(content,1,120) FROM messages p
                        WHERE p.conversation_id=c.id ORDER BY p.created_at DESC LIMIT 1),'')
                        AS preview
                    FROM conversations c {where}
                    ORDER BY c.updated_at DESC LIMIT ?""",
                params,
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def rename(self, user_id: str, conversation_id: str, title: str) -> Conversation:
        clean = " ".join(title.strip().split())[:100]
        if not clean:
            raise ValueError("Conversation title cannot be empty")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET title=?,updated_at=? WHERE id=? AND user_id=?",
                (clean, _now(), conversation_id, user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(conversation_id)
        result = self.get(user_id, conversation_id)
        assert result is not None
        return result

    def set_collection(
        self, user_id: str, conversation_id: str, collection_id: str
    ) -> Conversation:
        """Scope (or unscope, with ``collection_id=""``) this conversation's
        retrieval to one knowledge-base collection (Phase 67)."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET collection_id=?,updated_at=? WHERE id=? AND user_id=?",
                (collection_id, _now(), conversation_id, user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(conversation_id)
        result = self.get(user_id, conversation_id)
        assert result is not None
        return result

    def set_project(self, user_id: str, conversation_id: str, project_id: str) -> Conversation:
        """Move this conversation into (or out of, with ``project_id=""``) a
        project (Phase 71)."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET project_id=?,updated_at=? WHERE id=? AND user_id=?",
                (project_id, _now(), conversation_id, user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(conversation_id)
        result = self.get(user_id, conversation_id)
        assert result is not None
        return result

    def unassign_project(self, user_id: str, project_id: str) -> int:
        """Clear every conversation's reference to a project that's being
        deleted (Phase 71) - conversations themselves are untouched, they
        just leave the project (same precedent as
        ``IngestionService.unassign_collection``). Deliberately leaves
        ``updated_at`` alone: this is bookkeeping triggered by deleting the
        project, not something the user did to each conversation, and must
        not reorder the conversation list as a side effect (same reasoning
        as ``update_summary`` above)."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET project_id='' WHERE user_id=? AND project_id=?",
                (user_id, project_id),
            )
            return cursor.rowcount

    def delete(self, user_id: str, conversation_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)
            )
            return cursor.rowcount > 0

    def clear(self, user_id: str) -> int:
        """Deletes only ``user_id``'s own conversations."""
        with self._connect() as connection:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM conversations WHERE user_id=?", (user_id,)
                ).fetchone()[0]
            )
            connection.execute("DELETE FROM conversations WHERE user_id=?", (user_id,))
            return count

    def summary_state(self, user_id: str, conversation_id: str) -> tuple[str, int]:
        """Phase 50/55: the conversation's rolling summary and how many messages
        (oldest-first) it already covers. ``("", 0)`` for an unknown, not-owned,
        or never-summarized conversation."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT summary,summarized_message_count FROM conversations WHERE id=? AND user_id=?",
                (conversation_id, user_id),
            ).fetchone()
        if row is None:
            return "", 0
        return row["summary"] or "", int(row["summarized_message_count"] or 0)

    def update_summary(
        self, user_id: str, conversation_id: str, summary: str, summarized_message_count: int
    ) -> None:
        """Phase 50/55: persist a regenerated rolling summary. Does not touch
        ``updated_at`` — this is prompt-construction bookkeeping, not a change
        a user made, and must not reorder the conversation list. A no-op if
        ``conversation_id`` isn't owned by ``user_id``."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE conversations SET summary=?,summarized_message_count=? "
                "WHERE id=? AND user_id=?",
                (summary, max(0, int(summarized_message_count)), conversation_id, user_id),
            )

    def messages(self, user_id: str, conversation_id: str) -> list[Message]:
        with self._connect() as connection:
            if not self._owns(connection, user_id, conversation_id):
                return []
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,id",
                (conversation_id,),
            ).fetchall()
        return [self._message(row) for row in rows]

    def add_message(
        self,
        user_id: str,
        conversation_id: str,
        role: str,
        content: str,
        *,
        citations: list[dict] | None = None,
        status: str = "complete",
    ) -> Message:
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        if status not in {"complete", "stopped", "error"}:
            raise ValueError("invalid message status")
        message_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT title FROM conversations WHERE id=? AND user_id=?",
                (conversation_id, user_id),
            ).fetchone()
            if existing is None:
                raise KeyError(conversation_id)
            connection.execute(
                """INSERT INTO messages
                   (id,conversation_id,role,content,citations_json,status,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    message_id,
                    conversation_id,
                    role,
                    content,
                    json.dumps(citations or [], ensure_ascii=False),
                    status,
                    now,
                ),
            )
            title = existing["title"]
            if role == "user" and title == "New conversation":
                title = _title_from(content)
            connection.execute(
                "UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                (title, now, conversation_id),
            )
        return Message(
            message_id, conversation_id, role, content, tuple(citations or []), status, now
        )

    def set_feedback(
        self, user_id: str, conversation_id: str, message_id: str, feedback: str | None
    ) -> Message:
        """Set (or clear, with ``None``) a user's up/down reaction to one assistant
        message. Local, per-user signal only — not aggregated or sent anywhere."""
        if feedback not in {"up", "down", None}:
            raise ValueError("feedback must be 'up', 'down', or null")
        with self._connect() as connection:
            if not self._owns(connection, user_id, conversation_id):
                raise KeyError(message_id)
            cursor = connection.execute(
                """UPDATE messages SET feedback=?
                   WHERE id=? AND conversation_id=? AND role='assistant'""",
                (feedback, message_id, conversation_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(message_id)
            row = connection.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        return self._message(row)

    def last_user_message(self, user_id: str, conversation_id: str) -> Message | None:
        with self._connect() as connection:
            if not self._owns(connection, user_id, conversation_id):
                return None
            row = connection.execute(
                """SELECT * FROM messages WHERE conversation_id=? AND role='user'
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (conversation_id,),
            ).fetchone()
        return self._message(row) if row else None

    def remove_answers_after(self, user_id: str, message: Message) -> int:
        """Remove assistant output after a selected user message before regeneration."""
        with self._connect() as connection:
            if not self._owns(connection, user_id, message.conversation_id):
                return 0
            cursor = connection.execute(
                """DELETE FROM messages WHERE conversation_id=? AND role='assistant'
                   AND created_at>=?""",
                (message.conversation_id, message.created_at),
            )
            connection.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (_now(), message.conversation_id),
            )
            return cursor.rowcount

    def recent_turns(
        self,
        user_id: str,
        conversation_id: str,
        limit: int,
        *,
        exclude_user_message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Return paired turns in the shape expected by RagEngine memory."""
        messages = self.messages(user_id, conversation_id)
        if exclude_user_message_id:
            messages = [message for message in messages if message.id != exclude_user_message_id]
        turns: list[dict[str, str]] = []
        pending_user: str | None = None
        for message in messages:
            if message.role == "user":
                pending_user = message.content
            elif pending_user is not None and message.status in {"complete", "stopped"}:
                turns.append({"user": pending_user, "assistant": message.content})
                pending_user = None
        return turns[-max(1, limit):]

    @staticmethod
    def _conversation(row) -> Conversation | None:
        if row is None:
            return None
        keys = set(row.keys())
        return Conversation(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=int(row["message_count"]) if "message_count" in keys else 0,
            preview=row["preview"] if "preview" in keys else "",
            collection_id=row["collection_id"] if "collection_id" in keys else "",
            project_id=row["project_id"] if "project_id" in keys else "",
        )

    @staticmethod
    def _message(row) -> Message:
        try:
            citations = json.loads(row["citations_json"] or "[]")
        except json.JSONDecodeError:
            citations = []
        keys = set(row.keys())
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            citations=tuple(citations if isinstance(citations, list) else []),
            status=row["status"],
            created_at=row["created_at"],
            feedback=row["feedback"] if "feedback" in keys else None,
        )


class ConversationMemoryAdapter:
    """Read-only RagEngine memory view for one selected conversation.

    ``add`` is intentionally a no-op: the streaming controller persists the final
    answer together with its verified citation payload in one place.
    """

    def __init__(
        self,
        store: ConversationStore,
        user_id: str,
        conversation_id: str,
        limit: int,
        *,
        exclude_user_message_id: str | None = None,
    ) -> None:
        self.store = store
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.limit = limit
        self.exclude_user_message_id = exclude_user_message_id

    def recent(self, n: int | None = None) -> list[dict[str, str]]:
        return self.store.recent_turns(
            self.user_id,
            self.conversation_id,
            n or self.limit,
            exclude_user_message_id=self.exclude_user_message_id,
        )

    def add(self, user: str, assistant: str) -> None:
        # RagEngine calls memory.add during finalization. The controller writes the
        # richer record (status + citations) after receiving the final result.
        return None

    def summary_text(self) -> str:
        """Phase 50: the conversation's rolling summary of turns older than
        what ``recent()`` returns in full. RagEngine duck-types this method
        (``getattr(..., "summary_text", None)``); implementing it here, and
        not on the legacy JSON ConversationMemory, scopes summarization to the
        SQLite-backed web chat this phase targets."""
        summary, _ = self.store.summary_state(self.user_id, self.conversation_id)
        return summary
