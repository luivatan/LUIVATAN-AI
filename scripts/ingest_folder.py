#!/usr/bin/env python3
"""Bulk-ingest a folder of documents from the command line.

    python scripts/ingest_folder.py path/to/folder [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from apex_ai.core.errors import UNEXPECTED_ERROR_MESSAGE, ApexError
from apex_ai.core.logging import get_logger
from apex_ai.documents.extraction import supported
from apex_ai.runtime import build_services

log = get_logger("ingest.cli")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest every supported file in a folder.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--force", action="store_true", help="re-index even if already indexed")
    args = parser.parse_args()

    if not args.folder.is_dir():
        print(f"Not a directory: {args.folder}")
        return 1

    services = build_services()
    if not services.ready:
        print(services.startup_error)
        return 1

    try:
        files = [p for p in sorted(args.folder.rglob("*")) if p.is_file() and supported(p)]
    except Exception:
        log.exception("Could not scan the requested ingestion folder")
        print(UNEXPECTED_ERROR_MESSAGE)
        return 1
    if not files:
        print(f"No supported files (PDF/TXT/MD/JSON) found in {args.folder}")
        return 0

    for path in files:
        try:
            result = services.ingestion.ingest_path(path, force=args.force)
            print(f"[{result.status}] {result.message}")
        except ApexError as error:
            print(f"[error] {path.name}:\n{error.public_message()}\n")
        except Exception:
            log.exception("Unexpected batch-ingest failure for one document")
            print(f"[error] {path.name}:\n{UNEXPECTED_ERROR_MESSAGE}\n")

    try:
        stats = services.ingestion.stats()
        print(
            f"\nDone. Library now holds {stats['documents']} document(s), "
            f"{stats['chunks']} chunk(s)."
        )
    except ApexError as error:
        print(error.public_message())
        return 1
    except Exception:
        log.exception("Could not read final ingestion statistics")
        print(UNEXPECTED_ERROR_MESSAGE)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
