"""Backward-compatible Apex AI entry point.

Historically ``python ingest.py`` opened the interface. It now opens the same
chat-first web application as ``python ui.py``. For batch document ingestion use
``python scripts/ingest_folder.py <folder>``.
"""

from apex_ai.core.logging import get_logger
from apex_ai.runtime import build_services
from apex_ai.web import launch

log = get_logger("entry")


def main() -> None:
    services = build_services()
    if services.startup_error:
        log.warning("Startup problem detected:\n%s", services.startup_error)
    launch(services)


if __name__ == "__main__":
    main()
