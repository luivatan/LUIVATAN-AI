# Apex AI Phase 57 — File Security

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `e035226` (Phase 55 document isolation completed)
- **Scope:** the roadmap names four things to validate — file sizes, types,
  names, and storage permissions. Three already had real, tested protection
  before this phase (inspected and verified, not assumed); storage
  permissions did not, and is what this phase adds.

## What was already true (inspected, not assumed)

- **Sizes.** `apex_ai/api/uploads.py` enforces `APEX_MAX_UPLOAD_MB` as a
  *streaming* check — it aborts as soon as the running byte count crosses the
  limit, never buffering an oversized file to disk first. Previously
  untested end-to-end; `test_browser_upload_rejects_oversized_file` (new,
  this phase) proves the real HTTP path returns `413 upload_too_large` and
  that no partial file gets indexed.
- **Types.** `SUPPORTED_EXTENSIONS` gates the upload route by extension
  before anything is written to a permanent location, and every extraction
  path (`apex_ai/documents/extraction.py`) independently validates real
  content at parse time regardless of what the extension claimed: `pypdf`
  raises on a non-PDF renamed to `.pdf` (wrapped as `DocumentProcessingError`),
  `json.loads` raises on non-JSON. A magic-byte pre-check was considered and
  rejected as redundant — the parser itself is the authoritative check, and
  adding a second, weaker one (sniffing a handful of header bytes) would be
  exactly the kind of defense-in-depth that adds surface area without adding
  real protection here, since a parser failure already produces the same
  safe, informative rejection.
- **Names.** `sanitize_filename` (`apex_ai/security/files.py`) strips
  directory components, unicode look-alikes, and any character outside a
  small allowlist before a filename ever reaches the filesystem;
  `ensure_within` independently verifies every resolved path stays inside
  the intended directory. Both had existing unit tests
  (`test_sanitize_filename_blocks_traversal`, `test_ensure_within_blocks_escape`).
  This phase adds one more layer: an end-to-end proof
  (`test_browser_upload_sanitizes_a_path_traversal_filename`) that a
  `../../../../etc/passwd.md`-style filename submitted through the real
  `/documents/upload` endpoint comes out sanitized, not honored — closing the
  gap between "the helper function is correct" and "the route actually calls
  it correctly on every path."

## What this phase adds: storage permissions

Phase 55 partitioned document storage by account (per-user Chroma metadata,
per-user upload subdirectories, per-user BM25 sub-indices) — but partition
labels alone don't stop a different local account or process on the same
machine from reading the raw files or the vector database directly off disk.
Metadata filtering is an *application-layer* boundary; permission bits are
the *filesystem-layer* boundary underneath it, and Apex AI had none before
this phase — new directories and files were created with whatever the
process umask happened to allow (commonly world-readable).

`apex_ai.security.files.restrict_to_owner(path)` is a small best-effort
helper: `chmod 700` for a directory, `chmod 600` for a file, swallowing
`OSError` so a chmod failure (an unsupported filesystem, Windows, a
restricted container) never breaks the upload/ingest it's hardening — the
path-traversal and filename protections above remain the primary defense
either way. It's applied to every location that holds real document content:

- `settings.database_path` and `settings.upload_dir` themselves, right after
  creation at startup (`runtime.py`).
- Each account's upload subdirectory and the file copied into it
  (`IngestionService.ingest_path`, `documents/service.py`).
- The transient per-request staging directory and file used while a browser
  upload is still being streamed to disk, before `IngestionService` takes
  over (`api/uploads.py`).

This is best-effort hardening, not a hard guarantee — a multi-tenant
production deployment on a *shared* machine still needs proper OS-level user
separation or containerization; Apex AI's own process runs as one OS user, so
`700`/`600` means "only this process (and whoever can already impersonate
it) can read this," which is the strongest guarantee achievable from inside
a single-process application.

## Files

- `apex_ai/security/files.py` — new `restrict_to_owner()`,
  `PRIVATE_DIR_MODE`/`PRIVATE_FILE_MODE` constants.
- `apex_ai/runtime.py` — restricts `database_path`/`upload_dir` at startup.
- `apex_ai/documents/service.py` — restricts each account's upload
  subdirectory and every file copied into it.
- `apex_ai/api/uploads.py` — restricts the transient staging directory/file
  used during a browser upload.
- `tests/test_documents.py` — `test_restrict_to_owner_removes_group_and_other_access`,
  `test_restrict_to_owner_never_raises_on_a_missing_path`.
- `tests/test_conversations_web.py` — `test_browser_upload_rejects_oversized_file`,
  `test_browser_upload_sanitizes_a_path_traversal_filename` (both end-to-end,
  through the real HTTP API).

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 300 passed, 3 skipped |
| `tests/test_documents.py` | 17 passed |
| `tests/test_conversations_web.py` | 26 passed |
| `ruff check` on every new/touched file | only pre-existing findings (verified unchanged from baseline) |

## Deliberately not done in this phase

- **No magic-byte/MIME sniffing.** See "Types" above — the existing
  extension gate plus parse-time validation is judged sufficient; adding a
  sniffing layer would duplicate protection the parsers already provide.
- **No antivirus/malware scanning.** Out of scope for an offline-first
  personal/small-team tool; would also contradict the "offline-first, no
  external calls" design unless a fully local scanner were added, which
  nothing in the roadmap asks for.
- **No filesystem-level encryption at rest.** Permission bits restrict *who*
  can read the files from this machine; they don't protect the data if the
  disk itself is stolen or imaged. Encryption at rest is a deployment/OS
  concern (LUKS, BitLocker, cloud-provider disk encryption) rather than
  something the application should reimplement.
