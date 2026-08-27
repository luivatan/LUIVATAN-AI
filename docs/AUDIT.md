# Apex AI — Internal Audit (Phase 1)

Date: 2026-08-27 · Auditor: agent session on `arena/01a042f1-luivatan-ai`
Scope: full repository as of commit `cb321bb` ("Added PDF library").

## 1. What exists today

| File | Role | Verdict |
|---|---|---|
| `ingest.py` (749 lines) | The whole app: config + embeddings + 4 LLM backends + ChromaDB + memory + PDF indexing + retrieval + Gradio UI | Core asset. Works, but is a monolith. |
| `ui.py` (5 lines) | Entry point, imports `launch` from `ingest` | Keep as shim. |
| `chat.py` (86 lines) | Terminal chat loop | Broken on any other machine (hardcoded model path, top-level `llama_cpp` import). |
| `launch_luivatan.sh` | Runs `python3 ingest.py` | Keep, update target. |
| `pu/medical-rag/` | Older experiment: OCR ingestion (pdf2image+pytesseract), LoRA training scripts, 5.9 MB PDF, 12 MB `sqlite3` file that is actually a PostScript image | Experiment folder — not wired into the app. |
| `uploaded_pdfs/` | 2 sample PDFs (3.6 MB) committed to git | Runtime data; should not be tracked. |
| `conversation_memory.json` | Real conversation history committed to git | Privacy problem. |
| `requirements.txt` | 150-line `pip freeze` dump | Mostly uninstallable/bloated: CUDA 13 wheels, `kubernetes`, `my-custom-module==0.1` (local-only package → `pip install -r` fails on any other machine), langchain/langgraph (unused by the app). |
| `README` (74 lines) + `README.md` (1 line) | Split, inconsistent docs | Consolidate. |

## 2. What works and must be preserved

1. **Multi-provider LLM design** in `ingest.py`: `llama_cpp` / `ollama` / `openai` / `openai_compatible` / `transformers`, lazy loading, cached by a provider key. This is the seed of the provider abstraction — keep the idea, formalize the interface.
2. **UI-based GGUF selection** (upload → copy to `models/`) — keep, extend into a model manager that scans a directory.
3. **SHA-256-based chunk IDs** (`{hash}:p{page}:c{n}`) + `upsert` → natural duplicate protection. Keep, generalize.
4. **Page numbers preserved** in chunk metadata; scanned-PDF detection with a clear message.
5. **Citation list + source viewer** in the UI — good UX idea, keep and make citations strictly evidence-derived.
6. **Persistent conversation memory** with a turn limit.
7. **Medical-document heuristic** (`is_likely_medical_document`) with a scope warning.
8. **Friendly missing-package messages.**

## 3. Problems found

### Portability (breaks on another computer)
- `chat.py` and `pu/medical-rag/app.py` hardcode `/media/shaggvt/progames/lm/qwen2.5-coder-7b-instruct-q4_k_m.gguf`.
- `pu/medical-rag/app.py` **deletes `./database` on every run** (destroys the index) and assigns sequential integer chunk IDs (collide across runs).
- Paths like `./database`, `uploaded_pdfs` resolve against the **current working directory**, not the project root.
- `my-custom-module==0.1` in requirements is machine-local; the freeze-dump includes CUDA wheels that assume an NVIDIA box.

### Security / repo hygiene
- Personal conversation history committed (`conversation_memory.json`).
- Uploads and an 18 MB experiment dataset tracked in git; `.gitignore` lacks `.env`, `logs/`, `cache/`, `data/`.
- `.continue/agents/new-config.yaml` contains an API-key placeholder pattern (no real key present).

### RAG quality / hallucination risks
- **Blind character chunking**: `" ".join(text.split())` flattens paragraphs/headings, splits sentences mid-way; chunk size/overlap hardcoded; chunking per page separates headings from their sections.
- **Vector-only retrieval**, no keyword stage, no reranking, no similarity threshold → irrelevant chunks still reach the prompt; the prompt says "explain what was found" instead of "say when evidence is insufficient".
- **Embedding model identity not stored** in collection metadata → switching embedding models silently corrupts retrieval.
- **Citations list every retrieved chunk** whether or not the answer used it (over-claims sourcing).
- Old collection uses Chroma's default L2 space; cosine is the right space for normalized sentence embeddings.
- No context budget: all 5 chunks always sent, regardless of size or the model's context window.

### Duplication
- Embedding model constructed in 3 files; LLM loading duplicated in 3 files (2 with the same hardcoded path); Chroma setup duplicated 3×; 3 near-identical prompt templates.

### Error handling / operations
- No logging (only `print`), no timing, errors stringified into chat boxes.
- Model-not-found message doesn't say *where* the app looked or *how* to fix it.
- No tests, no evaluation harness.

## 4. Decisions

1. Keep ChromaDB (persistent client) and sentence-transformers `all-MiniLM-L6-v2` (matches the existing index; embedding model becomes configurable with metadata versioning).
2. Restructure into an `apex_ai/` package while keeping `ui.py`, `ingest.py`, `chat.py` as working entry-point shims.
3. New `APEX_*` environment variables; legacy names (`LLM_PROVIDER`, `LLAMA_MODEL_PATH`, `OLLAMA_*`, `OPENAI_*`, `HF_MODEL_PATH`) still honored for backward compatibility.
4. Add retrieval stages incrementally with graceful degradation: hybrid (vector+BM25) → optional reranker → context budget → grounded prompt → citation of **used** evidence only → low-confidence refusal.
5. Split requirements into core / dev / gpu; drop the freeze-dump.
6. Untrack runtime data (uploads, memory JSON, `pu` data blobs) — files stay on disk, out of git.
7. Evaluation harness with deterministic, honest metrics — no performance claims without measurements.

See `README.md` for the resulting architecture.
