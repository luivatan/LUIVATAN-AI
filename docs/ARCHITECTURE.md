# Architecture Map (Roadmap Phase 2)

LUIVATAN AI is a single Python process: one Gradio UI, calling plain
functions in the same module, with no network-separated backend. There is
no authentication layer and no multi-user database — see `docs/AUDIT.md`
for why, and `roadmap.md` for where that changes in later phases.

## Component diagram

```
                        ┌─────────────────────────────┐
                        │        Gradio UI             │
                        │        (ingest.py)           │
                        │  - Select GGUF model         │
                        │  - Upload PDF                │
                        │  - Ask question               │
                        │  - View sources / memory      │
                        │  - Health check panel         │
                        └───────────────┬──────────────┘
                                        │ direct function calls
                                        │ (no HTTP hop, same process)
        ┌───────────────────┬──────────┴───────────┬───────────────────┐
        ▼                   ▼                       ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────────────┐  ┌──────────────┐
│ PDF ingestion │   │ Embeddings    │   │ LLM provider           │  │ Conversation │
│ (rag_utils.py)│   │ sentence-      │   │ abstraction            │  │ memory       │
│ - extract text│   │ transformers   │   │ (ingest.py:load_llm)   │  │ (JSON file)  │
│ - chunk_text  │   │ all-MiniLM-L6- │   │ - llama_cpp (local     │  │              │
│ - safe_filename│  │ v2             │   │   GGUF)                │  │              │
│ - sha256 hash │   └───────┬───────┘   │ - ollama (local HTTP)  │  └──────┬───────┘
└───────┬───────┘           │           │ - openai/compatible    │         │
        │                   │           │   (remote HTTP)         │         │
        │                   │           │ - transformers (local  │         │
        │                   │           │   HF pipeline)          │         │
        │                   │           └────────────┬───────────┘         │
        ▼                   ▼                        │                     │
┌─────────────────────────────────┐                  │                     │
│      Chroma vector store         │                  │                     │
│      (./database, persistent)    │                  │                     │
│  - one "medical_docs" collection │                  │                     │
│  - metadata: source, source_hash,│                  │                     │
│    page, chunk, is_likely_medical│                  │                     │
└─────────────────┬─────────────────┘                  │                     │
                  │ query() top-5 by embedding distance │                     │
                  ▼                                     ▼                     │
          ┌───────────────────────────────────────────────────┐              │
          │              Prompt assembly (ask_ai)               │◄─────────────┘
          │  conversation memory + retrieved chunks + question  │
          └───────────────────────┬─────────────────────────────┘
                                  ▼
                        answer + [n] citations
                                  │
                                  ▼
                   appended back into conversation memory
```

## Request flow: asking a question

1. User types a question in the Gradio textbox and clicks **Ask** (or
   presses enter).
2. `ask_ai()` embeds the question with the same `all-MiniLM-L6-v2` model
   used at ingestion time.
3. The embedding is queried against the Chroma `medical_docs` collection
   for the top 5 nearest chunks (`n_results=5`).
4. `format_retrieved_context()` turns those chunks into numbered citation
   blocks (`[1] filename Page X Chunk Y`) and logs the full retrieval
   detail at `DEBUG` level (not printed by default — see `LOG_LEVEL`).
5. The prompt is assembled from: recent conversation memory (last 8 turns),
   the retrieved chunks, and the question, with an explicit instruction to
   answer only from the retrieved evidence and to cite `[n]`.
6. `get_answer_generator()` lazily loads (and caches) the configured LLM
   backend, then generates the answer.
7. The answer, with a `Sources:` list appended, is shown to the user, saved
   into `conversation_memory.json`, and the first source's full text is
   shown in the Source Viewer.

## Request flow: uploading a PDF

1. User uploads a PDF via the Gradio file control.
2. `index_uploaded_pdf()` copies it into `uploaded_pdfs/` under a
   sanitized filename (`safe_filename()` strips any directory components,
   which also blocks path traversal from a crafted filename).
3. `file_sha256()` hashes the saved file (used as a stable per-document ID
   prefix so re-uploading the same file re-indexes it deterministically).
4. `extract_pdf_pages()` (via `pypdf`) pulls text per page; if no page has
   extractable text (e.g. a scanned PDF with no OCR), it raises rather than
   silently indexing nothing.
5. `is_likely_medical_document()` does a simple keyword-count heuristic and
   surfaces a warning banner if the document doesn't look medical — it
   does not block the upload.
6. Each page is split with `chunk_text()` (1000 chars, 150-char overlap),
   embedded, and upserted into Chroma with metadata (`source`,
   `source_hash`, `page`, `chunk`, `is_likely_medical`).

## Environment / configuration surface

All configuration is environment variables, loaded from a real `.env` file
(via `python-dotenv`, wired up in Phase 3) or the process environment —
see `.env.example` for the full list: `LLM_PROVIDER`, `LLAMA_MODEL_PATH`,
`LLM_CONTEXT_SIZE`, `OLLAMA_URL`, `OLLAMA_MODEL`, `OPENAI_API_BASE`,
`OPENAI_API_KEY`, `OPENAI_MODEL`, `HF_MODEL_PATH`, `LOG_LEVEL`. No secret
is ever hardcoded in source.

## Explicitly out of scope today

- **No REST/GraphQL API.** The UI calls Python functions directly in the
  same process. Roadmap Phase 7 ("API Structure") doesn't apply until a
  separate backend is introduced.
- **No authentication or multi-user isolation.** One shared
  `conversation_memory.json` and one shared Chroma collection for whoever
  runs the app. Roadmap Section 5 ("Users, Authentication & Security")
  covers introducing this.
- **No billing/subscription system, no production deployment target.**
  This is a local/offline tool today (see `README.md`); those are later
  roadmap sections (8 and 9).
