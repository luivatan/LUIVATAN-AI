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
- **Document collections** (Phase 66/67) — group documents into named knowledge bases and
  scope a conversation's retrieval to one of them; moving a document between collections
  is a registry update only, never a re-embed.
- **Project workspaces** (Phase 71/72) — group conversations under a shared name,
  instructions, and a linked collection; a project's instructions are woven into every
  prompt in its conversations as clearly separated, never-cited guidance, and its linked
  collection governs retrieval the same way a standalone collection does.
- **Tool-calling abstraction** (Phase 73) — a safe, bounded `ToolRegistry` execution
  boundary and an opt-in `LLMProvider.generate_with_tools()` capability (real for the
  OpenAI-compatible provider; every other provider honestly reports no support rather
  than simulating one). Not yet wired into live chat — see `docs/PHASE73_TOOL_ARCHITECTURE.md`.
- **Bounded conversation context** — newest complete turns are selected under configurable
  turn, total-character, and per-message limits. History helps resolve follow-ups but is
  never treated as document evidence and can never be cited.
- **Separate long-term-memory foundation** — explicit preferences and ongoing context have
  an independent SQLite store, isolated from conversations and document evidence.
- **Conservative memory candidates** — new browser-chat messages are checked locally for
  only explicitly signaled preference/ongoing-context candidates while preserving exact
  terms. Safe candidates remain pending and expire; they are not confirmed memories.
- **Explicit memory confirmation** — an accessible card lets the user choose **Remember**
  or **Don't save**. Only approval atomically moves pending text into long-term memory.
- **Relevant memory retrieval** — confirmed preferences (how to answer) and
  keyword-relevant ongoing context (what the user is currently doing) are selected per
  question and added to the prompt as a clearly separated, never-cited "user context"
  block — never document evidence, never a citation source.
- **Memory management** — a Settings panel lists every confirmed memory with delete and
  clear-all controls, backed directly by the same store (not the proposal workflow).
- **Memory conflict warnings** — a new memory candidate that looks like it may
  contradict an existing one of the same kind is flagged on the confirmation card;
  nothing is auto-deleted or overwritten, the user decides.
- **Long-conversation summaries** (opt-in, `APEX_CONVERSATION_SUMMARY=1`) — turns that
  fall out of the live short-term window get folded into a rolling per-conversation
  summary instead of silently disappearing, added to the prompt the same way
  conversation history is: context only, never evidence, never cited.
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
  conversations, history search, true token streaming, stop/regenerate/copy/feedback
  actions, attachments, drag-and-drop ingestion, model selection, source drawer,
  responsive mobile layout, and light/dark/system themes.
- **Safe Markdown and code rendering** — generated HTML is allowlisted/escaped;
  headings, lists, tables, and links render alongside fenced code blocks with
  dependency-free syntax highlighting and copy controls; a strict same-origin Content
  Security Policy is applied.
- **Real accounts** — Argon2id password hashing, server-side sessions, sign-up/sign-in
  at `/login`. A single local machine needs no login screen by default (an
  auto-provisioned local account); a real sign-in always takes precedence.
  Conversations, long-term memory, and uploaded documents (vector index, keyword
  index, and upload directory) are all fully isolated per account (Phase 54/55).
  Uploaded files and the vector database get owner-only filesystem permissions
  (Phase 57), and the API applies an in-memory per-client rate limit with a
  stricter budget on `/auth/login`/`/auth/signup` (Phase 58).
- **Compatibility interfaces** — the original JSON routes, terminal chat, and preserved
  Gradio interface remain available.

## Architecture

