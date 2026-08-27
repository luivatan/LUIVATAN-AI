"""Optional query processing (rewriting / decomposition).

Design rules:
- OFF by default. Most questions don't need rewriting and every extra LLM
  call costs latency.
- The **original question is always kept** and always passed to the final
  answer generator — rewrites only produce *additional* retrieval queries.
- Conservative triggers: a short follow-up containing pronouns ("what about
  it?") is a rewrite candidate because it is unanswerable without context; a
  question with multiple '?' marks or explicit ' and ' lists is a
  decomposition candidate.
"""

from __future__ import annotations

import re

from apex_ai.core.logging import get_logger

log = get_logger("rag.query")

_PRONOUNS = {"it", "this", "that", "they", "them", "these", "those", "he", "she"}


class QueryProcessor:
    def __init__(self, llm_provider=None, enabled: bool = False, max_subqueries: int = 3) -> None:
        self.llm = llm_provider
        self.enabled = enabled
        self.max_subqueries = max_subqueries

    # -- triggers -------------------------------------------------------------

    @staticmethod
    def _needs_rewrite(question: str, history: list[dict]) -> bool:
        """A short pronoun-heavy follow-up depends on earlier turns."""
        words = question.lower().split()
        if len(words) > 8:
            return False
        return any(word.strip(".,!?") in _PRONOUNS for word in words) and bool(history)

    @staticmethod
    def _is_multi_part(question: str) -> bool:
        if question.count("?") >= 2:
            return True
        return bool(re.search(r"\band (also )?(what|how|why|when|which|list|compare)", question.lower()))

    # -- LLM prompts -----------------------------------------------------------

    def _rewrite(self, question: str, history: list[dict]) -> str | None:
        """Return a standalone version of the question, or None on failure."""
        if self.llm is None:
            return None
        recent = "\n".join(
            f"User: {turn.get('user', '')}\nAssistant: {turn.get('assistant', '')[:300]}"
            for turn in history[-2:]
        )
        prompt = (
            "Rewrite the follow-up question as ONE standalone search query that does not "
            "depend on the conversation. Reply with the query only, nothing else.\n\n"
            f"Conversation:\n{recent}\n\nFollow-up question: {question}\nStandalone query:"
        )
        try:
            rewritten = (self.llm.generate(prompt, max_tokens=64, temperature=0.0) or "").strip()
            return rewritten or None
        except Exception as error:
            log.warning("Query rewrite failed (continuing with original): %s", error)
            return None

    def _decompose(self, question: str) -> list[str]:
        """Split a multi-part question into sub-questions (LLM, best effort)."""
        if self.llm is None:
            return [question]
        prompt = (
            "Break the question into up to "
            f"{self.max_subqueries} short standalone search questions, one per line, "
            "no numbering, no extra text.\n\n"
            f"Question: {question}\nSub-questions:"
        )
        try:
            raw = (self.llm.generate(prompt, max_tokens=120, temperature=0.0) or "").strip()
            lines = [line.strip("-•0123456789. ") for line in raw.split("\n")]
            subqueries = [line for line in lines if line][: self.max_subqueries]
            return subqueries or [question]
        except Exception as error:
            log.warning("Query decomposition failed (continuing with original): %s", error)
            return [question]

    # -- public API --------------------------------------------------------------

    def expand(self, question: str, history: list[dict] | None = None) -> list[str]:
        """Return the list of retrieval queries.

        The original question is ALWAYS first and never removed, so a failed
        rewrite/decomposition can only add value, never lose the user's
        actual question.
        """
        history = history or []
        queries = [question]
        if not self.enabled or self.llm is None:
            return queries

        if self._is_multi_part(question):
            queries.extend(q for q in self._decompose(question) if q and q not in queries)
        elif self._needs_rewrite(question, history):
            rewritten = self._rewrite(question, history)
            if rewritten:
                queries.insert(1, rewritten)

        log.debug("Retrieval queries: %s", queries)
        return queries
