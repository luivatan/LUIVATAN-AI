"""Phase 47: select which confirmed long-term memories are relevant to one question.

Deterministic and local — no embedding call, no network, matching the rest of the
long-term-memory module's dependency-free design (see ``long_term.py``'s own
docstring: "intentionally disconnected from model prompts" up to this phase).

Two memory kinds get different treatment, matching what each one actually means:

- ``preference`` describes HOW to answer (tone, format, standing instructions like
  "keep answers concise"). That applies to every question, not just topically similar
  ones, so preferences are always included, bounded to a small recency-capped count.
- ``ongoing_context`` describes WHAT the user is currently doing (a project, a task).
  That's only useful when the current question is actually about it, so context items
  are filtered by keyword overlap with the question — an unrelated question shouldn't
  see stale context about a different task.
"""

from __future__ import annotations

import re

from apex_ai.memory.long_term import LongTermMemory

_WORD = re.compile(r"[a-z0-9]+")

# Not an exhaustive stopword list (that already exists, larger, in rag/engine.py for
# lexical evidence scoring) — this one only needs to keep short, common words from
# dominating a memory/question keyword-overlap comparison.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do", "does",
        "for", "from", "how", "i", "in", "is", "it", "of", "on", "or", "that", "the",
        "this", "to", "was", "were", "what", "when", "where", "which", "who", "why",
        "with", "would", "you", "your", "my", "me",
    }
)


def _keywords(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) > 2 and word not in _STOPWORDS}


def select_relevant_memories(
    question: str,
    memories: list[LongTermMemory],
    *,
    max_preferences: int = 5,
    max_context: int = 3,
    min_overlap: int = 1,
) -> list[LongTermMemory]:
    """``memories`` is expected pre-sorted newest-updated-first (as
    ``LongTermMemoryStore.list()`` already returns), so truncation keeps the most
    recently confirmed/updated items on both branches."""
    preferences = [memory for memory in memories if memory.kind == "preference"][:max_preferences]

    question_words = _keywords(question)
    scored_context: list[tuple[int, LongTermMemory]] = []
    if question_words:
        for memory in memories:
            if memory.kind != "ongoing_context":
                continue
            overlap = len(question_words & _keywords(memory.content))
            if overlap >= min_overlap:
                scored_context.append((overlap, memory))
    scored_context.sort(key=lambda pair: pair[0], reverse=True)
    relevant_context = [memory for _, memory in scored_context[:max_context]]

    return preferences + relevant_context


def format_memory_text(memories: list[LongTermMemory]) -> str:
    if not memories:
        return ""
    return "\n".join(f"- {memory.content}" for memory in memories)


def find_similar_memory(
    content: str,
    existing_memories: list[LongTermMemory],
    *,
    min_overlap_ratio: float = 0.5,
) -> LongTermMemory | None:
    """Phase 49: the most keyword-similar existing memory, if any is similar
    enough to plausibly be about the same thing without being a duplicate.

    Exact (casefold/whitespace-normalized) duplicates are already deduplicated
    earlier, at proposal time (``LongTermMemoryStore.propose_candidate``), so a
    match found here is genuinely a *different* statement that shares most of
    its meaningful words with an existing one — e.g. "prefers concise answers"
    vs. "prefers detailed answers" — which is exactly the "may be outdated or
    conflicting" case this phase asks to detect. This never deletes or replaces
    anything by itself; it only surfaces the match for the confirmation UI so a
    human decides, per the roadmap's "handle safely" wording.
    """
    candidate_words = _keywords(content)
    if not candidate_words:
        return None
    best: LongTermMemory | None = None
    best_ratio = 0.0
    for memory in existing_memories:
        memory_words = _keywords(memory.content)
        if not memory_words:
            continue
        overlap = len(candidate_words & memory_words)
        ratio = overlap / min(len(candidate_words), len(memory_words))
        if ratio >= min_overlap_ratio and ratio > best_ratio:
            best_ratio = ratio
            best = memory
    return best


__all__ = ["find_similar_memory", "format_memory_text", "select_relevant_memories"]
