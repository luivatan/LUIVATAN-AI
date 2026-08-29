# Apex AI Phases 61–65, 69 — Documents Workspace (verified pre-existing)

- **Reviewed:** 2026-08-29 (America/Chicago)
- **Baseline:** `b389936` (Phase 60, end of Section 5)
- **Finding:** six of Section 6's ten phases were already real, working,
  tested features before this review — built incrementally during the
  Phase 11-20 (ChatGPT-style UX) and 21-40 (Advanced RAG) work, before
  Section 6 existed as a named checkpoint. This doc is the verification
  pass the roadmap's own rules require before marking a phase done: each
  claim below was checked against the running code and a passing test, not
  assumed from a phase name matching a feature that sounds similar.

## Why this doc exists

Nothing in `docs/` mentioned Phases 61-70 before this review — Section 6
hadn't been touched as a named unit. But grepping the actual `apex_ai/`
source turned up a fully-built, separately-routed Documents page, a real
upload state machine, working delete/re-index, and an eval-dataset category
specifically exercising cross-document retrieval. Re-implementing any of
this to "complete the phase" would violate the roadmap's own rule against
fake/duplicate work; the honest move is to verify it, test it as it stands,
and document it as done — which is what this pass does. Four phases
(66-68, 70) are not covered here because they are genuinely new; see their
own phase docs.

## Phase 61 — Documents Workspace

**Claim:** a polished document management page, separate from chat.
**Evidence:** `apex_ai/web/templates/index.html`'s `#documentsView` is a
dedicated `page-view` section (heading "Documents", subtitle "Everything
Apex AI can retrieve and cite in your answers"), reached via the sidebar's
`data-view="documents"` link and the welcome screen's "Add knowledge"
suggestion — a full navigational destination, not a modal bolted onto chat.
`test_web_shell_is_chat_first_and_has_security_headers` asserts `"Documents"`
appears in the served shell; `test_uploaded_documents_are_isolated_between_accounts`
and `test_browser_upload_connects_to_existing_ingestion_pipeline` exercise
the page's backing routes end-to-end.

## Phase 62 — Upload Pipeline

**Claim:** reliable upload, extraction, processing, and indexing status.
**Evidence:** `apex_ai/api/uploads.py`'s `/documents/upload` streams the
file to a staging location with a real byte-count size check (Phase 57),
validates the extension before touching disk, then hands off to
`IngestionService.ingest_path()` — extract → chunk → embed → upsert →
registry update — returning a structured `IngestResult` (`status`,
`chunks`, `warnings`, `message`) the frontend surfaces via `toast()`.
Failure at any stage (unsupported type, oversized, corrupt/unreadable file,
empty extraction) produces a specific, actionable error rather than a
generic failure — see `apex_ai/documents/service.py` and
`apex_ai/documents/extraction.py`'s per-stage `DocumentProcessingError`s.

## Phase 63 — Processing States

**Claim:** pending, processing, completed, and failed document states shown
to the user.
**Evidence:** `apex_ai/web/static/app.js`'s upload state machine
(`queueFiles`/`uploadOne`/`renderAttachmentTray`) tracks each file through
`ready` (queued, pending upload) → `uploading` (in flight — rendered as
"Processing {name}…") → `done` or `error`, each with a distinct CSS state
(`.attachment-chip.uploading`/`.done`/`.error` in `apex_ai/web/static/app.css`).
Backend ingestion is synchronous (one HTTP request completes the whole
extract/chunk/embed pipeline), so there is no server-side queue to poll —
the four states are the real lifecycle of that one request as experienced
by the user, not a queue simulated for appearance. A future move to
background/async ingestion (relevant mainly for Phase 70's large-file case)
would need this doc revisited alongside it.

## Phase 64 — Document Management

**Claim:** view, delete, and re-index documents.
**Evidence:** `GET /documents` (list), `DELETE /documents/{id}` (delete),
`POST /documents/{id}/reindex` (re-index) are all real, authenticated,
per-account-scoped routes (Phase 54/55) with matching UI actions in
`documentsView` (`loadDocuments`, `deleteDocument`, `reindexDocument` in
`app.js`, wired to per-row delete/re-index buttons). Tested end-to-end via
`test_memory_management_list_delete_and_clear`-adjacent document flows in
`tests/test_conversations_web.py` and directly via
`tests/test_vectordb.py::test_reindex_replaces_chunks`,
`test_delete_document_removes_all_chunks`.

## Phase 65 — Multiple Document RAG

**Claim:** a question can retrieve evidence across multiple documents.
**Evidence:** this was never a per-document search — `HybridRetriever`
(Phase 21-40) always searches a user's *entire* indexed chunk set (now
correctly scoped per-account, Phase 55), fuses results from every matching
document via weighted RRF, and `build_context` explicitly interleaves
evidence from different `document_id`s rather than exhausting one document
first (`apex_ai/rag/context_builder.py`). `eval/dataset.example.jsonl`
carries a dedicated `"multi-document"` category
(`test_example_dataset_covers_required_quality_categories` requires it to
be present), and `tests/test_retrieval.py::test_bm25_finds_exact_terms`
ingests three separate documents and retrieves across all of them in one
query.

## Phase 69 — Re-indexing

**Claim:** reliable re-indexing after document changes.
**Evidence:** the same `reindex()` path verified under Phase 64 — it
deletes the document's existing chunks (scoped to the calling account,
Phase 55) and re-runs the full ingest pipeline against the original file on
disk (`IngestionService.reindex`, `documents/service.py`), so a changed
chunking configuration or a fixed extraction bug is reflected without a
manual delete-then-reupload. This phase and Phase 64 describe the same
underlying capability from two different angles (the roadmap doesn't
distinguish "re-index as document management" from "re-index as content
freshness"); nothing further was needed once Phase 64's mechanism existed.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 311 passed, 3 skipped (no code change) |
| `tests/test_vectordb.py` (list/delete/reindex/duplicate-detection) | 11 passed |
| `tests/test_retrieval.py` (cross-document BM25/hybrid retrieval) | 9 passed |
| `tests/test_conversations_web.py` (upload/list/isolation end-to-end) | 26 passed |
| Read `apex_ai/web/templates/index.html`, `app.js`, `app.css` for the Documents page, upload state machine, and management actions | confirmed present and wired to real routes, not placeholders |
