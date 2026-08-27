"""Grounded answer orchestration and lightweight RAG evaluation utilities."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Callable
from apex_retrieval import Result, optimize_context

@dataclass
class Citation:
    number: int
    source: str
    page: int | str
    text: str
    def label(self): return f"[{self.number}] {self.source}, page {self.page}"

class AnswerError(RuntimeError): pass

def rewrite_query(query: str, history: list[dict[str, str]] | None = None) -> str:
    """Resolve common follow-up pronouns without inventing domain facts."""
    query = " ".join(query.split())
    if not history or not re.search(r"\b(it|they|that|this|those|these)\b", query, re.I): return query
    previous = history[-1].get("user", "")
    return f"Regarding the previous question ({previous}), {query}"

def decompose_question(query: str) -> list[str]:
    parts = re.split(r"\s+(?:and|then|also)\s+|[?;]+", query, flags=re.I)
    return [part.strip() + ("?" if not part.strip().endswith("?") else "") for part in parts if part.strip()]

def build_context(results: list[Result], max_chars=6000) -> tuple[str, list[Citation]]:
    selected = results[:]
    context = optimize_context(selected, max_chars)
    citations = [Citation(i, r.metadata.get("source", "unknown source"), r.metadata.get("page", "?"), r.text) for i, r in enumerate(selected, 1) if f"[{i}]" in context]
    return context, citations

def evidence_for_answer(answer: str, citations: list[Citation]) -> list[Citation]:
    """Return only citations explicitly referenced by a model response."""
    numbers = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
    return [citation for citation in citations if citation.number in numbers]

def grounded_prompt(question: str, context: str) -> str:
    return ("You are a careful document assistant. Answer ONLY from the evidence below. "
            "If evidence is insufficient, say so plainly. Do not guess. Cite every factual claim using [number].\n\n"
            f"Evidence:\n{context}\n\nQuestion: {question}\nAnswer:")

class AnswerEngine:
    def __init__(self, generate: Callable[[str], str]): self.generate = generate
    def answer(self, question: str, results: list[Result]) -> tuple[str, list[Citation]]:
        context, citations = build_context(results)
        if not context: raise AnswerError("No supporting evidence was found.")
        try: response = self.generate(grounded_prompt(question, context)).strip()
        except Exception as exc: raise AnswerError("The answer service is temporarily unavailable.") from exc
        if not response: raise AnswerError("The answer service returned no content.")
        return response, evidence_for_answer(response, citations)

def evaluate_answer(answer: str, citations: list[Citation]) -> dict[str, float]:
    referenced = evidence_for_answer(answer, citations)
    claims = [part for part in re.split(r"[.!?]", answer) if part.strip()]
    citation_coverage = len(referenced) / max(1, len(claims))
    return {"citation_coverage": min(1.0, citation_coverage), "has_evidence": float(bool(referenced)), "answer_length": float(len(answer))}

def source_viewer(citation: Citation | None) -> str:
    if not citation: return "No source selected."
    return f"{citation.label()}\n\n{citation.text}"
