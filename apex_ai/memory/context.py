"""Bounded short-term conversation context for one generation turn.

This module does not implement long-term memory. It turns already-persisted
question/answer pairs into a small, auditable prompt section. Conversation
context remains separate from retrieved document evidence and is never a
citation source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TRUNCATION_MARKER = " …[truncated]… "
_GENERATED_SOURCES_FOOTER = re.compile(
    r"\n{2,}Sources:\s*\n(?:\s*\*\s+\[\d+\].*(?:\n|$))+\s*$",
    re.IGNORECASE,
)
_STALE_CITATION_MARKER = re.compile(r"(?<!\w)\[\d+\](?!\w)")


@dataclass(frozen=True)
class ConversationContext:
    """The exact bounded history supplied to query analysis and generation."""

    text: str
    turns: list[dict[str, str]] = field(default_factory=list)
    input_turn_count: int = 0
    dropped_turn_count: int = 0
    truncated_message_count: int = 0
    stripped_source_footer_count: int = 0
    stripped_citation_marker_count: int = 0
    max_turns: int = 0
    char_limit: int = 0
    message_char_limit: int = 0

    @property
    def character_count(self) -> int:
        return len(self.text)

    def diagnostics(self, *, include_text: bool = False) -> dict:
        payload = {
            "input_turn_count": self.input_turn_count,
            "included_turn_count": len(self.turns),
            "dropped_turn_count": self.dropped_turn_count,
            "truncated_message_count": self.truncated_message_count,
            "stripped_source_footer_count": self.stripped_source_footer_count,
            "stripped_citation_marker_count": self.stripped_citation_marker_count,
            "character_count": self.character_count,
            "character_limit": self.char_limit,
            "message_character_limit": self.message_char_limit,
            "max_turns": self.max_turns,
        }
        if include_text:
            payload["text"] = self.text
        return payload


def _truncate_middle(text: str, limit: int) -> tuple[str, bool]:
    """Keep both ends of long conversational text under a strict limit."""
    text = (text or "").strip()
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text, False
    if limit == 0:
        return "", True
    if limit <= len(_TRUNCATION_MARKER):
        return text[:limit], True

    remaining = limit - len(_TRUNCATION_MARKER)
    head = (remaining + 1) // 2
    tail = remaining - head
    suffix = text[-tail:] if tail else ""
    return f"{text[:head]}{_TRUNCATION_MARKER}{suffix}", True


def _strip_generated_sources(text: str) -> tuple[str, bool]:
    """Remove only the engine's generated citation-list footer from legacy memory."""
    clean = (text or "").strip()
    stripped = _GENERATED_SOURCES_FOOTER.sub("", clean).rstrip()
    return stripped, stripped != clean


def _strip_stale_citation_markers(text: str) -> tuple[str, int]:
    """Prevent prior answer markers from pointing at new evidence block numbers."""
    stripped, count = _STALE_CITATION_MARKER.subn("", text)
    return stripped, count


def _format_turn(turn: dict[str, str]) -> str:
    return f"User: {turn['user']}\nAssistant: {turn['assistant']}"


