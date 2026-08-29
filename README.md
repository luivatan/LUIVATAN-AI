# Apex AI

**Apex AI** is an offline-first, model-agnostic RAG (Retrieval-Augmented Generation)
assistant. Load a local LLM, index your own documents (PDF / TXT / Markdown / JSON),
and ask questions that are answered **only from retrieved evidence**, with page-level
citations.

```
USER → UI/API → QUERY PROCESSING → HYBRID RETRIEVAL → RERANKER
     → CONTEXT BUILDER → LOCAL LLM → GROUNDED ANSWER → CITATIONS

PDF/TXT/MD/JSON → DOCUMENT PROCESSOR → SMART CHUNKING → METADATA
     → EMBEDDINGS → CHROMADB
```

> **Medical-use disclaimer.** Apex AI works well with medical documents and reports
> what those documents say. It is **not** medical advice, not a diagnosis, and not a
> substitute for a qualified healthcare professional. It holds no regulatory approval
> and has no clinical validation. Always verify citations against the source document.

---

## Features

- **Offline-first** — after one-time model downloads, everything runs locally: no
  internet is needed for inference, embeddings, retrieval, or chat.
- **Model-agnostic** — a provider abstraction with local GGUF (llama.cpp), Ollama,
  OpenAI-compatible APIs, and local Hugging Face transformers. Adding a backend means
  adding one class.
- **Model manager** — scans a configurable models directory, shows name / type / size /
  status / active, and validates GGUF headers before loading.
- **Structure-aware ingestion** — per-paragraph page provenance, page ranges,
  headings → sections → paragraphs, extraction cleanup, scanned/empty-page detection.
- **Smart chunking** — configurable chunk/overlap/min/max sizes, sentence-boundary
  splits, hard post-overlap size limits, and no tiny-fragment merges across headings.
- **Hybrid retrieval** — independently configurable semantic and BM25 candidate pools
  merged with weighted Reciprocal Rank Fusion across conservative query variants.
- **Exact-term retrieval** — names, IDs, dates, numbers, versions, and abbreviations
  retain whole tokens as well as searchable components.
- **Conservative query processing** — simple questions remain untouched; clear
  follow-ups gain a lossless history query and genuinely multi-part questions gain
  bounded subqueries. Optional LLM rewriting rejects dropped exact terms.
- **Optional reranking** — cross-encoder if available, offline lexical fallback
  otherwise, or off. The app never breaks because a reranker is missing.
- **Grounded generation** — the LLM must answer from numbered evidence blocks and
  identify unsupported parts. A conservative semantic-plus-lexical gate refuses when
  retrieved context lacks enough support instead of trusting rank alone.
- **Honest citations** — sources are built only from chunks actually sent to the
  model, with SOURCE / PAGE or PAGE RANGE / SECTION headers and a source viewer in the UI.
- **Duplicate protection** — SHA-256 document IDs; re-uploading the same file is
  detected and skipped (or force re-indexed).
- **Bounded conversation context** — newest complete turns are selected under configurable
  turn, total-character, and per-message limits. History helps resolve follow-ups but is
  never treated as document evidence and can never be cited.
- **Separate long-term-memory foundation** — explicit preferences and ongoing context have
  an independent SQLite store, isolated from conversations and document evidence.
- **Conservative memory candidates** — new browser-chat messages are checked locally for
  only explicitly signaled preference/ongoing-context candidates while preserving exact
  terms. Safe candidates remain pending and expire; they are not confirmed memories.
- **Explicit memory confirmation** — an accessible card lets the user choose **Remember**
  or **Don't save**. Only approval atomically moves pending text into long-term memory;
  confirmed memory is still not injected into prompts until relevance retrieval exists.
- **Memory safety at the storage boundary** — candidates and every long-term-memory
  create/update are screened locally for labeled credentials, known token/key formats,
  likely opaque secrets, and unnecessary sensitive identifiers. Findings never echo the
  matched value; confirmation remains a separate roadmap gate.
- **Evaluation harness** — category-level retrieval, refusal, citation-integrity,
  groundedness-proxy, and per-stage latency measurements via `evaluate_rag.py`, with
  proxy limitations recorded in every report.
