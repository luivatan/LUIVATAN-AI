"""Backward-compatible entry point.

The historical command `python3 ingest.py` launched the app; it still does.
For command-line document ingestion use:

    python scripts/ingest_folder.py <folder>
"""

from apex_ai.core.logging import get_logger
from apex_ai.runtime import build_services
from apex_ai.ui import launch

log = get_logger("entry")


def main() -> None:
    # Build services up front so startup problems are logged *before* the
    # UI opens (the UI also shows them in the banner).
    services = build_services()
    if services.startup_error:
        log.warning("Startup problem detected:\n%s", services.startup_error)
    launch(services)


if __name__ == "__main__":
    main()
