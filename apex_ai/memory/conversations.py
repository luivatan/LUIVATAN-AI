"""Persistent multi-conversation history for the web interface.

This store is deliberately separate from document evidence. It supplies recent turns
only as conversational context; the RAG engine still retrieves every factual source
from ChromaDB and citations still come only from chunks sent to the model.
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
            "preview": self.preview,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "citations": list(self.citations),
            "status": self.status,
            "created_at": self.created_at,
        }


class ConversationStore:
    """SQLite-backed conversation repository for the local single-user app."""

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

    def create(self, title: str = "New conversation") -> Conversation:
        conversation_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO conversations(id,title,created_at,updated_at) VALUES (?,?,?,?)",
                (conversation_id, title.strip()[:100] or "New conversation", now, now),
            )
        return Conversation(conversation_id, title, now, now)

    def get(self, conversation_id: str) -> Conversation | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT c.*,(SELECT COUNT(*) FROM messages m
                   WHERE m.conversation_id=c.id) AS message_count
                   FROM conversations c WHERE c.id=?""",
                (conversation_id,),
            ).fetchone()
        return self._conversation(row)

    def list(self, search: str = "", limit: int = 100) -> list[Conversation]:
        limit = max(1, min(int(limit), 200))
        term = search.strip()
        params: list[Any] = []
        where = ""
        if term:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            where = (
                "WHERE c.title LIKE ? ESCAPE '\\' OR EXISTS "
                "(SELECT 1 FROM messages sm WHERE sm.conversation_id=c.id "
                "AND sm.content LIKE ? ESCAPE '\\')"
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

    def rename(self, conversation_id: str, title: str) -> Conversation:
        clean = " ".join(title.strip().split())[:100]
        if not clean:
            raise ValueError("Conversation title cannot be empty")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                (clean, _now(), conversation_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(conversation_id)
        result = self.get(conversation_id)
        assert result is not None
        return result

    def delete(self, conversation_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            return cursor.rowcount > 0

    def clear(self) -> int:
        with self._connect() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
            connection.execute("DELETE FROM conversations")
            return count

    def messages(self, conversation_id: str) -> list[Message]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,id",
                (conversation_id,),
            ).fetchall()
        return [self._message(row) for row in rows]

    def add_message(
        self,
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
                "SELECT title FROM conversations WHERE id=?", (conversation_id,)
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

    def last_user_message(self, conversation_id: str) -> Message | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM messages WHERE conversation_id=? AND role='user'
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (conversation_id,),
            ).fetchone()
        return self._message(row) if row else None

    def remove_answers_after(self, message: Message) -> int:
        """Remove assistant output after a selected user message before regeneration."""
        with self._connect() as connection:
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
        self, conversation_id: str, limit: int, *, exclude_user_message_id: str | None = None
    ) -> list[dict[str, str]]:
        """Return paired turns in the shape expected by RagEngine memory."""
        messages = self.messages(conversation_id)
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
        )

    @staticmethod
    def _message(row) -> Message:
        try:
            citations = json.loads(row["citations_json"] or "[]")
        except json.JSONDecodeError:
            citations = []
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            citations=tuple(citations if isinstance(citations, list) else []),
            status=row["status"],
            created_at=row["created_at"],
        )


class ConversationMemoryAdapter:
    """Read-only RagEngine memory view for one selected conversation.

    ``add`` is intentionally a no-op: the streaming controller persists the final
    answer together with its verified citation payload in one place.
    """

    def __init__(
        self,
        store: ConversationStore,
        conversation_id: str,
        limit: int,
        *,
        exclude_user_message_id: str | None = None,
    ) -> None:
        self.store = store
        self.conversation_id = conversation_id
        self.limit = limit
        self.exclude_user_message_id = exclude_user_message_id

    def recent(self, n: int | None = None) -> list[dict[str, str]]:
        return self.store.recent_turns(
            self.conversation_id,
            n or self.limit,
            exclude_user_message_id=self.exclude_user_message_id,
        )

    def add(self, user: str, assistant: str) -> None:
        # RagEngine calls memory.add during finalization. The controller writes the
        # richer record (status + citations) after receiving the final result.
        return None
