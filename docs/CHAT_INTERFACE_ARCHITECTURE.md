# Apex AI chat-interface architecture

## Scope

This interface is an application-experience layer over the established Apex AI
backend. It does **not** replace `RagEngine`, `HybridRetriever`, the reranker, ChromaDB,
embedding providers, or any `LLMProvider` implementation.

```text
Browser (HTML/CSS/vanilla JS)
    │
    ├── GET/POST /conversations ──> ConversationStore (SQLite)
    ├── POST /documents/upload ───> existing IngestionService ──> ChromaDB
    ├── GET/POST /models ─────────> existing ModelManager / provider registry
    └── POST /chat/stream (NDJSON)
             │
             ├── ConversationMemoryAdapter (history only, never evidence)
             └── existing RagEngine
                    ├── QueryProcessor
                    ├── HybridRetriever (vector + BM25)
                    ├── Reranker
                    ├── ContextBuilder
                    └── existing LLMProvider.stream()
                             │
                             └── tokens + final verified citations
```

## Browser components

The browser application is deliberately dependency-free and works without a CDN:

- **Conversation sidebar** — persisted history, current selection, rename, delete, and
  server-side text search.
- **Chat canvas** — real saved user/assistant messages, streamed token updates,
  evidence citations, empty state, copy, regenerate, and stop.
- **Composer** — auto-growing text area, keyboard behavior, attachment queue, click
  upload, and page-wide drag/drop.
- **Document page** — actual indexed-document registry with upload, re-index, and
  vector deletion controls.
- **Settings page** — light/dark/system appearance and local chat preferences plus
  live, non-secret backend status.
- **Source drawer** — displays the exact chunk used by the RAG context, not generated
  citation text.

CSS uses a tokenized light/dark design system and responsive breakpoints at 900, 720,
and 420 px. At mobile width, the sidebar becomes an accessible off-canvas panel and
the citation viewer becomes a bottom sheet.

## Streaming protocol

`POST /chat/stream` returns `application/x-ndjson`; each line is one JSON event:

1. `meta` — real conversation and user-message identifiers.
2. `token` — one token/text delta from `RagEngine.ask_stream()`.
3. `final` — persisted assistant message, confidence, and citations.
4. `stopped` — optional partial message explicitly marked `stopped`.
5. `error` — an actionable backend error; never a fabricated model response.

NDJSON is used rather than WebSockets because it works with ordinary HTTP streaming,
reverse proxies, FastAPI `StreamingResponse`, and the existing synchronous local-model
iterator. `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no` prevent
common proxies from batching tokens.

## Conversation memory and evidence separation

`ConversationStore` stores multiple conversations in SQLite. For a selected chat,
`ConversationMemoryAdapter` exposes completed prior turns in the interface expected by
`RagEngine`. It is intentionally read-only during generation; the controller persists
the final answer and its citations together.

Conversation text remains prompt history only. It never enters the numbered evidence
context and can never become a citation. Citations are serialized exclusively from the
`AnswerResult.citations` produced by the existing engine.

## Stop and regenerate behavior

- The browser creates a request ID for every generation.
- `GenerationManager` allows one active response per conversation.
- `POST /chat/stop` sets a thread-safe event.
- The stream controller closes the existing engine iterator at the next token boundary
  and stores partial output as `stopped`, never `complete`.
- Regeneration reuses the last persisted user question. The prior answer is replaced
  only after new output is available, so a backend failure does not silently erase it.

## Upload path

The browser sends multipart data to `/documents/upload`. The controller:

1. sanitizes the filename and checks the extension,
2. streams it into an isolated staging directory,
3. rejects it if it exceeds `APEX_MAX_UPLOAD_MB`,
4. calls the existing `IngestionService.ingest_path()`, and
5. always removes the staging directory.

The ingestion service still performs extraction, hashing, duplicate detection,
structure-aware chunking, embedding, and ChromaDB persistence. Same-named files with
different content receive a short hash suffix instead of overwriting an older managed
file.

## Markdown security

Assistant Markdown is rendered locally with a small allowlist renderer. Raw HTML is
escaped first; only headings, emphasis, lists, blockquotes, HTTP(S) links, inline code,
and fenced code blocks are emitted. This prevents model output from injecting scripts.
Code blocks are rendered as preformatted text with their own copy button. A strict
same-origin Content Security Policy blocks remote scripts, frames, plugins, and assets.

## Compatibility

- `python ui.py` and `python ingest.py` launch the new chat interface.
- `python legacy_ui.py` launches the preserved Gradio interface.
- `/query`, `/documents`, `/documents/ingest`, and `/models` remain backward-compatible.
- `python chat.py`, evaluation, batch ingestion, and all provider classes are unchanged.
