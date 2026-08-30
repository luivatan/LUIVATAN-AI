# Apex AI Phase 78 — File Analysis Format Expansion (CSV/TSV)

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `6d255e1`, immediately following Phase 77 (Structured Outputs)
- **Scope:** "Expand supported document and data analysis only when the
  backend can actually process those formats." CSV and TSV are the one
  format expansion this phase adds — real, stdlib-only support (Python's
  `csv` module), reusing the existing extraction → chunking → embedding →
  retrieval pipeline with no new code path downstream of extraction.

## Why CSV/TSV, and not something else

The roadmap's own qualifier — "only when the backend can actually
process those formats" — is exactly the same license this session applied
to Phase 75's web search decision, evaluated the other direction here: CSV
and TSV genuinely can be processed with what is already available
(stdlib `csv`, no new dependency), so this phase adds them for real. A
format like DOCX would need a new third-party dependency
(`python-docx`) purely to unlock one more format; that is a real,
separate cost/benefit call (a new dependency to maintain and keep updated,
license and supply-chain surface, offline-cache implications) that
deserves its own deliberate decision rather than being bundled into "add
CSV support" just because both are spreadsheet/document-adjacent. It is
left for a future phase to decide on its own merits.

## Design: reuse the existing pipeline, add no new one

`_extract_csv()` (`apex_ai/documents/extraction.py`) turns each data row
into one paragraph — `"column: value, column: value, ..."` — separated by
blank lines, exactly the shape `_extract_json()` already produces from
nested JSON string leaves. That means the existing paragraph-based
`Chunker`, embeddings, vector store, and retrieval pipeline handle CSV/TSV
documents with zero CSV-specific code anywhere downstream of extraction —
the same "a `Document`/`Page` structure is all any other layer needs to
know about" boundary every other format already respects.

The first row is always treated as the header (column names); blank rows
are skipped; a row's cells pair with the header positionally
(`zip(header, cells)`, which safely handles a ragged row of mismatched
length by just pairing however many cells exist). A file with only a
header row (no data), or no rows at all, is rejected with a specific,
actionable message rather than silently indexing nothing.

**Row-count bound.** The same reasoning as Phase 70's `max_document_pages`:
a spreadsheet can be well within `APEX_MAX_UPLOAD_MB` and still have a
pathological row count. `APEX_MAX_CSV_ROWS` (default 5000) is checked as
soon as the row count is known, before any per-row text is built —
`IngestionService.ingest_path()` reads it from settings the same way it
already reads `max_document_pages`.

## Files

- `apex_ai/documents/extraction.py` — `_extract_csv()`; `.csv`/`.tsv` added
  to `SUPPORTED_EXTENSIONS` and `extract_document()`'s dispatch
- `apex_ai/documents/service.py` — `ingest_path()` passes
  `settings.max_csv_rows`
- `apex_ai/documents/models.py`, `apex_ai/config/settings.py`,
  `.env.example`, `README.md` — `max_csv_rows` / `APEX_MAX_CSV_ROWS`
- `apex_ai/api/uploads.py` — updated unsupported-file-type message
- `apex_ai/web/templates/index.html`, `apex_ai/web/static/app.js` — upload
  hints and the composer's allowed-extension list now mention CSV/TSV
- `tests/test_documents.py`, `tests/test_vectordb.py` — extraction
  correctness, row-limit enforcement, and a real end-to-end
  ingest-then-search round trip

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 461 passed, 3 skipped |
| `tests/test_documents.py` | 28 passed |
| `tests/test_vectordb.py` | 14 passed |
| `node --check apex_ai/web/static/app.js` | OK |
| `ruff check` on every touched file | only pre-existing findings (verified identical against baseline) |

## Deliberately not done in this phase

- **No DOCX, XLSX, or other binary-format support.** Each would need a new
  dependency and its own extraction design (XLSX in particular has multiple
  sheets, formulas, and merged cells to decide how to represent) — a
  separate, deliberate decision, not an extension of this phase's
  stdlib-only scope.
- **No per-column type inference or numeric analysis of CSV content.**
  Phase 76's `data_stats` tool already gives the model exact aggregation
  over numbers *it* is given; teaching ingestion itself to detect and
  summarize numeric columns would be a different, much larger feature
  (real spreadsheet analysis) than "index this as searchable text."
- **No CSV dialect auto-detection** (custom delimiters, alternate quoting).
  `.csv` is comma-delimited and `.tsv` is tab-delimited, the two
  unambiguous, standard conventions; anything else is out of scope until a
  real need for it appears.
