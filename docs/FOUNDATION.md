# Apex AI — Foundation & Planning (Phases 1–10)

This document records the baseline audit and the decisions that should guide the Apex AI work. The current repository is a small Python/Gradio prototype; Apex AI should evolve it without losing its privacy-first, document-grounded behavior.

## 1. Audit of existing code

| Area | Current state | Assessment |
|---|---|---|
| UI | `ingest.py` builds a Gradio Blocks interface; `ui.py` is a launcher | Functional prototype, but UI construction happens at import time |
| Ingestion | `pypdf` extracts text, then chunks and embeds it | Text PDFs work; scanned PDFs require OCR; upload copies are not validated by extension |
| Retrieval | Chroma persistent collection, top five semantic matches | No score threshold, tenant/session separation, or delete/re-index workflow |
| Generation | llama.cpp, Ollama, OpenAI-compatible, and Transformers adapters | Good provider coverage; model loading is lazy except the embedding model |
| Memory | JSON list with an eight-turn prompt window | Corrupt files are tolerated, but writes are non-atomic and shared globally |
| Sources | Citations and a latest-answer source viewer | Useful baseline; `last_source_texts` is process-global |
| Operations | shell launcher and one unpinned environment dump | No test suite, environment template, CI, or health checks |

## 2. Architecture map

```text
ui.py -> ingest.launch()
          ├─ Gradio event handlers
          ├─ PDF extraction/chunking -> SentenceTransformer -> Chroma
          ├─ question -> SentenceTransformer -> Chroma query
          ├─ retrieved context + JSON conversation memory -> provider adapter
          └─ answer + citations + source viewer

Storage: ./database (Chroma), ./uploaded_pdfs (PDF copies), conversation_memory.json
Providers: llama_cpp | ollama | openai_compatible/openai | transformers
```

## 3. Apex AI requirements

* Answer only from retrieved document context and clearly communicate uncertainty.
* Preserve citations to document, page, and chunk for every grounded answer.
* Keep local/offline operation as the default; remote providers must be explicit.
* Never expose API keys in logs, prompts, or the UI.
* Make ingestion deterministic and repeatable; duplicate uploads must not create duplicate chunks.
* Handle malformed, empty, unsupported, and scanned PDFs with actionable errors.
* Keep model and embedding initialization lazy so tests and health checks do not download models.
* Provide isolated development/test storage and reproducible dependency installation.
* Add automated tests before feature expansion, with medical safety language in the product UI.

## 4. Existing features

PDF upload and indexing, semantic retrieval, four LLM backends, GGUF selection, OCR guidance, persistent conversation memory, source citations/viewer, document library, medical-content warning, and local-first configuration.

## 5. Known bugs and risks

1. `chat.py` is a separate, unusable CLI with a hard-coded absolute model path and import-time model loading; it can be mistaken for the supported entry point.
2. `ingest.py` imports and initializes Chroma and the embedding model at module import, making tests and missing-dependency diagnostics unnecessarily expensive.
3. `save_memory()` can leave a truncated JSON file if interrupted and has no concurrency protection.
4. `safe_filename()` strips path separators but does not reject empty names or normalize collisions.
5. Every embedding is calculated one at a time during ingestion; large PDFs will be slow.
6. Retrieval has no minimum similarity/distance policy, so irrelevant context may be presented as evidence.
7. API and model errors are returned to users verbatim, which can leak provider/network details.
8. `OLLAMA_URL` defaults to localhost, which is appropriate for local execution but must never be used directly by browser code in a hosted preview.
9. No automated tests or CI currently protect chunking, citations, memory, or provider selection.

## 6. Dependency cleanup decision

`requirements.txt` previously contained a full machine-generated environment, including CUDA packages, transitive libraries, and `my-custom-module`. It should contain only direct runtime dependencies; optional providers belong in extras. The replacement baseline is intentionally CPU-friendly and leaves GPU acceleration to the install environment.

## 7. Target structure

```text
apex_ai/                 # application modules (configuration, ingestion, retrieval, providers)
tests/                   # fast unit tests; no model/network access
docs/FOUNDATION.md    # planning and audit records
.env.example             # documented configuration contract
ui.py                    # thin entry point
```

The current monolithic module remains compatible during this foundation phase; extraction into `apex_ai/` is the next implementation phase. The provider abstraction now lives in `apex_llm.py` and is ready for that extraction.

## 8–9. Environment strategy

* `.env.example` documents all supported settings; `.env` is local-only.
* Development uses the repository-local `database/`, `uploaded_pdfs/`, and memory file.
* Tests must set temporary paths and mock embedding/LLM/provider boundaries; no network, model download, or real user documents.
* Production packaging should supply paths outside the source tree and set an explicit provider.

## 10. Baseline test plan

Initial tests cover safe filenames, chunk overlap/termination, medical-document heuristic behavior, citation formatting, memory formatting, and provider validation. Integration tests will be added once the monolith is split and dependencies become injectable.