```
apex_ai/
├── config/       Settings from env/.env (APEX_* variables), path resolution
├── core/         ApexError hierarchy (WHAT/WHY/FIX), logging, shared types
├── security/     filename sanitization, path containment, hashing
├── auth/         accounts, Argon2id password hashing, sessions (Phase 51/52)
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
records. Confirmed records are relevance-filtered per question and included in the prompt
as a clearly separated, never-cited "user context" block (`APEX_MEMORY_PROMPT_USE=1` by
default); pending proposals are never read by chat generation.

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
| `GET/POST /conversations` | history/search and New Chat records; `POST` accepts an optional `collection_id` (Phase 67) or `project_id` (Phase 71) to scope retrieval; `GET` accepts an optional `project_id` filter |
| `GET/PATCH/DELETE /conversations/{id}` | load, rename, or delete a conversation |
| `PATCH /conversations/{id}/collection` | change (or clear) which knowledge-base collection a conversation is scoped to |
| `PATCH /conversations/{id}/project` | move a conversation into (or out of) a project (Phase 71) |
| `GET/POST /projects`, `PATCH/DELETE /projects/{id}` | project workspace CRUD — name, instructions, and a linked collection (Phase 71/72) |
| `GET /memory/candidates` | safe pending suggestions awaiting a user decision |
| `POST /memory/candidates/{id}/approve` or `/reject` | explicit confirmation decision |
| `POST /documents/upload` | bounded multipart upload into existing ingestion; accepts an optional `collection_id` form field |
| `GET/DELETE /documents/{id}` | knowledge-base display/removal |
| `PATCH /documents/{id}/collection` | move a document into (or out of) a collection — a registry update only, no re-embedding |
| `POST /documents/{id}/reindex` | rerun existing extraction/chunk/embed pipeline |
| `GET/POST /collections`, `PATCH/DELETE /collections/{id}` | named document-collection CRUD (Phase 66) |
| `GET /models`, `POST /models/select` | existing model manager |
| `GET /app-config` | non-secret runtime status for Settings |

The compatibility endpoints `/query` and `/documents/ingest` keep their request/response
shape unchanged. `/documents/ingest` now requires a user like every other document route
(falling back to the default local account under `APEX_AUTO_LOGIN_LOCAL=1`, same as the
rest of the app); `/query` alone stays genuinely ungated — it always reads and writes the
default local account's data through the one singleton engine built at startup, the same
boundary documented below.

## Accounts

Apex AI has real accounts, Argon2id password hashing, and server-side sessions
(`data/users.db`), added in Phase 51/52. By default (`APEX_AUTO_LOGIN_LOCAL=1`) a
single machine running Apex AI for one person needs no login screen: an
unauthenticated browser request is transparently treated as an auto-provisioned
default local account. Visit `/login` any time to create a real account or sign in
as one explicitly — an explicit login always takes precedence over the local
default. Set `APEX_AUTO_LOGIN_LOCAL=0` to require real sign-in for every request.

| Method + path | Purpose |
|---|---|
| `POST /auth/signup` | create an account (email + password ≥ 8 chars), sets the session cookie |
| `POST /auth/login` | sign in, sets the session cookie |
| `POST /auth/logout` | invalidate the session server-side and clear the cookie |
| `GET /auth/me` | current user (falls back to the default local account when `APEX_AUTO_LOGIN_LOCAL=1`) |

Conversations, long-term memory, and documents are all scoped per account
(Phase 54/55): every store method checks ownership, and a missing/mismatched
owner is always treated as "not found," never a distinct "forbidden" signal.
Two accounts uploading byte-identical files each get their own indexed copy
(the ChromaDB vector store, the BM25 keyword index, and the upload directory
are all partitioned by account) rather than silently sharing one — see
[`docs/PHASE54-55_AUTHORIZATION_AND_ISOLATION.md`](docs/PHASE54-55_AUTHORIZATION_AND_ISOLATION.md)
for the full design. Project isolation (Phase 56) stays out of scope, same as
Phase 48 — there is no project/workspace data model anywhere yet to isolate.

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
| `APEX_MEMORY_PROMPT_USE` | `1` | inject relevance-filtered confirmed long-term memory into prompts (Phase 47) |
| `APEX_CONVERSATION_SUMMARY` | `0` | summarize turns that fall out of the live short-term window (Phase 50; extra LLM call) |
| `APEX_RAG_DEBUG` | `0` | add developer-only trace route when explicitly enabled |
| `APEX_CHUNK_SIZE` / `_OVERLAP` / `_MIN` / `_MAX` | 1000/150/200/1600 | chunking |
| `APEX_MAX_DOCUMENT_PAGES` | `2000` | reject a PDF with more pages than this before extracting any text (Phase 70) |
| `APEX_DATABASE_PATH` | `data/chroma` | vector store location |
| `APEX_CONVERSATION_DB_PATH` | `data/conversations.db` | persistent conversation/history database |
| `APEX_LONG_TERM_MEMORY_DB_PATH` | `data/long_term_memory.db` | separate explicit preference/context store; see `APEX_MEMORY_PROMPT_USE` |
| `APEX_USERS_DB_PATH` | `data/users.db` | accounts + sessions (Phase 51/52) |
| `APEX_COLLECTIONS_DB_PATH` | `data/collections.db` | named document-collection labels (Phase 66) |
| `APEX_PROJECTS_DB_PATH` | `data/projects.db` | project workspaces — name, instructions, a linked collection (Phase 71) |
| `APEX_SESSION_COOKIE_NAME` / `APEX_SESSION_TTL_DAYS` | `apex_session` / `30` | session cookie name and lifetime |
| `APEX_AUTO_LOGIN_LOCAL` | `1` | `0` = require real sign-in for every request instead of the default-local-account fallback |
| `APEX_MAX_UPLOAD_MB` | `50` | server-enforced size limit for each browser upload |
| `APEX_RATE_LIMIT_ENABLED` / `APEX_RATE_LIMIT_PER_MINUTE` | `1` / `120` | in-memory per-client-IP request budget (Phase 58) |
| `APEX_AUTH_RATE_LIMIT_PER_MINUTE` | `10` | stricter budget for `/auth/login` and `/auth/signup` |
| `APEX_CORS_ALLOWED_ORIGINS` | *(empty)* | comma-separated allowed origins; empty = no CORS headers at all |
| `APEX_OFFLINE` | `0` | `1` = never download, fail with clear errors instead |

Legacy names from the previous project (`LLM_PROVIDER`, `LLAMA_MODEL_PATH`,
`OLLAMA_*`, `OPENAI_*`, `HF_MODEL_PATH`) still work; `APEX_*` names win.
Secrets (API keys) belong only in `.env` — never committed.

### Secret management (Phase 59)

`APEX_OPENAI_API_KEY` is the only runtime secret Apex AI has today (local
llama.cpp needs none; Ollama's default local URL needs none; session identity
is an opaque random token, not a signed/keyed scheme, so there is no signing
secret either). It is read once from the environment
(`apex_ai/config/settings.py`), never logged, and excluded from `Settings`'s
own `repr()` (`test_api_key_is_redacted_from_settings_repr` in
`tests/test_config.py` enforces this) — so it cannot leak into logs, error
messages, or a debug dump of the running configuration by accident.

For local development, `.env` (gitignored; `.env.example` documents every
variable with a placeholder, never a real value) is sufficient. For a real
deployment, set `APEX_OPENAI_API_KEY` from your platform's own secret
storage instead of a checked-in file — Apex AI needs no code changes for
this, since every secret manager's standard integration point is injecting
environment variables into the process at startup:

- **Docker / Docker Compose** — `docker run --env-file` pointed at a file
  outside version control, or [Docker secrets](https://docs.docker.com/engine/swarm/secrets/)
  mounted and exported by your entrypoint script.
- **Kubernetes** — a `Secret` object consumed via `envFrom`/`env.valueFrom.secretKeyRef`
  on the container spec.
- **systemd** — `EnvironmentFile=/etc/apex-ai/secrets.env` (mode `600`,
  owned by the service user) in the unit file.
- **Cloud providers** — AWS Secrets Manager, GCP Secret Manager, Railway,
  Render, Fly.io, and similar all offer "inject as environment variable"
  for a deployed service; point it at `APEX_OPENAI_API_KEY`.

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
- Real accounts, password hashing, and sessions exist (Phase 51/52 — see Accounts
  below). Conversations, long-term memory, and documents (vector store, BM25 index,
  upload directory) are all isolated per account (Phase 54/55). The `/query`
  compatibility endpoint is the one deliberate exception: it always reads and writes
  the auto-provisioned default local account's data through a single engine built
  at startup, by design (see Accounts) — do not expose it on a shared/multi-account
  deployment expecting per-caller isolation. Subscriptions remain fully deferred.
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
