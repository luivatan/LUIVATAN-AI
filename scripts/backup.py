#!/usr/bin/env python3
"""Back up every persistent store Apex AI writes to (Phase 92).

    python scripts/backup.py [--output-dir data/backups] [--verify]

Only reads configuration (``load_settings()``) - it never loads the
embedding model or LLM, so a backup can run even if the model isn't set up.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from apex_ai.backup import create_backup, verify_backup
from apex_ai.config.settings import load_settings
from apex_ai.core.errors import ApexError


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up every Apex AI persistent store.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write the backup archive into (default: data/backups).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Immediately extract the new backup into a scratch location and check "
             "every file's checksum before reporting success.",
    )
    args = parser.parse_args()

    settings = load_settings()
    output_dir = args.output_dir or (settings.database_path.parent / "backups")

    try:
        result = create_backup(settings, output_dir)
    except ApexError as error:
        print(error.public_message())
        return 1

    size_mb = result.total_bytes / (1024 * 1024)
    print(
        f"Backup created: {result.archive_path}\n"
        f"  {result.file_count} file(s), {size_mb:.2f} MB, created {result.created_at}"
    )

    if args.verify:
        problems = verify_backup(result.archive_path)
        if problems:
            print(f"\nVERIFICATION FAILED ({len(problems)} problem(s)):")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("Verified: every file matches its recorded checksum.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