def _fit_single_turn(
    user: str,
    assistant: str,
    char_limit: int,
) -> tuple[dict[str, str] | None, set[str]]:
    """Fit the newest turn when even its per-message-bounded form is too large."""
    overhead = len("User: \nAssistant: ")
    content_limit = char_limit - overhead
    if content_limit <= 0:
        return None, set()

    # Reserve space for both sides of the exchange, then give unused space to
    # whichever side still needs it. This avoids dropping the question or the
    # answer entirely merely because the other side is unusually long.
    user_limit = min(len(user), content_limit // 2)
    assistant_limit = min(len(assistant), content_limit - user_limit)
    spare = content_limit - user_limit - assistant_limit
    if spare:
        user_remaining = len(user) - user_limit
        add_user = min(spare, user_remaining)
        user_limit += add_user
        spare -= add_user
    if spare:
        assistant_limit += min(spare, len(assistant) - assistant_limit)

    fitted_user, user_truncated = _truncate_middle(user, user_limit)
    fitted_assistant, assistant_truncated = _truncate_middle(assistant, assistant_limit)
    turn = {"user": fitted_user, "assistant": fitted_assistant}
    truncated = {
        role
        for role, changed in (
            ("user", user_truncated),
            ("assistant", assistant_truncated),
        )
        if changed
    }
    return turn, truncated


def build_conversation_context(
    history: list[dict] | None,
    *,
    max_turns: int = 3,
    char_limit: int = 2400,
    message_char_limit: int = 1000,
) -> ConversationContext:
    """Build newest-first-selected, chronologically rendered short-term context.

    The result never exceeds ``char_limit``. Older turns are dropped before a
    newer turn, and each included user/assistant message is independently
    bounded. If the newest turn still cannot fit, both sides are shortened while
    preserving their beginnings and endings.
    """
    raw_history = list(history or [])
    max_turns = max(0, int(max_turns))
    char_limit = max(0, int(char_limit))
    message_char_limit = max(0, int(message_char_limit))

    eligible: list[tuple[dict[str, str], set[str], int, int]] = []
    for raw in raw_history[-max_turns:] if max_turns else []:
        if not isinstance(raw, dict):
            continue
        user_value = raw.get("user", "")
        assistant_value = raw.get("assistant", "")
        user = ("" if user_value is None else str(user_value)).strip()
        assistant, footer_stripped = _strip_generated_sources(
            "" if assistant_value is None else str(assistant_value)
        )
        assistant, marker_count = _strip_stale_citation_markers(assistant)
        if not user and not assistant:
            continue
        user, user_truncated = _truncate_middle(user, message_char_limit)
        assistant, assistant_truncated = _truncate_middle(
            assistant, message_char_limit
        )
        if not user and not assistant:
            continue
        truncated = {
            role
            for role, changed in (
                ("user", user_truncated),
                ("assistant", assistant_truncated),
            )
            if changed
        }
        eligible.append(
            (
                {"user": user, "assistant": assistant},
                truncated,
                int(footer_stripped),
                marker_count,
            )
        )

    selected_reversed: list[tuple[dict[str, str], set[str], int, int]] = []
    used_characters = 0
    for turn, truncated, stripped_footer, marker_count in reversed(eligible):
        block = _format_turn(turn)
        separator = 2 if selected_reversed else 0
        if used_characters + separator + len(block) <= char_limit:
            selected_reversed.append(
                (turn, truncated, stripped_footer, marker_count)
            )
            used_characters += separator + len(block)
            continue

        if not selected_reversed:
            fitted, additionally_truncated = _fit_single_turn(
                turn["user"], turn["assistant"], char_limit
            )
            if fitted is not None:
                selected_reversed.append(
                    (
                        fitted,
                        truncated | additionally_truncated,
                        stripped_footer,
                        marker_count,
                    )
                )
        # Keep a contiguous window of the newest turns. Skipping a newer long
        # turn to include a stale short one would make follow-up resolution less
        # predictable.
        break

    selected = list(reversed(selected_reversed))
    turns = [turn for turn, _, _, _ in selected]
    text = "\n\n".join(_format_turn(turn) for turn in turns)
    # Defensive invariant: formatting and accounting must agree exactly.
    if len(text) > char_limit:  # pragma: no cover - guarded by construction
        text = text[:char_limit]

    return ConversationContext(
        text=text,
        turns=turns,
        input_turn_count=len(raw_history),
        dropped_turn_count=max(0, len(raw_history) - len(turns)),
        truncated_message_count=sum(
            len(changed) for _, changed, _, _ in selected
        ),
        stripped_source_footer_count=sum(value for _, _, value, _ in selected),
        stripped_citation_marker_count=sum(value for _, _, _, value in selected),
        max_turns=max_turns,
        char_limit=char_limit,
        message_char_limit=message_char_limit,
    )
