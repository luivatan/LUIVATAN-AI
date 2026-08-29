"""RAG evaluation entry point.

    python evaluate_rag.py                        # retrieval metrics on the example dataset
    python evaluate_rag.py --with-llm             # also generate + score answers
    python evaluate_rag.py --embedding hashing    # fully offline smoke run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from apex_ai.core.errors import UNEXPECTED_ERROR_MESSAGE, ApexError
from apex_ai.core.logging import get_logger
from apex_ai.evaluation.runner import (
    EXAMPLE_DATASET,
    print_report,
    run_evaluation,
    save_report,
)

log = get_logger("evaluation.cli")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Apex AI retrieval and answers.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=EXAMPLE_DATASET,
        help="JSONL dataset with question/expected_answer/expected_source/expected_page",
    )
    parser.add_argument(
        "--docs",
        type=Path,
        default=EXAMPLE_DATASET.parent / "docs",
        help="Directory containing the source documents referenced by the dataset",
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Also generate answers with the configured LLM and score groundedness",
    )
    parser.add_argument(
        "--embedding",
        type=str,
        default=None,
        help="Override the embedding model (e.g. 'hashing' for an offline smoke run)",
    )
    parser.add_argument("--top-k", type=int, default=None, help="Candidate pool size")
    args = parser.parse_args()

    try:
        report = run_evaluation(
            dataset_path=args.dataset,
            docs_dir=args.docs,
            with_llm=args.with_llm,
            embedding=args.embedding,
            top_k=args.top_k,
        )
        report_path = save_report(report, PROJECT_ROOT / "eval" / "reports")
        print_report(report, report_path)
    except ApexError as error:
        print(error.public_message())
        return 1
    except Exception:
        log.exception("Evaluation failed")
        print(UNEXPECTED_ERROR_MESSAGE)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