- **Developer-only RAG trace** — an unlinked, OpenAPI-hidden `/debug/rag` route exists
  only when `APEX_RAG_DEBUG=1`; it can show query/rank/context/gate timings plus the
  actual configured model response and real sources without writing memory. Ordinary
  chat payloads never contain candidate text.
- **Chat-first web application** — a polished ChatGPT-style interface with persistent
  conversations, history search, true token streaming, stop/regenerate/copy actions,
  attachments, drag-and-drop ingestion, model selection, source drawer, responsive
  mobile layout, and light/dark/system themes.
- **Safe Markdown and code rendering** — generated HTML is allowlisted/escaped, fenced
  code blocks have copy controls, and a strict same-origin Content Security Policy is
  applied.
- **Compatibility interfaces** — the original JSON routes, terminal chat, and preserved
  Gradio interface remain available.

## Architecture

```
apex_ai/
├── config/       Settings from env/.env (APEX_* variables), path resolution
├── core/         ApexError hierarchy (WHAT/WHY/FIX), logging, shared types
├── security/     filename sanitization, path containment, hashing
├── documents/    extraction (PDF/TXT/MD/JSON), chunker, ingestion service
├── embeddings/   EmbeddingProvider (sentence-transformers, hashing-for-tests)
├── llm/          LLMProvider: llama.cpp local, Ollama, OpenAI-compatible, transformers
├── models/       model manager: discovery + validation + selection
├── vectordb/     ChromaDB persistence, embedding-version metadata, doc registry
├── retrieval/    BM25 index, hybrid RRF pipeline, rerankers
├── rag/          query processing, context builder, prompts, RagEngine
├── memory/       bounded chat context/history + separate long-term-memory SQLite store
├── web/          offline HTML/CSS/JS chat application and responsive design system
├── ui/           preserved legacy Gradio interface
├── api/          FastAPI, NDJSON chat streaming, uploads, conversations, legacy routes
└── evaluation/   metrics + runner used by evaluate_rag.py
```

Each component has one responsibility and communicates through small dataclasses
(`Chunk`, `RetrievedChunk`, `Citation`, `AnswerResult`).

## Requirements

- Python 3.10+ (tested on 3.11)
- ~4 GB RAM for small models (more for larger GGUF files)
- A `.gguf` model file (see below). **No GPU required** — CPU-only is the default.

## Installation

```bash
git clone https://github.com/luivatan/LUIVATAN-AI.git
cd LUIVATAN-AI
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # add requirements-dev.txt for tests
cp .env.example .env             # optional: edit defaults
```

