#!/usr/bin/env python3
"""Measure real retrieval latency (Phase 95).

    python scripts/benchmark_retrieval.py [--chunks 2000] [--queries 50]

Uses HashingEmbeddingProvider (deterministic, no network/model download) so
this runs anywhere, including this sandbox - it measures the retrieval
*pipeline's own* overhead (Chroma round-trips, BM25 scoring, RRF fusion),
which is independent of which embedding model is configured. Real
end-to-end latency with a real embedding/LLM model is additionally
recorded in a real deployment's request logs (timings_ms on every
response) - this script isolates the part that can be measured and
optimized from inside the codebase itself.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from apex_ai.config.settings import Settings
from apex_ai.documents.models import Chunk
from apex_ai.embeddings.hashing import HashingEmbeddingProvider
from apex_ai.retrieval.keyword import BM25Index
from apex_ai.retrieval.pipeline import HybridRetriever
from apex_ai.vectordb.chroma_store import ChromaVectorStore

USER_ID = "bench-user"

WORDS = [
    "apex", "retrieval", "hybrid", "vector", "keyword", "rerank", "chunk",
    "document", "policy", "warranty", "invoice", "contract", "manual",
    "procedure", "safety", "compliance", "audit", "budget", "schedule",
    "maintenance", "inspection", "report", "incident", "training",
]


def _settings(root: Path) -> Settings:
    return Settings(
        database_path=root / "chroma",
        upload_dir=root / "uploads",
        model_dir=root / "models",
        model_path="",
        log_dir=root / "logs",
        cache_dir=root / "cache",
        memory_path=root / "memory.json",
        conversation_db_path=root / "conversations.db",
        long_term_memory_db_path=root / "long_term_memory.db",
        users_db_path=root / "users.db",
        collections_db_path=root / "collections.db",
        projects_db_path=root / "projects.db",
        billing_db_path=root / "billing.db",
        embedding_model="hashing-256-v1",
        top_k=6,
        rerank_top_k=3,
        min_similarity=0.0,
        reranker_mode="lexical",
        context_char_limit=4000,
        rate_limit_enabled=False,
    )


def _seed(store: ChromaVectorStore, n_chunks: int) -> None:
    batch: list[Chunk] = []
    for i in range(n_chunks):
        word_a = WORDS[i % len(WORDS)]
        word_b = WORDS[(i * 7 + 3) % len(WORDS)]
        text = (
            f"Section {i}: this passage discusses {word_a} and {word_b} in "
            f"the context of document {i // 20}, covering procedure {i % 13} "
            f"and reference code REF-{i:05d}."
        )
        batch.append(
            Chunk(
                chunk_id=f"chunk-{i}",
                text=text,
                document_id=f"doc-{i // 20}",
                metadata={
                    "user_id": USER_ID,
                    "document_id": f"doc-{i // 20}",
                    "section": f"Section {i}",
                },
            )
        )
        if len(batch) >= 200:
            store.upsert_chunks(batch)
            batch = []
    if batch:
        store.upsert_chunks(batch)


def _time_calls(fn, n: int) -> list[float]:
    samples = []
    for _ in range(n):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def _report(label: str, samples: list[float]) -> None:
    print(
        f"{label}: mean={statistics.mean(samples):.3f}ms "
        f"median={statistics.median(samples):.3f}ms "
        f"p95={sorted(samples)[int(len(samples) * 0.95) - 1]:.3f}ms "
        f"(n={len(samples)})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Apex AI retrieval latency.")
    parser.add_argument("--chunks", type=int, default=2000)
    parser.add_argument("--queries", type=int, default=50)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="apex-bench-") as tmp:
        root = Path(tmp)
        settings = _settings(root)
        embeddings = HashingEmbeddingProvider()
        store = ChromaVectorStore(settings, embeddings, collection_name="bench")

        print(f"Seeding {args.chunks} chunks...")
        seed_started = time.perf_counter()
        _seed(store, args.chunks)
        print(f"Seed complete in {time.perf_counter() - seed_started:.2f}s")

        queries = [
            f"What does {WORDS[i % len(WORDS)]} say about {WORDS[(i * 3) % len(WORDS)]}?"
            for i in range(args.queries)
        ]

        print(f"\n--- store.search() in isolation, {args.chunks} chunks ---")
        idx = {"i": 0}

        def _one_search():
            q = queries[idx["i"] % len(queries)]
            idx["i"] += 1
            store.search(q, USER_ID, k=10)

        _report("store.search()", _time_calls(_one_search, args.queries))

        print(f"\n--- full hybrid retrieval pipeline, {args.chunks} chunks ---")
        retriever = HybridRetriever(store, settings, keyword_index=BM25Index(store))
        idx["i"] = 0

        def _one_retrieve():
            q = queries[idx["i"] % len(queries)]
            idx["i"] += 1
            retriever.retrieve([q], USER_ID, top_k=10)

        _report("HybridRetriever.retrieve()", _time_calls(_one_retrieve, args.queries))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
