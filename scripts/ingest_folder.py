"""Bulk-ingest a folder of documents from the command line.

    python scripts/ingest_folder.py path/to/folder [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from apex_ai.core.errors import ApexError  # noqa: E402
from apex_ai.documents.extraction import supported  # noqa: E402
from apex_ai.runtime import build_services  # noqa: E402


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

    files = [p for p in sorted(args.folder.rglob("*")) if p.is_file() and supported(p)]
    if not files:
        print(f"No supported files (PDF/TXT/MD/JSON) found in {args.folder}")
        return 0

    for path in files:
        try:
            result = services.ingestion.ingest_path(path, force=args.force)
            print(f"[{result.status}] {result.message}")
        except ApexError as error:
            print(f"[error] {path.name}:\n{error.user_message()}\n")

    stats = services.ingestion.stats()
    print(f"\nDone. Library now holds {stats['documents']} document(s), {stats['chunks']} chunk(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
