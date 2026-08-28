"""Conservative retrieval-query processing.

The original question is always first and is always the question shown to the
answer model. Processing only adds retrieval variants when a question clearly
depends on recent conversation or contains distinct clauses. Deterministic
handling is enabled by default; optional LLM rewriting remains separately
gated because it adds latency and can alter exact terminology.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from apex_ai.core.logging import get_logger

log = get_logger("rag.query")

_PRONOUNS = {
    "it",
    "its",
    "this",
    "that",
    "they",
    "their",
    "them",
    "these",
    "those",
    "former",
    "latter",
}
_FOLLOWUP_PREFIX = re.compile(
    r"^(?:and |also |then )?(?:what|how|why|when|where|which)\s+(?:about|else)\b",
    re.IGNORECASE,
)
_CONTINUATION_PREFIX = re.compile(r"^(?:and|also|then)\b", re.IGNORECASE)
_ORDINAL_REFERENCE = re.compile(
    r"\b(?:first|second|third|last|next|other)\s+ones?\b", re.IGNORECASE
)
_MULTI_CONNECTOR = re.compile(
    r"\s+and\s+(?:also\s+)?(?=(?:what|how|why|when|where|which|list|compare|explain)\b)",
    re.IGNORECASE,
)
_PROTECTED = re.compile(
    r'"[^"]+"|\b(?:[A-Z]{2,}(?:[-_.:/][A-Za-z0-9]+)*|'
    r"[A-Za-z]*\d[A-Za-z0-9_.:/-]*|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b"
)


@dataclass
class QueryTrace:
    original: str
    follow_up: bool = False
    multi_part: bool = False
    protected_terms: list[str] = field(default_factory=list)
    strategies: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "follow_up": self.follow_up,
            "multi_part": self.multi_part,
            "protected_terms": list(self.protected_terms),
            "strategies": list(self.strategies),
            "errors": list(self.errors),
            "queries": list(self.queries),
        }


class QueryProcessor:
    def __init__(
        self,
        llm_provider=None,
        enabled: bool = False,
        max_subqueries: int = 3,
        *,
        decompose: bool = True,
        llm_rewrite: bool | None = None,
    ) -> None:
        self.llm = llm_provider
        self.enabled = enabled
        self.decompose = decompose
        self.max_subqueries = max(0, max_subqueries)
        # Backward compatibility: callers that explicitly supplied an LLM to
        # the old QueryProcessor still get LLM processing unless they opt out.
        self.llm_rewrite = bool(llm_provider) if llm_rewrite is None else llm_rewrite

    # -- triggers ---------------------------------------------------------

    @staticmethod
    def _needs_rewrite(question: str, history: list[dict]) -> bool:
        """True only for a short, clearly context-dependent follow-up."""
        if not history:
            return False
        words = re.findall(r"\b\w+\b", question.lower())
        if not words or len(words) > 14:
            return False
        stripped = question.strip()
        return (
            bool(_FOLLOWUP_PREFIX.search(stripped))
            or bool(_CONTINUATION_PREFIX.search(stripped))
            or bool(_ORDINAL_REFERENCE.search(stripped))
            or any(word in _PRONOUNS for word in words)
        )

    @staticmethod
    def _is_multi_part(question: str) -> bool:
        if question.count("?") >= 2:
            return True
        if _MULTI_CONNECTOR.search(question):
            return True
        # Semicolons commonly delimit independent requests. A lone comma or
        # ordinary "A and B" does not trigger decomposition.
        return ";" in question and bool(
            re.search(
                r"\b(what|how|why|when|where|which|compare|list|explain)\b",
                question,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _protected_terms(text: str) -> list[str]:
        return list(dict.fromkeys(match.group(0).strip('"') for match in _PROTECTED.finditer(text)))

    # -- deterministic paths --------------------------------------------

    @staticmethod
    def _latest_user_question(history: list[dict]) -> str:
        for turn in reversed(history):
            prior = str(turn.get("user", "")).strip()
            if prior:
                return prior
        return ""

    def _deterministic_followup(self, question: str, history: list[dict]) -> str | None:
        prior = self._latest_user_question(history)
        if not prior:
            return None
        # Concatenation is intentionally lossless: names, IDs, dates, numbers,
        # and abbreviations from both turns survive exactly as written.
        return f"{prior.rstrip(' ?')} — {question.strip()}"

    def _deterministic_decompose(self, question: str) -> list[str]:
        pieces: list[str] = []
        if question.count("?") >= 2:
            pieces.extend(part.strip() + "?" for part in question.split("?") if part.strip())
        elif ";" in question:
            pieces.extend(part.strip() for part in question.split(";") if part.strip())
        else:
            pieces.extend(part.strip() for part in _MULTI_CONNECTOR.split(question) if part.strip())

        cleaned: list[str] = []
        for piece in pieces:
            piece = re.sub(r"\s+", " ", piece).strip(" ,;")
            if piece:
                piece = piece[:1].upper() + piece[1:]
            if piece and piece != question and piece not in cleaned:
                cleaned.append(piece)
        return cleaned[: self.max_subqueries]

    # -- optional LLM paths ---------------------------------------------

    def _safe_llm_output(self, output: str, source: str) -> str | None:
        candidate = re.sub(r"\s+", " ", (output or "")).strip().strip('"')
        if not candidate or len(candidate) > 500:
            return None
        missing = [
            term for term in self._protected_terms(source) if term.casefold() not in candidate.casefold()
        ]
        if missing:
            log.warning("Rejected query rewrite that dropped protected term(s): %s", missing)
            return None
        return candidate

    def _rewrite(
        self,
        question: str,
        history: list[dict],
        trace: QueryTrace | None = None,
    ) -> str | None:
        """Return a terminology-preserving standalone query, or None."""
        if self.llm is None or not self.llm_rewrite:
            return None
        recent = "\n".join(
            f"User: {turn.get('user', '')}\nAssistant: {turn.get('assistant', '')[:300]}"
            for turn in history[-2:]
        )
        prompt = (
            "Rewrite the follow-up as ONE standalone document-search query. Preserve every "
            "name, quoted phrase, identifier, number, date, and abbreviation exactly. "
            "Reply with the query only.\n\n"
            f"Conversation:\n{recent}\n\nFollow-up question: {question}\nStandalone query:"
        )
        try:
            raw = self.llm.generate(prompt, max_tokens=96, temperature=0.0) or ""
            source = f"{self._latest_user_question(history)} {question}"
            candidate = self._safe_llm_output(raw, source)
            if candidate is None and trace is not None:
                trace.errors.append("llm_rewrite: output rejected by terminology/format validation")
            return candidate
        except Exception as error:
            if trace is not None:
                trace.errors.append(f"llm_rewrite: {type(error).__name__}: {error}")
            log.warning("Query rewrite failed; deterministic query remains available: %s", error)
            return None

    def _decompose(
        self, question: str, trace: QueryTrace | None = None
    ) -> list[str]:
        """Optional LLM decomposition, validated against exact terminology."""
        if self.llm is None or not self.llm_rewrite:
            return []
        prompt = (
            "Break the question into up to "
            f"{self.max_subqueries} short standalone document-search questions, one per line. "
            "Preserve names, identifiers, numbers, dates, and abbreviations exactly. "
            "No numbering or extra text.\n\n"
            f"Question: {question}\nSub-questions:"
        )
        try:
            raw = self.llm.generate(prompt, max_tokens=160, temperature=0.0) or ""
            lines = [line.strip("-•0123456789. ") for line in raw.splitlines()]
            valid = [self._safe_llm_output(line, "") for line in lines if line.strip()]
            subqueries = list(dict.fromkeys(line for line in valid if line))[
                : self.max_subqueries
            ]
            if not subqueries and trace is not None:
                trace.errors.append(
                    "llm_decomposition: no output passed terminology/format validation"
                )
            combined = " ".join(subqueries).casefold()
            missing = [
                term
                for term in self._protected_terms(question)
                if term.casefold() not in combined
            ]
            if missing:
                if trace is not None:
                    trace.errors.append(
                        "llm_decomposition: output rejected after protected terms were dropped"
                    )
                log.warning(
                    "Rejected query decomposition that dropped protected term(s): %s",
                    missing,
                )
                return []
            return subqueries
        except Exception as error:
            if trace is not None:
                trace.errors.append(f"llm_decomposition: {type(error).__name__}: {error}")
            log.warning("Query decomposition failed; deterministic queries remain available: %s", error)
            return []

    # -- public API ------------------------------------------------------

    def expand_with_trace(
        self, question: str, history: list[dict] | None = None
    ) -> tuple[list[str], QueryTrace]:
        history = history or []
        original = question.strip()
        trace = QueryTrace(original=original, protected_terms=self._protected_terms(original))
        queries = [original]
        if not self.enabled or not original or self.max_subqueries == 0:
            trace.queries = queries
            return queries, trace

        trace.multi_part = self.decompose and self._is_multi_part(original)
        trace.follow_up = self._needs_rewrite(original, history)

        additions: list[str] = []
        if trace.multi_part:
            deterministic = self._deterministic_decompose(original)
            if deterministic:
                additions.extend(deterministic)
                trace.strategies.append("deterministic_decomposition")
            llm_queries = self._decompose(original, trace)
            if llm_queries:
                additions.extend(llm_queries)
                trace.strategies.append("llm_decomposition")
        elif trace.follow_up:
            deterministic = self._deterministic_followup(original, history)
            if deterministic:
                additions.append(deterministic)
                trace.strategies.append("history_expansion")
            rewritten = self._rewrite(original, history, trace)
            if rewritten:
                additions.append(rewritten)
                trace.strategies.append("llm_rewrite")

        for query in additions:
            query = query.strip()
            if query and query not in queries:
                queries.append(query)
            if len(queries) >= self.max_subqueries + 1:
                break

        trace.queries = queries
        log.debug("Retrieval queries: %s", queries)
        return queries, trace

    def expand(self, question: str, history: list[dict] | None = None) -> list[str]:
        """Return retrieval variants, always with the unmodified original first."""
        return self.expand_with_trace(question, history)[0]
