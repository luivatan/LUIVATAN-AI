"""Document collections (Phase 66) — named groupings of an account's documents.

A collection is a pure organizational label: it holds a name and an owner,
never document content or bytes. Document-to-collection membership lives in
``IngestionService``'s own registry (``DocumentInfo.collection_id``), not
here — this store only needs to answer "what collections does this account
have" and "does this one still exist," which is exactly what naming and
deleting a collection need to check.

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
class Collection:
    id: str
    name: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CollectionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.Error as error:
            raise self._error("open", error) from error

    def _error(self, action: str, error: Exception) -> DatabaseError:
        return DatabaseError(
            what=f"Could not {action} the collections database at {self.path}.",
            why=str(error),
            fix="Check disk space and permissions. If the database is corrupted, "
                "back it up and delete the file to start fresh - collections are "
                "labels only; the documents themselves are unaffected.",
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
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_collections_user
                    ON collections(user_id, name);
                """
            )

    def create(self, user_id: str, name: str) -> Collection:
        clean = " ".join(name.strip().split())[:80]
        if not clean:
            raise ValueError("Collection name cannot be empty")
        collection_id = str(uuid.uuid4())
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO collections(id,user_id,name,created_at,updated_at) "
                    "VALUES (?,?,?,?,?)",
                    (collection_id, user_id, clean, now, now),
                )
        except sqlite3.Error as error:
            raise self._error("write to", error) from error
        return Collection(collection_id, clean, now, now)

    def get(self, user_id: str, collection_id: str) -> Collection | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM collections WHERE id=? AND user_id=?",
                    (collection_id, user_id),
                ).fetchone()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return self._collection(row)

    def list(self, user_id: str) -> list[Collection]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM collections WHERE user_id=? ORDER BY name COLLATE NOCASE",
                    (user_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise self._error("read", error) from error
        return [self._collection(row) for row in rows if row is not None]

    def rename(self, user_id: str, collection_id: str, name: str) -> Collection:
        clean = " ".join(name.strip().split())[:80]
        if not clean:
            raise ValueError("Collection name cannot be empty")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE collections SET name=?,updated_at=? WHERE id=? AND user_id=?",
                    (clean, _now(), collection_id, user_id),
                )
        except sqlite3.Error as error:
            raise self._error("write to", error) from error
        if cursor.rowcount == 0:
            raise KeyError(collection_id)
        result = self.get(user_id, collection_id)
        assert result is not None
        return result

    def delete(self, user_id: str, collection_id: str) -> bool:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM collections WHERE id=? AND user_id=?",
                    (collection_id, user_id),
                )
        except sqlite3.Error as error:
            raise self._error("write to", error) from error
        return cursor.rowcount > 0

    @staticmethod
    def _collection(row) -> Collection | None:
        if row is None:
            return None
        return Collection(
            id=row["id"],
            name=row["name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


__all__ = ["Collection", "CollectionStore"]
