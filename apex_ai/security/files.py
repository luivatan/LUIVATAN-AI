"""Filesystem safety helpers.

Small but important: these functions prevent path traversal from untrusted
filenames (an uploaded file called ``../../something`` must never escape the
uploads directory) and provide content hashing used for duplicate detection.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import Path

from apex_ai.core.errors import SecurityError

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ ()\-\[\]]")
_MULTI = re.compile(r"\s+")
_SEPARATORS = re.compile(r"[/\\]")  # both POSIX and Windows separators

# Phase 57: uploaded documents and the vector index derived from them can
# hold private (often medical) content, now additionally partitioned by
# account (Phase 55). Owner-only permissions are the storage-side half of
# that isolation - metadata filtering keeps one account from *querying*
# another's data, but a shared filesystem location readable by any local
# account or process would still leak the raw bytes underneath it.
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def restrict_to_owner(path: Path) -> None:
    """Best-effort: remove group/other access from a directory or file that
    may hold document content. Never raises - a chmod failure (an
    unsupported filesystem, Windows, a restricted container) must not break
    the upload/ingest it is hardening; the path traversal and filename
    protections below remain the primary defense either way."""
    try:
        mode = PRIVATE_DIR_MODE if path.is_dir() else PRIVATE_FILE_MODE
        os.chmod(path, mode)
    except OSError:
        pass


def sanitize_filename(filename: str) -> str:
    """Return a filesystem-safe version of ``filename``.

    Strips any directory components (on every OS, regardless of the current
    platform), unicode look-alikes, and characters that are unsafe across
    operating systems. The result is never empty — falls back to ``"file"``.
    """
    parts = _SEPARATORS.split(filename)
    name = parts[-1] if parts else ""
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = _UNSAFE_CHARS.sub("_", name.strip())
    name = _MULTI.sub(" ", name).strip("._ ")
    return name or "file"


def ensure_within(base: Path, candidate: Path) -> Path:
    """Raise SecurityError unless ``candidate`` resolves inside ``base``.

    Returns the resolved candidate path on success.
    """
    base_resolved = base.resolve()
    candidate_resolved = candidate.resolve()
    if base_resolved != candidate_resolved and base_resolved not in candidate_resolved.parents:
        raise SecurityError(
            what=f"Refusing to access `{candidate}` — it is outside the allowed directory `{base}`.",
            why="Accessing files outside the project directories could leak or overwrite other data.",
            fix=f"Place files inside `{base}` or configure a different directory in .env.",
        )
    return candidate_resolved


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Streaming SHA-256 of a file (used for duplicate document detection)."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"
