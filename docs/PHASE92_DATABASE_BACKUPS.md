# Apex AI Phase 92 — Database Backups

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `07d3a40`, immediately following the `scripts/ingest_folder.py` bugfix
  (itself immediately following Section 8 / Phase 90)
- **Scope:** "Create and verify backups and a recovery procedure." The
  first real, buildable phase of Section 9 (Production, Monitoring &
  Sales) under the user's chosen scope — code Claude can actually run and
  test stands on its own merit here; deployment/monitoring/customer
  phases that need real infrastructure or real people are separate
  decisions, tracked in their own docs.

## Design: one archive, everything persistent, provably restorable

`apex_ai/backup.py`'s `create_backup(settings, output_dir)` covers every
persistent store this application writes to in one `.tar.gz`:

- **Every SQLite database** (users, conversations, long-term memory,
  collections, projects, billing) — copied via SQLite's own **online
  backup API** (`sqlite3.Connection.backup()`), not a plain file copy. A
  database can be open elsewhere at the moment of backup (WAL mode
  especially); the online backup API is the documented, safe way to copy
  one under those conditions, where a raw `shutil.copy2` could capture a
  torn, inconsistent snapshot mid-write.
- **The Chroma vector store and uploads directories** — best-effort
  directory copies. Chroma has no equivalent public online-backup API from
  Python, so this phase states the real limitation plainly rather than
  implying a guarantee the tooling doesn't actually provide: a backup
  taken while the app is actively writing to these directories is not
  guaranteed point-in-time consistent.
- **The small JSON registries** (document registry, conversation memory)
  — plain file copies, since they're written as a single atomic
  `write_text()` call each, not incrementally.

A store that has never been used (no file on disk yet) is simply omitted
— not an error. Every backed-up file is recorded in a `manifest.json`
(relative path, SHA-256, size) written into the archive itself, so the
archive is self-describing and independently verifiable without needing
the original source data to compare against.

## Verification is not optional-by-convention — it's a real function call

"Create and verify" is two different guarantees, and this phase keeps them
that way: `verify_backup(archive_path)` extracts an archive into a
throwaway temp location and checks every file against its manifest
checksum, returning the list of problems found (empty = verified).
`restore_backup(archive_path, target_dir)` does the real thing (extracts
into a real target) and calls the *same* verification logic on what it
just wrote — a restore is only ever reported as successful once every
byte has been checked, not merely once the archive opened without error.
`scripts/backup.py --verify` runs `verify_backup()` immediately after
creating a new archive, so "backup succeeded" and "backup is provably
restorable" are checked in the same run, not left as separate hope.

**Restoring never overwrites an existing directory.** `restore_backup()`
raises `RestoreError` if `target_dir` already exists — the same
non-destructive-by-default posture Phase 68 (document versioning)
established: a destructive action needs an explicit, separate decision
from the caller, never a side effect of the tool doing its main job.

## Files

- `apex_ai/backup.py` (new) — `create_backup()`, `verify_backup()`,
  `restore_backup()`, `BackupError`, `RestoreError`
- `scripts/backup.py`, `scripts/restore.py` (new) — thin CLI wrappers;
  read configuration only (`load_settings()`), never load the embedding
  model or LLM, so a backup can run even when the model isn't set up
- `README.md` — a new "Backups" section
- `tests/test_backup.py` (new)

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 539 passed, 3 skipped |
| `tests/test_backup.py` | 9 passed |
| Real end-to-end CLI smoke test | `scripts/backup.py --verify` → real archive created and verified; `scripts/restore.py` → real SQLite data round-tripped correctly (`SELECT` against the restored DB returned the original rows); re-running restore into the same target correctly refused with `RestoreError` |
| `ruff check` on every new file | clean |

## Deliberately not done in this phase

- **No automatic/scheduled backups.** This phase delivers the
  create/verify/restore capability for real; wiring it to a cron job or
  systemd timer is a deployment-environment decision (Phase 91, declined
  this pass — see `docs/PHASE91_PRODUCTION_DEPLOYMENT_DECISION.md`) rather
  than something to assume into this phase.
- **No off-host/offsite backup upload** (S3, another server, etc.) — that
  needs real destination credentials this environment doesn't have, the
  same reasoning behind every Section 8 payment-provider decision.
- **No point-in-time consistency guarantee for Chroma/uploads**, stated
  explicitly above rather than implied.
- **No backup encryption.** The archive contains the same data already at
  rest unencrypted in `data/`; encrypting backups specifically (without
  also addressing at-rest encryption of the live data) would be a
  narrower, arguably misleading guarantee. A real deployment should apply
  encryption at the storage layer (disk/volume encryption) uniformly
  rather than only for backup archives.
