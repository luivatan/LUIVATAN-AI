# Project Audit (Roadmap Phase 1)

This documents how LUIVATAN AI actually works today, before any further
roadmap phases change it. See `roadmap.md` for the 100-phase plan this
audit kicks off, and `docs/ARCHITECTURE.md` for the system diagram.

## What this project is

A local, offline, single-process medical-document RAG assistant:
PDF in, question in, an answer grounded in the PDF's own text, with
citations back to page/chunk. It is a personal/desktop-style tool, not a
multi-user web service — there is no authentication, no per-user data
isolation, and no hosted backend API.

## Active application (what actually runs)

- `launch_luivatan.sh` -> `python3 ingest.py` -> `ui.py` (`from ingest import launch`) -> `ingest.py:launch()`.
- `ingest.py` is the entire application: embedding, vector store, multi-provider
  LLM loading, PDF ingestion/chunking, conversation memory, and the Gradio UI
  are all defined in this one module.
- Storage:
  - `./database/` — Chroma persistent vector store (gitignored).
  - `conversation_memory.json` — flat JSON list of `{user, assistant}` turns,
    read/written on every answer (currently tracked in git — see Findings).
  - `uploaded_pdfs/` — copies of every PDF a user uploads (currently tracked
    in git — see Findings).
  - `models/` — copies of GGUF model files selected in the UI (gitignored
    as of this audit).
- LLM backends, selected via `LLM_PROVIDER`: `llama_cpp` (default, local
  GGUF file), `ollama` (local HTTP server), `openai` / `openai_compatible`
  (remote HTTP API), `transformers` (local Hugging Face pipeline). All four
  are implemented in `load_llm()`.

## Legacy / prototype code (not used by the running app)

- `chat.py` (repo root) — an earlier, single-file version of the same idea:
  hardcoded local GGUF path (`/media/shaggvt/progames/lm/...`), no citations
  abstraction, no multi-provider support. Not imported or invoked by
  `launch_luivatan.sh` or `ui.py`. Superseded by `ingest.py`.
- `pu/medical-rag/` — an earlier prototype with a different design (OCR via
  `pdf2image`/`pytesseract`, LangChain's `RecursiveCharacterTextSplitter`,
  wipes `./database` on every run). Its own `requirements.txt` doesn't even
  list the packages it imports (missing `langchain-text-splitters`,
  `pdf2image`, `pytesseract`), so it isn't currently runnable as-is. It also
  contains a hardcoded personal GGUF path, a committed 12MB `sqlite3`
  artifact, and a training PDF. It is not referenced by any active entry
  point.
- `pu/medical-rag/training-data/` — a standalone fine-tuning script
  (`train.py`, LoRA fine-tune of `mistralai/Mistral-7B-v0.1`) and a chat
  script (`rag_chat.py`). Neither is wired into the main app.

Recommendation: these are safe to keep as historical reference, but should
not be mistaken for supported code paths. A future phase should decide
whether to delete `pu/` and `chat.py` outright or move them into a clearly
labeled `archive/` directory — left untouched in this audit since deleting
files is a decision for the project owner, not something to do silently
during an audit.

## Findings from this audit

1. **`.env` was never loaded.** `python-dotenv` was already a pinned
   dependency, but nothing called `load_dotenv()`, so a `.env` file had no
   effect — every setting had to come from real process environment
   variables. Fixed in Phase 3 (`load_dotenv()` now runs at import time).
2. **No `.env.example` and no `.gitignore` entry for `.env`.** Added both,
   so future secrets (e.g. `OPENAI_API_KEY`) have a documented, safe place
   to live and can't be committed by accident.
3. **`requirements.txt` was a raw environment freeze, not a project
   manifest.** It included packages with zero relationship to this app's
   imports or its dependencies' dependencies — notably `langchain`,
   `langchain-core`, `langchain-protocol`, `langchain-text-splitters`,
   `langgraph` (+ `-checkpoint`/`-prebuilt`/`-sdk`), `langsmith`, and a
   package literally named `my-custom-module==0.1` (not a real published
   package — almost certainly local test cruft picked up by `pip freeze`
   from a shared environment). Removed in Phase 4; see the audit trail in
   git history and the header comment now at the top of `requirements.txt`.
4. **Runtime data was committed to git.** `conversation_memory.json` (real
   Q&A history) and `uploaded_pdfs/*.pdf` (user-uploaded documents) are
   tracked in the repository. For a single-user local tool this is
   low-risk today, but it is the wrong long-term pattern once real user
   data flows through this app — flagging for a future phase rather than
   silently deleting tracked files or histories.
5. **No tests existed.** All logic — including pure functions like PDF
   chunking and filename sanitization — lived inside a module that
   imports `chromadb`, `gradio`, `sentence-transformers`, and `requests`
   at import time, making it impossible to unit test without installing
   the full ML stack. Fixed in Phase 9 by extracting the pure helpers into
   `rag_utils.py` (zero third-party dependencies) with a real, passing
   `pytest` suite in `tests/`.
6. **No structured logging.** `log_error()` was a bare `print()`, and
   retrieved-chunk debugging output printed unconditionally to stdout.
   Fixed in Phase 5-6 using Python's `logging` module, gated by a new
   `LOG_LEVEL` env var, so verbose retrieval details are `DEBUG`-only.
7. **No health check.** There was no way to check, without asking a real
   question, whether the embedding model loaded, the vector database is
   reachable, the configured LLM provider is ready, or the memory file is
   readable. Added `run_health_check()` in Phase 8, exposed as a
   "Health Check" panel in the UI.
8. **No real backend API.** This is a single Gradio process calling Python
   functions directly — there is no separate REST/GraphQL layer, so
   roadmap Phase 7 ("API Structure") does not apply in its current form.
   This is a legitimate architectural choice for a local desktop tool, not
   a defect; it only becomes relevant if/when the project moves toward a
   hosted, multi-user backend (see `roadmap.md` Section 5 onward).

## Verification performed

- `python3 -m py_compile ingest.py rag_utils.py chat.py ui.py` — all
  modified/adjacent files still parse.
- `python3 -m pytest` — 14/14 tests pass covering the extracted pure
  helpers (`chunk_text`, `safe_filename`, `file_sha256`,
  `is_likely_medical_document`, `citation_label`).
- Could **not** runtime-test the Gradio UI end-to-end in this environment:
  `chromadb`, `gradio`, `sentence-transformers`, and `torch` are not
  installed here and are multi-gigabyte installs, which was impractical in
  this audit session. The refactor preserves every call site and function
  signature used by the UI layer, but a full manual smoke test (upload a
  PDF, ask a question, confirm citations) is still recommended before
  relying on this in production.
