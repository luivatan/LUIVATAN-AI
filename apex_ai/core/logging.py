"""Structured logging for Apex AI.

- Console: concise INFO level.
- File: ``logs/apex.log`` (rotating, 3 x 2 MB) at DEBUG level for diagnosis.
- Third-party libraries (chromadb, urllib3, ...) are quieted to WARNING.

Never log document text or secrets at INFO; full text only at DEBUG and only
in short truncated form (see ``preview``).
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def preview(text: str, limit: int = 120) -> str:
    """Short single-line preview of a longer text, safe for log files."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    """Idempotently configure the ``apex`` logger tree."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_dir / "apex.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    root = logging.getLogger("apex")
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)
    root.propagate = False

    for noisy in ("chromadb", "urllib3", "httpx", "httpcore", "sentence_transformers",
                  "gradio", "fontTools", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Namespaced child logger, e.g. ``get_logger('ingest')`` -> ``apex.ingest``."""
    if not name.startswith("apex"):
        name = f"apex.{name}"
    return logging.getLogger(name)


@contextmanager
def timed(logger: logging.Logger, label: str, level: int = logging.DEBUG):
    """Log how long a block took.

    Usage:
        with timed(log, "embedding 120 chunks"):
            ...
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        logger.log(level, "%s took %.2fs", label, elapsed)
