#!/usr/bin/env python3
"""Restore an Apex AI backup archive (Phase 92) into a new directory.

    python scripts/restore.py path/to/apex-backup-*.tar.gz path/to/restored

Never overwrites an existing directory - restoring always lands in a fresh
location so you can inspect it (or swap it in for the live data/ directory
yourself, after stopping the application) rather than silently clobbering
anything live.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from apex_ai.backup import restore_backup
from apex_ai.core.errors import ApexError


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore an Apex AI backup archive.")
    parser.add_argument("archive", type=Path, help="The backup .tar.gz file to restore.")
    parser.add_argument("target", type=Path, help="Directory to restore into (must not exist).")
    args = parser.parse_args()

    if not args.archive.is_file():
        print(f"Backup file not found: {args.archive}")
        return 1

    try:
        problems = restore_backup(args.archive, args.target)
    except ApexError as error:
        print(error.public_message())
        return 1

    if problems:
        print(f"RESTORE COMPLETED WITH PROBLEMS ({len(problems)}):")
        for problem in problems:
            print(f"  - {problem}")
        print(f"\nExtracted (but not fully verified) into: {args.target}")
        return 1

    print(f"Restored and verified into: {args.target}")
    print(
        "Every file matches its recorded checksum. Stop Apex AI, then point its "
        "APEX_*_PATH settings (or move this directory into place) to use this restore."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