Notes:
- `llama-cpp-python` compiles on install (needs a C compiler; on Windows use
  [prebuilt wheels](https://github.com/abetlen/llama-cpp-python#installation) or use
  the Ollama provider instead, which needs no compiler).
- On machines without internet, install dependencies on a connected machine and copy
  the virtualenv, or use a wheelhouse (`pip download -r requirements.txt`).

## Model setup (offline operation)

1. Put one or more `.gguf` files into `models/` (change with `APEX_MODEL_DIR`), **or**
   point `APEX_MODEL_PATH` at any file.
2. Start the app and use the **model selector in the chat header**. Every detected
   model is listed with its size; a selection is validated before use.
3. The embedding model (`all-MiniLM-L6-v2` by default) downloads **once** into
   `data/cache/huggingface`. After that it loads fully offline.

There is no automatic model downloading at runtime and no hardcoded model path.
If a model is missing you get an error that states the exact expected path and both
ways to fix it.

Recommended starter models (GGUF, instruct-tuned): Qwen2.5 0.5B–7B Instruct,
Llama 3.2 1B/3B Instruct, Phi-3/4 mini — download from Hugging Face manually and drop
the file into `models/`.

## Running the application

```bash
python ui.py            # or: python ingest.py  or: ./launch_luivatan.sh
```

Open **http://127.0.0.1:7860**. The main screen is the chat interface. Select a model
in the header, attach or drag in documents, send a question, and expand the returned
source chips to inspect the exact evidence. Conversations are real persisted records in
`data/conversations.db`; **New chat**, history search, rename, delete, regenerate, and
stop all operate on that store. The independent memory foundation lives in
`data/long_term_memory.db`; it contains short-lived safe proposals and explicitly approved
records, but neither is read by chat generation yet.

The same process exposes API documentation at **`/api/docs`**. You can also use:

```bash
python -m apex_ai.api.server   # same chat website + API
python legacy_ui.py            # preserved pre-redesign Gradio tabs
python chat.py                 # terminal chat (add -q "question" for one-shot use)
```

See [`docs/CHAT_INTERFACE_ARCHITECTURE.md`](docs/CHAT_INTERFACE_ARCHITECTURE.md) for
the browser components, streaming event protocol, memory/evidence boundary, and upload
flow. The Phase 41 short-term-history audit and design are documented in
[`docs/PHASE41_CONVERSATION_CONTEXT.md`](docs/PHASE41_CONVERSATION_CONTEXT.md). The
separate Phase 42 persistence boundary and its deliberate non-goals are documented in
[`docs/PHASE42_LONG_TERM_MEMORY.md`](docs/PHASE42_LONG_TERM_MEMORY.md); Phase 43's
conservative, zero-write candidate extractor is covered in
[`docs/PHASE43_MEMORY_EXTRACTION.md`](docs/PHASE43_MEMORY_EXTRACTION.md), Phase 44's
storage-boundary safety policy in
[`docs/PHASE44_MEMORY_SAFETY.md`](docs/PHASE44_MEMORY_SAFETY.md), and the explicit Phase
45 approval/rejection flow in
[`docs/PHASE45_MEMORY_CONFIRMATION.md`](docs/PHASE45_MEMORY_CONFIRMATION.md).

### Web endpoints used by the interface

| Method + path | Purpose |
|---|---|
| `POST /chat/stream` | genuine `RagEngine.ask_stream()` events as NDJSON |
| `POST /chat/stop` | cooperative cancellation at the next provider token |
| `GET/POST /conversations` | history/search and New Chat records |
| `GET/PATCH/DELETE /conversations/{id}` | load, rename, or delete a conversation |
| `GET /memory/candidates` | safe pending suggestions awaiting a user decision |
| `POST /memory/candidates/{id}/approve` or `/reject` | explicit confirmation decision |
| `POST /documents/upload` | bounded multipart upload into existing ingestion |
| `GET/DELETE /documents/{id}` | knowledge-base display/removal |
| `POST /documents/{id}/reindex` | rerun existing extraction/chunk/embed pipeline |
| `GET /models`, `POST /models/select` | existing model manager |
| `GET /app-config` | non-secret runtime status for Settings |

The compatibility endpoints `/query` and `/documents/ingest` remain unchanged.

## Configuration

All configuration comes from environment variables or `.env` (see
[`.env.example`](.env.example)). Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `APEX_LLM_PROVIDER` | `llama_cpp` | `llama_cpp` / `ollama` / `openai` / `openai_compatible` / `transformers` |
| `APEX_MODEL_PATH` | *(empty)* | exact GGUF file (or pick in UI) |
| `APEX_MODEL_DIR` | `models` | directory scanned by the model manager |
| `APEX_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | embedding model (independent of the LLM) |
| `APEX_RERANKER` | `auto` | `auto` / `cross_encoder` / `lexical` / `off` |
| `APEX_TOP_K` | `12` | fused hybrid candidate pool size |
| `APEX_SEMANTIC_CANDIDATES` / `APEX_KEYWORD_CANDIDATES` | `16` / `16` | per-query channel pools |
| `APEX_RERANK_TOP_K` | `4` | maximum evidence chunks offered to context building |
| `APEX_VECTOR_WEIGHT` / `APEX_KEYWORD_WEIGHT` | `0.6` / `0.4` | weighted RRF channel influence |
| `APEX_MIN_SIMILARITY` | `0.30` | semantic part of the evidence gate |
| `APEX_QUERY_PROCESSING` / `APEX_QUERY_DECOMPOSITION` | `1` / `1` | automatic deterministic processing |
| `APEX_QUERY_REWRITE` | `0` | optional extra LLM rewrite; not required for follow-ups |
| `APEX_CONTEXT_CHAR_LIMIT` / `APEX_CONTEXT_TOKEN_RESERVE` | `6000` / `1024` | document-evidence budget and approximate model-window reserve |
| `APEX_MEMORY_TURNS` / `APEX_HISTORY_TURNS` | `8` / `3` | recent pairs requested (and JSON-retained) versus pairs eligible for one prompt |
| `APEX_HISTORY_CHAR_LIMIT` | `2400` | strict total short-term conversation-context budget |
| `APEX_HISTORY_MESSAGE_CHAR_LIMIT` | `1000` | strict limit for each prior user/assistant message |
| `APEX_RAG_DEBUG` | `0` | add developer-only trace route when explicitly enabled |
| `APEX_CHUNK_SIZE` / `_OVERLAP` / `_MIN` / `_MAX` | 1000/150/200/1600 | chunking |
| `APEX_DATABASE_PATH` | `data/chroma` | vector store location |
| `APEX_CONVERSATION_DB_PATH` | `data/conversations.db` | persistent conversation/history database |
| `APEX_LONG_TERM_MEMORY_DB_PATH` | `data/long_term_memory.db` | separate explicit preference/context store (not prompt-connected in Phase 42) |
| `APEX_MAX_UPLOAD_MB` | `50` | server-enforced size limit for each browser upload |
| `APEX_OFFLINE` | `0` | `1` = never download, fail with clear errors instead |

Legacy names from the previous project (`LLM_PROVIDER`, `LLAMA_MODEL_PATH`,
`OLLAMA_*`, `OPENAI_*`, `HF_MODEL_PATH`) still work; `APEX_*` names win.
Secrets (API keys) belong only in `.env` — never committed.

## Document ingestion

Supported: **PDF, TXT, Markdown, JSON**. Attach from chat, use the Documents page, or:

```bash
python scripts/ingest_folder.py path/to/folder
```

The pipeline: copy to `data/uploads` → SHA-256 → duplicate check → extract (pages
preserved, headers/footers and hyphenation artifacts cleaned, scanned pages detected)
→ structure-aware chunking (heading → section → paragraph) → embed → store with
`{sha256}:{seq}` chunk IDs. Every new/re-indexed chunk records `chunk_id`,
`document_id`, `document_name`/`filename`, `source`, `page`/`page_start`/`page_end`,
`section`/`section_level`, `chunk_index`, character/content hashes, schema version, and
`created_at`. Existing indexes remain readable; re-index a document to backfill the new
page-range and chunk-schema fields.

Documents can be attached from the composer, dropped anywhere on the chat, or managed
from the Documents page. The browser upload route streams through a bounded staging
area before handing the real file to this same ingestion pipeline. Documents can be
listed, re-indexed, and deleted with their associated vectors.

## Evaluation

```bash
python evaluate_rag.py                    # retrieval metrics on eval/dataset.example.jsonl
python evaluate_rag.py --with-llm         # also generate + score answers
python evaluate_rag.py --embedding hashing  # offline smoke run, no downloads
```

The bundled 19-item fixture covers direct, semantic/paraphrase, exact-match,
multi-part, negative, follow-up, multi-document, duplicate, long-query/long-document,
and multi-page retrieval. Reports include exact source/page hit and recall,
expected-document precision@candidate-k, candidate and post-rerank MRR, reranker MRR
change, evidence-gate accuracy, and stage latency. Only a run with a real configured LLM
also reports citation-payload integrity, answer-marker validity, marker-resolved source
recall, and the lexical groundedness proxy. Reports
are JSON files in `eval/reports/` and record configuration plus limitations. Precision
and reranker metrics use expected-document identity rather than human passage relevance;
context relevance and groundedness are lexical proxies, not factuality or human quality
judgments; hashing embeddings are an offline smoke-test provider, not a semantic
benchmark.

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q       # or: python -m pytest -q (testpaths is set in pyproject.toml)
```

234 tests cover extraction, page/section-safe chunking, metadata, embeddings,
vector-store operations, duplicate detection, exact and semantic retrieval, multi-query
fusion, reranker/channel fallbacks, evidence gating, budget-safe document and conversation
context, citations, configuration, memory, persistent conversation CRUD/search,
streaming/regeneration, safe uploads, developer-debug gating, live health checks,
responsive UI assets, the API, and both interface entry points. Automated tests run fully
offline with deterministic hashing embeddings and a deterministic test LLM; these verify
mechanics, not production model quality. `.github/workflows/tests.yml` runs this same
suite plus `ruff` on every push and pull request (see
[`docs/PHASE9_TESTING_FOUNDATION.md`](docs/PHASE9_TESTING_FOUNDATION.md)).

## Development

```bash
python scripts/list_models.py         # what the model manager sees
python scripts/make_test_fixtures.py  # regenerate tests/data (needs fpdf2)
```

Logging goes to `logs/apex.log` (rotating) with INFO console output; ingestion,
retrieval, model loads, and timings are logged, document *contents* are not.

New to this repository? See [`CONTRIBUTING.md`](CONTRIBUTING.md) for a beginner-friendly
setup walkthrough and the roadmap-phase development workflow this project follows.

## Troubleshooting

| Problem | Meaning / fix |
|---|---|
| `MODEL NOT FOUND` | No GGUF configured. Set `APEX_MODEL_PATH` or drop a `.gguf` into `APEX_MODEL_DIR` and select it from the chat header. The error shows the exact path checked. |
| `EMBEDDING MODEL NOT FOUND` | The embedding model isn't cached yet. Run once online, or pre-fill `data/cache/huggingface`, or pick another `APEX_EMBEDDING_MODEL`. |
| `EMBEDDING MODEL MISMATCH` | The index was built with a different embedding model. Point `APEX_EMBEDDING_MODEL` back at the old one, or rebuild the index (delete `data/chroma`, re-upload). |
| llama-cpp-python fails to install | It compiles C++. Install build tools, use a prebuilt wheel, or switch to `APEX_LLM_PROVIDER=ollama`. |
| `LLM PROVIDER ERROR: Ollama is not reachable` | Start `ollama serve` and `ollama pull <model>`, or check `APEX_OLLAMA_URL`. |
| "I couldn't find enough information…" | The retrieved context did not pass semantic-with-corroboration or conservative lexical support checks. Rephrase with exact terminology or index a source that covers the question; tune thresholds only after evaluation. |
| Old index incompatible | The pre-Apex index (`./database`, L2 space, no embedding metadata) can't be safely reused — delete it and re-ingest. |

## Limitations

- Scanned PDFs need OCR **before** upload (no built-in OCR).
- Chunk quality depends on PDF extraction quality; complex multi-column layouts and
  tables are approximated.
- The cross-encoder reranker needs its model downloaded once; if unavailable or broken,
  it falls back to lexical reranking. Whether a cross-encoder improves ranking must be
  measured on the actual corpus rather than assumed.
- Deterministic query processing is on; optional LLM rewriting remains off by default
  because it adds latency and model-dependent variability.
- Context-window budgeting uses an approximate four-characters-per-token conversion;
  provider tokenizers can differ.
- Existing chunks remain compatible but need re-indexing to gain schema-v2 page ranges.
- This stage remains a single-user local application. Authentication and subscriptions
  are intentionally deferred until the chat experience is validated; do not expose the
  server to an untrusted network yet.
- Stop generation is cooperative and takes effect at the next token yielded by the
  configured provider. A provider blocked inside a long native call cannot be interrupted
  until it yields control.
- Browser layout was built and statically tested at mobile breakpoints; use a real-device
  pass for platform-specific keyboards/safe areas before a public release.

## Project structure & history

This repository was previously **LUIVATAN AI** (a single-file medical RAG prototype).
The old `pu/` experiment folder and sample PDFs are kept on disk but no longer
tracked; the product is now **Apex AI**. See `docs/AUDIT.md` for the full audit that
motivated the redesign.
