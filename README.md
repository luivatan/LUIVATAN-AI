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
- **Structure-aware ingestion** — page numbers preserved, headings → sections →
  paragraphs, extraction-garbage cleanup, scanned/empty-page detection.
- **Smart chunking** — configurable chunk/overlap/min/max sizes, sentence-boundary
  splits, no mid-sentence cuts, section-accurate metadata.
- **Hybrid retrieval** — vector (semantic) + BM25 (exact keyword) merged with
  Reciprocal Rank Fusion.
- **Optional reranking** — cross-encoder if available, offline lexical fallback
  otherwise, or off. The app never breaks because a reranker is missing.
- **Grounded generation** — the LLM must answer from the numbered evidence blocks,
  distinguish evidence from inference, and say when evidence is insufficient. Below a
  configurable similarity threshold Apex AI refuses instead of guessing.
- **Honest citations** — sources are built only from chunks actually sent to the
  model, with SOURCE / PAGE / SECTION headers and a source viewer in the UI.
- **Duplicate protection** — SHA-256 document IDs; re-uploading the same file is
  detected and skipped (or force re-indexed).
- **Separated memory** — conversation memory exists only to resolve follow-ups; it is
  never treated as document evidence and can never be cited.
- **Evaluation harness** — deterministic retrieval metrics via `evaluate_rag.py`.
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
├── memory/       single-turn compatibility memory + SQLite conversation history
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
stop all operate on that store.

The same process exposes API documentation at **`/api/docs`**. You can also use:

```bash
python -m apex_ai.api.server   # same chat website + API
python legacy_ui.py            # preserved pre-redesign Gradio tabs
python chat.py                 # terminal chat (add -q "question" for one-shot use)
```

See [`docs/CHAT_INTERFACE_ARCHITECTURE.md`](docs/CHAT_INTERFACE_ARCHITECTURE.md) for
the browser components, streaming event protocol, memory/evidence boundary, and upload
flow.

### Web endpoints used by the interface

| Method + path | Purpose |
|---|---|
| `POST /chat/stream` | genuine `RagEngine.ask_stream()` events as NDJSON |
| `POST /chat/stop` | cooperative cancellation at the next provider token |
| `GET/POST /conversations` | history/search and New Chat records |
| `GET/PATCH/DELETE /conversations/{id}` | load, rename, or delete a conversation |
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
| `APEX_TOP_K` | `12` | hybrid candidate pool size |
| `APEX_RERANK_TOP_K` | `4` | evidence chunks sent to the LLM |
| `APEX_MIN_SIMILARITY` | `0.30` | below this → "not enough information" |
| `APEX_CHUNK_SIZE` / `_OVERLAP` / `_MIN` / `_MAX` | 1000/150/200/1600 | chunking |
| `APEX_DATABASE_PATH` | `data/chroma` | vector store location |
| `APEX_CONVERSATION_DB_PATH` | `data/conversations.db` | persistent conversation/history database |
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
`{sha256}:{seq}` chunk IDs. Every chunk metadata includes `document_id`,
`document_name`, `source`, `page`, `section`, `chunk_index`, `created_at`.

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

Metrics: `source_hit_rate`, `page_hit_rate`, `first_hit_rate`, context relevance,
insufficient-evidence rate, and (with `--with-llm`) a groundedness proxy. Reports are
JSON files in `eval/reports/`. These are heuristic measurements over your dataset —
the script reports raw numbers and makes no performance claims.

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

106 tests cover extraction, chunking, metadata, embeddings, vector-store operations,
duplicate detection, retrieval, reranking, provider errors, missing-model errors,
citations, configuration, memory, persistent conversation CRUD/search, streaming and
regeneration, safe browser uploads, responsive UI assets, the API, and both interface
entry points. Automated tests run fully offline with deterministic hashing embeddings
and a deterministic test LLM; the documented manual smoke test uses the configured
real provider.

## Development

```bash
python scripts/list_models.py         # what the model manager sees
python scripts/make_test_fixtures.py  # regenerate tests/data (needs fpdf2)
```

Logging goes to `logs/apex.log` (rotating) with INFO console output; ingestion,
retrieval, model loads, and timings are logged, document *contents* are not.

## Troubleshooting

| Problem | Meaning / fix |
|---|---|
| `MODEL NOT FOUND` | No GGUF configured. Set `APEX_MODEL_PATH` or drop a `.gguf` into `APEX_MODEL_DIR` and select it from the chat header. The error shows the exact path checked. |
| `EMBEDDING MODEL NOT FOUND` | The embedding model isn't cached yet. Run once online, or pre-fill `data/cache/huggingface`, or pick another `APEX_EMBEDDING_MODEL`. |
| `EMBEDDING MODEL MISMATCH` | The index was built with a different embedding model. Point `APEX_EMBEDDING_MODEL` back at the old one, or rebuild the index (delete `data/chroma`, re-upload). |
| llama-cpp-python fails to install | It compiles C++. Install build tools, use a prebuilt wheel, or switch to `APEX_LLM_PROVIDER=ollama`. |
| `LLM PROVIDER ERROR: Ollama is not reachable` | Start `ollama serve` and `ollama pull <model>`, or check `APEX_OLLAMA_URL`. |
| "I couldn't find enough information…" | Working as intended: retrieval confidence was below `APEX_MIN_SIMILARITY`. Lower it, rephrase, or index better-matching documents. |
| Old index incompatible | The pre-Apex index (`./database`, L2 space, no embedding metadata) can't be safely reused — delete it and re-ingest. |

## Limitations

- Scanned PDFs need OCR **before** upload (no built-in OCR).
- Chunk quality depends on PDF extraction quality; complex multi-column layouts and
  tables are approximated.
- The cross-encoder reranker needs its model downloaded once; offline it falls back
  to lexical reranking.
- Query rewriting is off by default (extra LLM latency when on).
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
