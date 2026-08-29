# Apex AI Phase 68 — Document Versioning

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `4f0d2f5` (Phases 66/67 document collections)
- **Scope:** "Where useful, track document versions and avoid stale indexed
  content." The roadmap's own "where useful" qualifier matters here — this
  phase implements the one clearly useful, low-risk piece (detecting a
  likely new version and making the old one easy to remove) and explicitly
  does not implement automatic version replacement, which would be a
  destructive-by-default behavior nothing in the roadmap or the existing
  codebase's conventions calls for.

## The real gap this closes

Before this phase, `document_id` is a pure content hash (Phase 43), so
uploading a *revised* version of a document (same logical document, edited
content) produces a completely unrelated `document_id` with no link back to
the original. `reindex()` already handles "the same bytes, re-extracted" —
it does not and cannot handle "different bytes, same document." The result:
uploading "policy.md" today, then uploading an edited "policy.md" next
month, leaves **both** fully indexed and citable forever, with nothing in
the system able to tell you the first one is stale. That is exactly the
failure mode the phase name describes.

## Design: detect and surface, never auto-replace

`IngestionService.find_by_name(user_id, name, exclude_document_id="")`
looks for another document owned by the same account with the exact same
stored name (`DocumentInfo.name`, the sanitized upload filename — not the
content-derived `document_id`). `ingest_path()` calls it once, right before
returning, for every successful `"indexed"` result (never for
`"duplicate"` or `"empty"`, since those aren't new content). If found, the
new `IngestResult.previous_version_id` field carries the older document's
ID and the human-readable `message` gets a note.

Nothing is deleted, moved, or hidden automatically. `previous_version_id`
is purely a signal threaded up through `IngestOut`/`UploadOut` to the
caller, who already has everything needed to act on it: the existing
`DELETE /documents/{id}` route (unchanged, no new destructive endpoint
added). The frontend's `offerToRemovePreviousVersion()` uses the same
`confirmAction()` confirmation dialog every other destructive action in
this UI already uses, and calls the same delete call `deleteDocument()`
already makes — Phase 68 adds zero new ways to lose data, only a new way
to *notice* you might want to.

This was a deliberate rejection of the alternative: an explicit
`replace_document_id` parameter that auto-deletes the old document as part
of the same upload call. That would be one HTTP round-trip instead of two,
but it would mean the API silently destroys data as a side effect of an
upload — a real behavior change in kind (destructive-by-default) rather
than degree, and out of proportion to what "where useful" asks for.

## Files

- `apex_ai/documents/service.py` — `IngestResult.previous_version_id`; new
  `IngestionService.find_by_name()`; `ingest_path()` calls it and populates
  the field and message.
- `apex_ai/api/schemas.py` — `IngestOut.previous_version_id`.
- `apex_ai/api/uploads.py`, `apex_ai/api/server.py` — thread
  `previous_version_id` through the `/documents/upload`,
  `/documents/{id}/reindex`, and `/documents/ingest` response dicts.
- `apex_ai/web/static/app.js` — `offerToRemovePreviousVersion()`, called
  from `uploadOne()` whenever a response carries `previous_version_id`.
- `tests/test_collections.py` — service-layer coverage: flagging,
  non-destructiveness, no false positives across different names, and that
  a re-index never flags itself.
- `tests/test_conversations_web.py` — end-to-end: upload, upload again with
  the same name and different content, confirm both exist, delete the
  flagged old one via the existing route, confirm only the new one remains.
  Plus two static-marker assertions for the new frontend function.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 341 passed, 3 skipped |
| `tests/test_collections.py` | 18 passed |
| `node --check apex_ai/web/static/app.js` | passes |
| `ruff check` on every touched file | only pre-existing findings (verified unchanged from baseline) |

## Deliberately not done in this phase

- **No automatic replacement.** See "Design" above — this is the phase's
  central decision, not an oversight.
- **No version history / rollback.** Once a user deletes the flagged old
  version, it's gone the same way any other document deletion is gone
  (Phase 64) — there is no "restore a previous version" feature, since
  nothing in the roadmap's wording for this phase asks for full version
  history, only for stale-content detection.
- **No cross-collection or cross-account matching.** `find_by_name` is
  scoped to the same account (matching Phase 55's isolation discipline) but
  deliberately *not* scoped to the same collection — a document reorganized
  into a different collection with a revised name-match should still be
  flagged, since the goal is catching likely stale content, not modeling
  collections as version-history containers.
