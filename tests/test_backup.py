"""Phase 92: backup/restore for every persistent store, using SQLite's
online backup API for databases and verified via real checksums - no
network, no real deployment."""

from __future__ import annotations

import json
import sqlite3
import tarfile

import pytest

from apex_ai.backup import RestoreError, create_backup, restore_backup, verify_backup
from apex_ai.config.settings import Settings


def _isolated_settings(tmp_path, name="src") -> Settings:
    root = tmp_path / name
    return Settings(
        database_path=root / "chroma",
        upload_dir=root / "uploads",
        model_dir=root / "models",
        log_dir=root / "logs",
        cache_dir=root / "cache",
        memory_path=root / "conversation_memory.json",
        conversation_db_path=root / "conversations.db",
        long_term_memory_db_path=root / "long_term_memory.db",
        users_db_path=root / "users.db",
        collections_db_path=root / "collections.db",
        projects_db_path=root / "projects.db",
        billing_db_path=root / "billing.db",
    )


def _write_sqlite(path, table_sql, insert_sql, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(table_sql)
        connection.executemany(insert_sql, rows)
        connection.commit()
    finally:
        connection.close()


def test_create_backup_with_no_data_anywhere_still_succeeds(tmp_path):
    settings = _isolated_settings(tmp_path)
    result = create_backup(settings, tmp_path / "backups")
    assert result.archive_path.is_file()
    assert result.file_count == 0  # nothing has ever been written - nothing to list
    assert verify_backup(result.archive_path) == []


def test_create_backup_captures_real_sqlite_data_via_the_online_backup_api(tmp_path):
    settings = _isolated_settings(tmp_path)
    _write_sqlite(
        settings.users_db_path,
        "CREATE TABLE users(id TEXT PRIMARY KEY, email TEXT)",
        "INSERT INTO users VALUES (?, ?)",
        [("user-1", "a@example.com"), ("user-2", "b@example.com")],
    )

    result = create_backup(settings, tmp_path / "backups")
    assert verify_backup(result.archive_path) == []

    restored = tmp_path / "restored"
    problems = restore_backup(result.archive_path, restored)
    assert problems == []

    connection = sqlite3.connect(str(restored / "db" / "users.db"))
    try:
        rows = connection.execute("SELECT id, email FROM users ORDER BY id").fetchall()
    finally:
        connection.close()
    assert rows == [("user-1", "a@example.com"), ("user-2", "b@example.com")]


def test_create_backup_includes_chroma_dir_and_uploads(tmp_path):
    settings = _isolated_settings(tmp_path)
    settings.database_path.mkdir(parents=True)
    (settings.database_path / "chroma.sqlite3").write_bytes(b"fake chroma data")
    settings.upload_dir.mkdir(parents=True)
    (settings.upload_dir / "user-1" / "doc.pdf").parent.mkdir(parents=True)
    (settings.upload_dir / "user-1" / "doc.pdf").write_bytes(b"%PDF-fake")

    result = create_backup(settings, tmp_path / "backups")
    restored = tmp_path / "restored"
    assert restore_backup(result.archive_path, restored) == []
    assert (restored / "chroma" / "chroma.sqlite3").read_bytes() == b"fake chroma data"
    assert (restored / "uploads" / "user-1" / "doc.pdf").read_bytes() == b"%PDF-fake"


def test_create_backup_includes_registry_and_memory_json(tmp_path):
    settings = _isolated_settings(tmp_path)
    settings.database_path.mkdir(parents=True)
    registry_path = settings.database_path.parent / "document_registry.json"
    registry_path.write_text(json.dumps([{"document_id": "abc"}]))
    settings.memory_path.parent.mkdir(parents=True, exist_ok=True)
    settings.memory_path.write_text(json.dumps({"turns": []}))

    result = create_backup(settings, tmp_path / "backups")
    restored = tmp_path / "restored"
    assert restore_backup(result.archive_path, restored) == []
    assert json.loads((restored / "document_registry.json").read_text()) == [
        {"document_id": "abc"}
    ]
    assert json.loads((restored / "conversation_memory.json").read_text()) == {"turns": []}


def test_missing_stores_are_omitted_not_errors(tmp_path):
    """A store that was never used has no file on disk yet - that's not a
    failure, there's simply nothing to back up for it."""
    settings = _isolated_settings(tmp_path)
    result = create_backup(settings, tmp_path / "backups")
    with tarfile.open(result.archive_path, "r:gz") as archive:
        names = archive.getnames()
    assert not any(name.startswith("./db/") for name in names)
    assert not any("chroma" in name for name in names)


def test_restore_refuses_to_overwrite_an_existing_target(tmp_path):
    settings = _isolated_settings(tmp_path)
    result = create_backup(settings, tmp_path / "backups")
    target = tmp_path / "existing"
    target.mkdir()

    with pytest.raises(RestoreError, match="already exists"):
        restore_backup(result.archive_path, target)


def test_verify_backup_detects_a_missing_manifest(tmp_path):
    fake_archive = tmp_path / "not-a-real-backup.tar.gz"
    with tarfile.open(fake_archive, "w:gz") as archive:
        junk = tmp_path / "junk.txt"
        junk.write_text("not a backup")
        archive.add(junk, arcname="junk.txt")

    problems = verify_backup(fake_archive)
    assert problems == ["manifest.json is missing from the backup."]


def test_verify_backup_detects_a_corrupted_file(tmp_path):
    settings = _isolated_settings(tmp_path)
    _write_sqlite(
        settings.users_db_path,
        "CREATE TABLE users(id TEXT PRIMARY KEY)",
        "INSERT INTO users VALUES (?)",
        [("user-1",)],
    )
    result = create_backup(settings, tmp_path / "backups")

    # Tamper with the archive: extract, corrupt one backed-up file, re-pack -
    # the manifest (recorded at creation time) now disagrees with the content.
    extracted = tmp_path / "tamper"
    with tarfile.open(result.archive_path, "r:gz") as archive:
        archive.extractall(extracted, filter="data")
    (extracted / "db" / "users.db").write_bytes(b"corrupted, not a real sqlite file")
    tampered_archive = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered_archive, "w:gz") as archive:
        for item in sorted(extracted.iterdir()):
            archive.add(item, arcname=item.name)

    problems = verify_backup(tampered_archive)
    assert any("db/users.db" in problem and "checksum mismatch" in problem for problem in problems)


def test_backup_output_dir_is_created_if_missing(tmp_path):
    settings = _isolated_settings(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist" / "yet"
    result = create_backup(settings, output_dir)
    assert result.archive_path.is_file()
    assert result.archive_path.parent == output_dir
