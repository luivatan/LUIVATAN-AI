"""Backup and restore for every persistent store Apex AI writes to (Phase
92): SQLite databases, the Chroma vector store directory, the uploads
directory, and the small JSON registries (document registry, conversation
memory).

Design notes
------------
- **SQLite files use SQLite's own online backup API**
  (``sqlite3.Connection.backup()``), not a plain file copy. A live database
  can be mid-write (especially in WAL mode); the online backup API is the
  documented, safe way to copy a SQLite database while it may be open
  elsewhere, unlike ``shutil.copy2`` which could copy a torn/inconsistent
  snapshot.
- **Chroma and the uploads directory are best-effort directory copies.**
  Chroma has no equivalent public online-backup API from Python, so the
  same caveat any file-based backup has applies: taken while the app is
  actively writing, it is best-effort, not guaranteed point-in-time
  consistent. This is stated plainly rather than implying a stronger
  guarantee than what's actually provided.
- **Every backup carries a manifest** (path, sha256, size for every file)
  so a backup can be *verified* - restored into a scratch location and
  checked byte-for-byte - without trusting that "the archive exists"
  means "the archive is intact."
- **Restoring never overwrites an existing directory.** The target must
  not already exist; the caller chooses where a restored copy lands and
  decides what to do with it next, the same non-destructive-by-default
  posture the rest of this codebase already holds (Phase 68's document
  versioning, for one).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from apex_ai import __version__
from apex_ai.core.errors import ApexError
from apex_ai.core.logging import get_logger

log = get_logger("backup")


class BackupError(ApexError):
    title = "BACKUP ERROR"
    code = "backup_error"


class RestoreError(ApexError):
    title = "RESTORE ERROR"
    code = "restore_error"


def _now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(str(source))
    destination_connection = sqlite3.connect(str(destination))
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _sqlite_sources(settings) -> dict[str, Path]:
    """Every SQLite database Apex AI's stores can write to. A dict, not a
    list, so the archive layout (``db/<name>``) stays predictable
    regardless of how many exist on a given install."""
    return {
        "users.db": Path(settings.users_db_path),
        "conversations.db": Path(settings.conversation_db_path),
        "long_term_memory.db": Path(settings.long_term_memory_db_path),
        "collections.db": Path(settings.collections_db_path),
        "projects.db": Path(settings.projects_db_path),
        "billing.db": Path(settings.billing_db_path),
    }


def _manifest_files(root: Path) -> list[dict[str, object]]:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            entries.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return entries


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    created_at: str
    file_count: int
    total_bytes: int


def create_backup(settings, output_dir: Path) -> BackupResult:
    """Create one self-contained, timestamped ``.tar.gz`` backup covering
    every persistent store this installation actually has data in. A
    store that has never been used (no file on disk yet) is simply
    omitted - there is nothing to back up, not an error."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"apex-backup-{_now_slug()}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="apex-backup-") as staging_name:
        staging = Path(staging_name)

        for name, source_path in _sqlite_sources(settings).items():
            if not source_path.is_file():
                continue
            try:
                _backup_sqlite(source_path, staging / "db" / name)
            except sqlite3.Error as error:
                raise BackupError(
                    what=f"Could not back up {name}.",
                    why=str(error),
                    fix="Check the source database isn't corrupted and this "
                        "process has read access to it.",
                ) from error

        for name, source_dir in (
            ("chroma", Path(settings.database_path)),
            ("uploads", Path(settings.upload_dir)),
        ):
            if source_dir.is_dir():
                shutil.copytree(source_dir, staging / name)

        registry_path = Path(settings.database_path).parent / "document_registry.json"
        if registry_path.is_file():
            shutil.copy2(registry_path, staging / "document_registry.json")
        if Path(settings.memory_path).is_file():
            shutil.copy2(settings.memory_path, staging / "conversation_memory.json")

        manifest_entries = _manifest_files(staging)
        created_at = _now_iso()
        manifest = {
            "apex_ai_version": __version__,
            "created_at": created_at,
            "files": manifest_entries,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        with tarfile.open(archive_path, "w:gz") as archive:
            for item in sorted(staging.iterdir()):
                archive.add(item, arcname=item.name)

    log.info(
        "Backup created: %s (%d files, %d bytes)",
        archive_path,
        len(manifest_entries),
        sum(int(entry["size_bytes"]) for entry in manifest_entries),
    )
    return BackupResult(
        archive_path=archive_path,
        created_at=created_at,
        file_count=len(manifest_entries),
        total_bytes=sum(int(entry["size_bytes"]) for entry in manifest_entries),
    )


def _verify_extracted(directory: Path) -> list[str]:
    """Check every file an extracted backup claims to have against its
    manifest's recorded checksum. An empty list means the backup is
    genuinely, byte-for-byte intact - not merely "the archive opened."""
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json is missing from the backup."]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"manifest.json is not valid JSON: {error}"]

    problems: list[str] = []
    for entry in manifest.get("files", []):
        file_path = directory / entry["path"]
        if not file_path.is_file():
            problems.append(f"{entry['path']}: missing from the backup.")
            continue
        actual = _sha256(file_path)
        if actual != entry["sha256"]:
            problems.append(f"{entry['path']}: checksum mismatch (the backup may be corrupted).")
    return problems


def verify_backup(archive_path: Path) -> list[str]:
    """Extract ``archive_path`` into a throwaway location and verify every
    file against its recorded checksum, without touching any real data
    directory. Returns a list of problems found - empty means verified."""
    archive_path = Path(archive_path)
    with tempfile.TemporaryDirectory(prefix="apex-verify-") as extract_name:
        extract_dir = Path(extract_name)
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                archive.extractall(extract_dir, filter="data")
        except (tarfile.TarError, OSError) as error:
            return [f"Could not open or extract the archive: {error}"]
        return _verify_extracted(extract_dir)


def restore_backup(archive_path: Path, target_dir: Path) -> list[str]:
    """Extract ``archive_path`` into ``target_dir`` and verify what was
    written. ``target_dir`` must not already exist - restoring never
    silently overwrites a live data directory; the caller decides what to
    do with the restored copy next (e.g. swap it in after stopping the
    running application). Returns the same problem list ``verify_backup``
    would - empty means the restore is genuinely intact."""
    archive_path = Path(archive_path)
    target_dir = Path(target_dir)
    if target_dir.exists():
        raise RestoreError(
            what=f"The restore target already exists: {target_dir}",
            why="Restoring into an existing directory could silently overwrite live data.",
            fix="Choose an empty/new target directory, or move the existing one aside first.",
        )
    target_dir.mkdir(parents=True)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(target_dir, filter="data")
    except (tarfile.TarError, OSError) as error:
        raise RestoreError(
            what=f"Could not open or extract the archive: {archive_path}",
            why=str(error),
            fix="Verify the backup file isn't truncated or corrupted; try an earlier backup.",
        ) from error
    problems = _verify_extracted(target_dir)
    log.info(
        "Restore of %s into %s: %s",
        archive_path,
        target_dir,
        "verified intact" if not problems else f"{len(problems)} problem(s) found",
    )
    return problems


__all__ = [
    "BackupError",
    "BackupResult",
    "RestoreError",
    "create_backup",
    "restore_backup",
    "verify_backup",
]
