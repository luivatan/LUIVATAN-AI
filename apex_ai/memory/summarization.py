"""Phase 50: summarize conversation turns that have fallen out of the live
short-term context window, instead of letting them silently disappear.

This module decides WHAT needs summarizing and builds the LLM prompt for it.
It does not call an LLM itself and does not touch storage — the same
separation Phase 30's optional query rewriting already uses: pure decision
logic here, the actual provider call and persistence live in the caller
(``apex_ai/api/chat.py``, the same controller that already owns persisting
messages to ``conversations.db``).
"""

from __future__ import annotations

from dataclasses import dataclass

from apex_ai.memory.conversations import Message

SUMMARY_SYSTEM_PROMPT = (
    "Summarize the following conversation excerpt in under 200 words. Preserve "
    "concrete decisions that were made, facts that were established, and any "
    "questions that were left unresolved. Do not add information that was not "
    "stated. If there is nothing substantive, say so briefly."
)


@dataclass(frozen=True)
class PendingSummaryInput:
    """What needs to be folded into the rolling summary right now."""

    turns_text: str
    through_message_count: int  # new summarized_message_count once this succeeds


def turns_needing_summary(
    messages: list[Message],
    *,
    already_summarized_count: int,
    keep_live_messages: int,
) -> PendingSummaryInput | None:
    """``messages`` is one conversation's full message list, oldest first
    (``ConversationStore.messages()``'s existing order). Anything older than
    the newest ``keep_live_messages`` — the messages still directly visible via
    the normal short-term turn window — that hasn't already been folded into
    the summary needs summarizing now. Returns ``None`` when nothing new has
    fallen out of the live window since the last summary.
    """
    total = len(messages)
    boundary = max(0, total - max(0, int(keep_live_messages)))
    already_summarized_count = max(0, int(already_summarized_count))
    if boundary <= already_summarized_count:
        return None
    to_summarize = messages[already_summarized_count:boundary]
    if not to_summarize:
        return None
    lines = [
        f"{'User' if message.role == 'user' else 'Assistant'}: {message.content}"
        for message in to_summarize
        if message.content.strip()
    ]
    if not lines:
        # Nothing substantive (e.g. only empty/failed messages) fell out of the
        # window; still advance the marker so this range isn't rechecked forever.
        return PendingSummaryInput(turns_text="", through_message_count=boundary)
    return PendingSummaryInput(turns_text="\n".join(lines), through_message_count=boundary)


def build_summary_messages(previous_summary: str, new_turns_text: str) -> list[dict]:
    parts = []
    if previous_summary:
        parts.append(f"Existing summary of earlier conversation:\n{previous_summary}\n")
    parts.append(f"New conversation excerpt to fold in:\n{new_turns_text}")
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]


__all__ = [
    "PendingSummaryInput",
    "build_summary_messages",
    "turns_needing_summary",
]
