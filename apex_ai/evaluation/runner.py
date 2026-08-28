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
        if embedding == "hashing":
            # Hashing vectors intentionally have a different cosine scale and
            # no semantic meaning; this threshold is only for deterministic
            # smoke/failure-path runs and is recorded in report metadata.
            settings = with_overrides(settings, min_similarity=0.05)
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

    report = Report(
        metadata={
            "dataset": str(dataset_path),
            "embedding_provider": services.embeddings.name,
            "with_llm": with_llm,
            "top_k": settings.top_k,
            "semantic_candidate_k": settings.semantic_candidate_k,
            "keyword_candidate_k": settings.keyword_candidate_k,
            "min_similarity": settings.min_similarity,
            "reranker": services.reranker.name,
            "limitations": [
                "Source/page hits are exact metadata checks, not answer correctness.",
                "Source precision and reranker deltas use expected-document rank, not passage-level human relevance.",
                "Context relevance and groundedness are lexical-overlap proxies, not factuality judgments.",
                "Latency is local wall-clock time and is hardware/corpus/cache dependent.",
                "Hashing embeddings are deterministic smoke-test tools, not production semantic models.",
            ],
        }
    )
    for item in items:
        answer = None
        citations = None
        history = item.get("history") or []
        turn = services.engine.prepare(
            item["question"],
            use_memory=False,
            history_override=history,
        )
        retrieved = turn.candidates[: settings.top_k]
        context_text = turn.context.text if turn.context else ""
        context_ids = [chunk.chunk_id for chunk in (turn.context.used_chunks if turn.context else [])]
        insufficient = not turn.supported
        timings = dict(turn.timings)

        if with_llm:
            result = services.engine.ask(
                item["question"],
                use_memory=False,
                history_override=history,
            )
            answer = result.answer
            citations = result.citations
            context_ids = result.context_chunk_ids
            context_text = result.context_text
            insufficient = result.insufficient_evidence
            timings = {
                key: float(value)
                for key, value in result.timings.items()
                if key != "total_s"
            }

        report.items.append(
            evaluate_item(
                item,
                retrieved,
                context_text,
                answer=answer,
                insufficient=insufficient,
                reranked_chunks=turn.reranked_candidates,
                citations=citations,
                context_chunk_ids=context_ids,
                timings_ms=timings,
            )
        )
    return report


def _ensure_docs_ingested(services, docs_dir: Path, items: list[dict]) -> None:
    """Ingest any referenced source file that is not indexed yet."""
    referenced = {item.get("expected_source", "") for item in items}
    referenced |= {item.get("source", "") for item in items}
    for item in items:
        expected_sources = item.get("expected_sources", [])
        if isinstance(expected_sources, str):
            expected_sources = [expected_sources]
        referenced.update(expected_sources)
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
        expected_sources = item.get("expected_sources", [])
        if isinstance(expected_sources, str):
            expected_sources = [expected_sources]
        if (
            item.get("expected_source", "") == source_name
            or item.get("source", "") == source_name
            or source_name in expected_sources
        ):
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

    def percent(value) -> str:
        return "n/a" if value is None else f"{value:.2%}"

    def decimal(value) -> str:
        return "n/a" if value is None else f"{value:.3f}"

    print("\n=== RAG EVALUATION (measured values; proxies labeled, no quality claims) ===")
    print(f"items:                    {summary['items']}")
    print(f"source_hit_rate:          {percent(summary['source_hit_rate'])}")
    print(f"mean_source_recall:       {decimal(summary['mean_source_recall'])}")
    print(
        f"mean_source_precision@k*: {decimal(summary['mean_source_precision_at_k'])}"
    )
    print(f"page_hit_rate:            {percent(summary['page_hit_rate'])}")
    print(f"mean_page_recall:         {decimal(summary['mean_page_recall'])}")
    print(f"first_hit_rate:           {percent(summary['first_hit_rate'])}")
    print(f"mean_reciprocal_rank:     {decimal(summary['mean_reciprocal_rank'])}")
    print(
        "reranked_MRR / delta:      "
        f"{decimal(summary['reranked_mean_reciprocal_rank'])} / "
        f"{decimal(summary['mean_reranker_rr_delta'])}"
    )
    print(f"mean_context_relevance*:  {decimal(summary['mean_context_relevance'])}")
    print(f"insufficient_rate:        {percent(summary['insufficient_rate'])}")
    print(f"refusal_accuracy:         {percent(summary['refusal_accuracy'])}")
    if summary["mean_groundedness_proxy"] is not None:
        print(f"mean_groundedness_proxy*: {summary['mean_groundedness_proxy']:.3f}")
    if summary["citation_integrity"] is not None:
        print(f"citation_integrity:       {summary['citation_integrity']:.3f}")
    if summary["citation_source_recall"] is not None:
        print(f"citation_source_recall:   {summary['citation_source_recall']:.3f}")
    if summary["citation_marker_validity"] is not None:
        print(f"citation_marker_validity: {summary['citation_marker_validity']:.3f}")
    if summary["mean_latency_ms"]:
        latency = ", ".join(
            f"{name}={value:.2f}" for name, value in summary["mean_latency_ms"].items()
        )
        print(f"mean_latency_ms:          {latency}")
    print(
        "* source precision uses document labels, not passage judgments; context/"
        "groundedness use lexical overlap, not factuality judgments"
    )
    if report_path:
        print(f"report saved to:          {report_path}")

    print("\nPer-item:")
    for item in report.items:
        if item.expected_pages:
            page = "p" + ",".join(str(value) for value in item.expected_pages)
        else:
            page = "p-"
        source_flag = "SRC" if item.source_hit is not False else "src!"
        page_flag = page if item.page_hit is not False else page + "!"
        top_flag = "TOP" if item.first_hit is not False else "top!"
        refusal = ""
        if item.refusal_correct is not None:
            refusal = " GATE✓" if item.refusal_correct else " gate!"
        flags = f"{source_flag} {page_flag} {top_flag}"
        print(
            f"  [{item.category} | {flags}{refusal}] "
            f"rel*={item.context_relevance:.2f}  {item.question[:60]}"
        )
