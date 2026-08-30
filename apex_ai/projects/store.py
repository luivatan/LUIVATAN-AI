"""Project workspaces (Phase 71) — named containers linking conversations,
instructions, and a document collection.

A project does not duplicate any existing mechanism. "This project's
documents" is exactly "the documents in the collection this project points
at" — Phase 66/67's ``CollectionStore`` / ``document_ids_for_collection``
infrastructure, reused wholesale rather than reinvented. A project itself
owns only its own identity (name, instructions) plus a pointer to that
collection; conversations reference the project the same way they already
reference a standalone collection (``conversations.project_id``, Phase 71's
migration in ``memory/conversations.py``).

Same per-account isolation discipline as every other store since Phase 55:
every method takes ``user_id`` first, and a missing or mismatched owner is
"not found," never a distinct "forbidden."
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apex_ai.core.errors import DatabaseError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    instructions: str
    collection_id: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "instructions": self.instructions,
            "collection_id": self.collection_id or None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProjectStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.Error as error:
            raise self._error("open", error) from error

    def _error(self, action: str, error: Exception) -> DatabaseError:
        return DatabaseError(
            what=f"Could not {action} the projects database at {self.path}.",
            why=str(error),
            fix="Check disk space and permissions. If the database is corrupted, "
                "back it up and delete the file to start fresh - a project only "
                "holds a name, instructions, and a collection pointer; the "
                "conversations and documents themselves are unaffected.",
        )

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
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    instructions TEXT NOT NULL DEFAULT '',
                    collection_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_projects_user
                    ON projects(user_id, name);
                """
            )

    def create(
        self, user_id: str, name: str, instructions: str = "", collection_id: str = ""
    ) -> Project:
        clean = " ".join(name.strip().split())[:80]
        if not clean:
            raise ValueError("Project name cannot be empty")
        clean_instructions = instructions.strip()[:4000]
        project_id = str(uuid.uuid4())
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO projects"
                    "(id,user_id,name,instructions,collection_id,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (project_id, user_id, clean, clean_instructions, collection_id, now, now),
                )
        except sqlite3.Error as error:
            raise self._error("write to", error) from error
        return Project(project_id, clean, clean_instructions, collection_id, now, now)

    def get(self, user_id: str, project_id: str) -> Project | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM projects WHERE id=? AND user_id=?",
                    (project_id, user_id),
                ).fetchone()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return self._project(row)

    def list(self, user_id: str) -> list[Project]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM projects WHERE user_id=? ORDER BY name COLLATE NOCASE",
                    (user_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return [self._project(row) for row in rows if row is not None]

    def update(
        self,
        user_id: str,
        project_id: str,
        *,
        name: str | None = None,
        instructions: str | None = None,
        collection_id: str | None = None,
    ) -> Project:
        """Update whichever fields are given; ``None`` leaves that field
        unchanged (distinct from ``""``, which clears instructions/collection)."""
        current = self.get(user_id, project_id)
        if current is None:
            raise KeyError(project_id)
        new_name = current.name
        if name is not None:
            new_name = " ".join(name.strip().split())[:80]
            if not new_name:
                raise ValueError("Project name cannot be empty")
        new_instructions = (
            instructions.strip()[:4000] if instructions is not None else current.instructions
        )
        new_collection_id = collection_id if collection_id is not None else current.collection_id
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE projects SET name=?,instructions=?,collection_id=?,updated_at=? "
                    "WHERE id=? AND user_id=?",
                    (new_name, new_instructions, new_collection_id, _now(), project_id, user_id),
                )
        except sqlite3.Error as error:
            raise self._error("write to", error) from error
        if cursor.rowcount == 0:
            raise KeyError(project_id)
        result = self.get(user_id, project_id)
        assert result is not None
        return result

    def delete(self, user_id: str, project_id: str) -> bool:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM projects WHERE id=? AND user_id=?",
                    (project_id, user_id),
                )
        except sqlite3.Error as error:
            raise self._error("write to", error) from error
        return cursor.rowcount > 0

    @staticmethod
    def _project(row) -> Project | None:
        if row is None:
            return None
        return Project(
            id=row["id"],
            name=row["name"],
            instructions=row["instructions"],
            collection_id=row["collection_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


__all__ = ["Project", "ProjectStore"]
