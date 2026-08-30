#!/usr/bin/env python3
"""Seed a repeatable demo: real documents, in a real collection, on the
real default account (Phase 97).

    python scripts/seed_demo.py

Ingests the same small, synthetic document set the evaluation harness
already uses (``eval/docs/``) into a "Demo: Apex Research" collection, so
``docs/DEMO_SCRIPT.md``'s walkthrough produces the same result every time
it's run. Uses the real, configured embedding model and LLM
(``build_services()``) rather than a stand-in - what a viewer sees during
the demo is the actual retrieval and generation pipeline, not a mock.

Idempotent: re-running reuses the existing "Demo: Apex Research"
collection and skips documents already ingested (Apex's own duplicate
detection), so this is safe to run again before every demo.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from apex_ai.core.errors import ApexError
from apex_ai.runtime import build_services

DEMO_COLLECTION_NAME = "Demo: Apex Research"
DEMO_DOCS_DIR = PROJECT_ROOT / "eval" / "docs"
# burn_care.md is deliberately excluded: docs/DEMO_SCRIPT.md's walkthrough
# uploads it live, so the demo shows a real "upload -> ask -> grounded
# answer -> sources" cycle rather than only pre-loaded documents.
DEMO_DOC_NAMES = [
    "sample_first_aid.pdf",
    "apex_operations.md",
    "apex_finance.md",
]


def main() -> int:
    services = build_services()
    if services.startup_error:
        print(f"Apex AI did not start cleanly: {services.startup_error}")
        return 1
    if services.default_local_user is None:
        print("No default local account is available to own the demo documents.")
        return 1
    user_id = services.default_local_user.id

    existing = {c.name: c for c in services.collections.list(user_id)}
    collection = existing.get(DEMO_COLLECTION_NAME) or services.collections.create(
        user_id, DEMO_COLLECTION_NAME
    )
    print(f"Demo collection: {collection.name} ({collection.id})")

    failures = 0
    for name in DEMO_DOC_NAMES:
        path = DEMO_DOCS_DIR / name
        if not path.is_file():
            print(f"  SKIP      {name}: not found at {path}")
            failures += 1
            continue
        try:
            result = services.ingestion.ingest_path(path, user_id, collection_id=collection.id)
        except ApexError as error:
            print(f"  FAILED    {name}: {error.public_message()}")
            failures += 1
            continue
        print(f"  {result.status.upper():9s} {name} ({result.chunks} chunks)")

    if failures:
        print(f"\n{failures} document(s) could not be seeded.")
        return 1

    print(
        f'\nDemo ready. See docs/DEMO_SCRIPT.md for the walkthrough - scope a '
        f'conversation to the "{DEMO_COLLECTION_NAME}" collection and ask the '
        f"sample questions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
