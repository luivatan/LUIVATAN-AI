"""Evaluation runner used by ``evaluate_rag.py``.

Pipeline:
1. Load a JSONL dataset (question / expected_answer / expected_source /
   expected_page per line).
2. Ingest every document referenced by the dataset from the docs directory
   (idempotent — duplicates are skipped).
3. For each item: run the retrieval stages (and optionally the full answer
   generation with ``--with-llm``), score with :mod:`apex_ai.evaluation.metrics`.
4. Print a table, write a JSON report to ``eval/reports/``.

Usage examples::

    python evaluate_rag.py                          # retrieval-only, default dataset
    python evaluate_rag.py --dataset my.jsonl --docs mydocs/
    python evaluate_rag.py --with-llm               # also generates answers
    python evaluate_rag.py --embedding hashing      # no model download (smoke tests)
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from apex_ai.config.settings import load_settings, with_overrides
from apex_ai.core.errors import ApexError
from apex_ai.core.logging import get_logger, setup_logging
from apex_ai.evaluation.metrics import Report, evaluate_item

log = get_logger("eval")

EXAMPLE_DATASET = Path(__file__).resolve().parents[2] / "eval" / "dataset.example.jsonl"


def load_dataset(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            item = json.loads(line)
            if item.get("question"):
                items.append(item)
    return items


def run_evaluation(
    dataset_path: Path,
    docs_dir: Path,
    with_llm: bool = False,
    embedding: str | None = None,
    top_k: int | None = None,
) -> Report:
    settings = load_settings()
    if embedding:
        settings = with_overrides(settings, embedding_model=embedding)
    if top_k:
        settings = with_overrides(settings, top_k=top_k, rerank_top_k=min(4, top_k))
    setup_logging(settings.log_dir)

    embedding_factory = None
    if embedding == "hashing":
        from apex_ai.embeddings import HashingEmbeddingProvider

        embedding_factory = HashingEmbeddingProvider

    from apex_ai.runtime import build_services

    services = build_services(settings, embedding_factory=embedding_factory)
    if not services.ready:
        raise ApexError(
            what="Evaluation cannot start: the application failed to initialize.",
            why=services.startup_error or "unknown",
            fix="Fix the startup problem first (see the message above).",
        )

    items = load_dataset(dataset_path)
    if not items:
        raise ApexError(
            what=f"No evaluation items found in {dataset_path}.",
            fix="Add JSONL lines with question/expected_answer/expected_source/expected_page.",
        )

    _ensure_docs_ingested(services, docs_dir, items)

    report = Report()
    for item in items:
        answer = None
        insufficient = False
        context_text = ""
        retrieved = []

        turn = services.engine.prepare(item["question"], use_memory=False)
        retrieved = turn.candidates[: settings.top_k]
        context_text = turn.context.text if turn.context else ""

        if with_llm:
            result = services.engine.ask(item["question"], use_memory=False)
            answer = result.answer
            insufficient = result.insufficient_evidence

        report.items.append(
            evaluate_item(item, retrieved, context_text, answer=answer, insufficient=insufficient)
        )
    return report


def _ensure_docs_ingested(services, docs_dir: Path, items: list[dict]) -> None:
    """Ingest any referenced source file that is not indexed yet."""
    referenced = {item.get("expected_source", "") for item in items}
    referenced |= {item.get("source", "") for item in items}
    referenced.discard("")

    indexed_names = {d.name.lower() for d in services.ingestion.list_documents()}
    for name in sorted(referenced):
        candidates = list(docs_dir.glob(name)) or list(docs_dir.glob(f"**/{name}"))
        for candidate in candidates:
            if candidate.name.lower() not in indexed_names or _force(item_for(name, items)):
                from apex_ai.core.logging import timed

                with timed(log, f"ingesting {candidate.name}", level=logging.INFO):
                    services.ingestion.ingest_path(candidate, force=False)
                log.info("Ingested %s", candidate.name)


def _force(item: dict | None) -> bool:
    return bool(item and item.get("force_ingest"))


def item_for(source_name: str, items: list[dict]) -> dict | None:
    for item in items:
        if item.get("expected_source", "") == source_name or item.get("source", "") == source_name:
            return item
    return None


def save_report(report: Report, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"eval-{stamp}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **report.to_dict(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def print_report(report: Report, report_path: Path | None) -> None:
    summary = report.summary()
    print("\n=== RAG EVALUATION (heuristic metrics — raw numbers, no claims) ===")
    print(f"items:                  {summary['items']}")
    print(f"source_hit_rate:        {summary['source_hit_rate']:.2%}")
    print(f"page_hit_rate:          {summary['page_hit_rate']:.2%}")
    print(f"first_hit_rate:         {summary['first_hit_rate']:.2%}")
    print(f"mean_context_relevance: {summary['mean_context_relevance']:.3f}")
    print(f"insufficient_rate:      {summary['insufficient_rate']:.2%}")
    if summary["mean_groundedness_proxy"] is not None:
        print(f"mean_groundedness_proxy:{summary['mean_groundedness_proxy']:.3f}")
    if report_path:
        print(f"report saved to:        {report_path}")

    print("\nPer-item:")
    for item in report.items:
        page = f"p{item.expected_page}" if item.expected_page is not None else "p-"
        flags = " ".join([
            "SRC" if item.source_hit else "src!",
            page if item.page_hit else page + "!",
            "TOP" if item.first_hit else "top!",
        ])
        print(f"  [{flags}] rel={item.context_relevance:.2f}  {item.question[:60]}")
