"""Conversation memory.

Architectural rule: memory is **not** evidence. It exists only so the model
can resolve conversational context ("what about children?" after a question
about fever dosage). It never enters the retrieved-evidence context block and
can never be cited as a source. Document facts come only from the vector
store.

Persistence: a small JSON file (configurable path). A corrupted file is moved
aside instead of crashing or silently losing the flag that something broke.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from apex_ai.core.logging import get_logger
from apex_ai.documents.models import utc_now_iso

log = get_logger("memory")


class ConversationMemory:
    def __init__(self, path: Path, limit: int = 8) -> None:
        self.path = Path(path)
        self.limit = max(1, limit)
        self.turns: list[dict] = []
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self.turns = [t for t in data if isinstance(t, dict) and "user" in t]
        except (json.JSONDecodeError, OSError) as error:
            backup = self.path.with_suffix(".corrupt.bak")
            try:
                shutil.move(str(self.path), backup)
                log.warning(
                    "Conversation memory was unreadable and was backed up "
                    "(error_type=%s, backup=%s)",
                    type(error).__name__,
                    backup.name,
                )
            except OSError as backup_error:
                log.warning(
                    "Conversation memory was unreadable and could not be backed up "
                    "(read_error_type=%s, backup_error_type=%s)",
                    type(error).__name__,
                    type(backup_error).__name__,
                )
            self.turns = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.write_text(
                json.dumps(self.turns, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as error:
            log.error(
                "Could not save conversation memory (error_type=%s)",
                type(error).__name__,
            )

    # -- operations -----------------------------------------------------------

    def add(self, user: str, assistant: str) -> None:
        self.turns.append({"user": user, "assistant": assistant, "at": utc_now_iso()})
        if len(self.turns) > self.limit:
            self.turns = self.turns[-self.limit:]
        self.save()

    def recent(self, n: int | None = None) -> list[dict]:
        n = self.limit if n is None else n
        return self.turns[-n:]

    def as_messages(self, n: int | None = None) -> list[dict]:
        """History in chat-messages form (for chat-template providers)."""
        return [
            {"role": role, "content": turn[role]}
            for turn in self.recent(n)
            for role in ("user", "assistant")
        ]

    def display(self) -> str:
        if not self.turns:
            return "No conversation yet."
        return "\n\n".join(
            f"User: {turn['user']}\nAssistant: {turn['assistant']}" for turn in self.turns
        )

    def clear(self) -> None:
        self.turns = []
        self.save()
        log.info("Conversation memory cleared")
