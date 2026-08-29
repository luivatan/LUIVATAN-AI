# Apex AI Phase 70 — Large Document Handling

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `4f0d2f5`, immediately following Phase 68 (Document Versioning)
- **Scope:** "Improve processing for large files without exhausting memory
  or model context." Audited the actual processing pipeline end to end
  before writing anything — most of this concern turned out to already be
  structurally handled by the existing architecture; one real gap remained
  and is what this phase closes.

## What the audit found already handled

- **Model context.** This concern is resolved by the RAG design itself, not
  by anything document-size-specific: retrieval never puts a whole document
  into the LLM prompt. `HybridRetriever` returns the top-K most relevant
  chunks (`APEX_TOP_K`, default 12) regardless of how large the source
  document is, and `context_builder.py` enforces its own strict character
  budget on top of that. A 2,000-page document and a 2-page document
  contribute the same bounded amount of context to any one answer.
- **Embedding memory.** `SentenceTransformerProvider.embed_documents()`
  passes `batch_size=settings.embedding_batch_size` (default 32) straight
  to `sentence-transformers`' own `.encode()`, which batches the actual
  compute internally rather than processing every chunk's vector in one
  pass. The full output vector list is materialized in memory, but even at
  an extreme 10,000 chunks and a 384-dimension model that's roughly 15 MB —
  not a real constraint.
- **Chunking.** `Chunker` already processes page-by-page with hard
  `min_chunk_size`/`max_chunk_size` bounds; nothing about a larger document
  changes its per-chunk memory profile, only the chunk *count*.
- **Upload size.** `APEX_MAX_UPLOAD_MB` (Phase 57) already bounds total
  bytes accepted through the browser upload path.

## The real gap: page count is not bytes

`_extract_pdf()` builds `raw_pages = [(page.extract_text() or "") for page
in reader.pages]` — every page's extracted text held in memory
simultaneously before any chunking begins. `APEX_MAX_UPLOAD_MB` bounds file
*size*, but PDF file size does not reliably bound page *count*: a file
built from many near-empty pages can sit comfortably under the byte limit
while still having an extreme page count, and processing that page-by-page
extraction (and everything downstream of it) is genuinely a memory and
latency risk that scales with pages, not bytes. This was the one concrete,
previously-unguarded failure mode.

## What this phase adds

`extract_document(path, max_pages=None)` — and `_extract_pdf()` beneath
it — check `len(reader.pages)` immediately after opening the PDF, *before*
extracting a single page's text, and raise a clear `DocumentProcessingError`
if it exceeds `max_pages`. `IngestionService.ingest_path()` passes
`settings.max_document_pages` (new setting, `APEX_MAX_DOCUMENT_PAGES`,
default 2000) through. Checking the page count via `len(reader.pages)` is
cheap — it reads the PDF's page tree structure, not page content — so the
guard itself adds negligible overhead to a document that's within the
limit.

`max_pages=None` (the default when calling `extract_document()` directly,
e.g. in tests that don't go through `IngestionService`) means no limit,
preserving prior behavior for every caller that doesn't opt in.

## Files

- `apex_ai/documents/extraction.py` — `extract_document()`/`_extract_pdf()`
  gained `max_pages`; the page-count check runs before text extraction.
- `apex_ai/documents/service.py` — `ingest_path()` passes
  `settings.max_document_pages`.
- `apex_ai/config/settings.py`, `.env.example`, `README.md` —
  `max_document_pages` / `APEX_MAX_DOCUMENT_PAGES` (default 2000).
- `tests/test_documents.py` — page-limit rejection (with the exact page
  count and limit in the message), a within-limit document proceeding to
  real extraction, and `max_pages=None` meaning no limit — using
  `pypdf.PdfWriter` to generate a real multi-page PDF fixture rather than
  needing a checked-in large binary file.
- `tests/test_vectordb.py` — proves `IngestionService` actually reads
  `settings.max_document_pages` (not just `extract_document`'s own
  default) end to end.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 345 passed, 3 skipped |
| `tests/test_documents.py` | 20 passed |
| `tests/test_vectordb.py` | 12 passed |
| `ruff check` on every touched file | only pre-existing findings (verified unchanged from baseline) |

## Deliberately not done in this phase

- **No streaming/incremental PDF extraction.** Bounding page count directly
  bounds the memory the current all-at-once extraction approach uses;
  rewriting extraction to be streaming/incremental would be a much larger
  change for a document class (PDFs large enough in page count to matter
  but still under the byte cap) that the page limit already makes safe.
- **No separate limit for TXT/MD/JSON.** These formats are read as one
  string in one call already bounded by `APEX_MAX_UPLOAD_MB` — unlike PDFs,
  their processing cost scales with bytes, which is already capped, so a
  second, format-specific limit would be redundant.
- **No async/background ingestion queue.** Phase 63's finding stands:
  ingestion is synchronous by design, and nothing in this phase's scope
  ("without exhausting memory or model context") requires changing that
  architecture — it requires bounding what one synchronous request can be
  asked to process, which the page limit does.
