# LUIVATAN AI

LUIVATAN AI is an offline medical document assistant: upload a PDF, ask a
question, get an answer grounded in that document with page/chunk
citations.

Features:
- Local AI (four interchangeable LLM backends)
- PDF ingestion with page-aware chunking
- Conversation memory
- Source citations and a source viewer
- Uploaded document library
- First-run GGUF model selection
- Health check panel
- Privacy-first, no internet required (with the default local backend)

Document scope: this AI is designed for medical documents. Uploads that
don't look medical are not blocked, but the app warns that results may be
less reliable for non-medical content.

For how the app is built (data flow, storage, LLM backends) see
`docs/ARCHITECTURE.md`. For the state of the codebase, known gaps, and the
reasoning behind cleanups already made, see `docs/AUDIT.md`. The long-term
plan is `roadmap.md`.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in what you need (all fields have
   sane defaults except API keys):
   ```bash
   cp .env.example .env
   ```
3. Run the app:
   ```bash
   ./launch_luivatan.sh
   ```
   or directly:
   ```bash
   python3 ingest.py
   ```

No GGUF model is hard-coded. On first run, use the **Select GGUF Model**
control in the UI to choose a `.gguf` file before chatting (or set
`LLAMA_MODEL_PATH` in `.env` to preselect one).

## LLM provider

The app can use different LLM backends, selected with `LLM_PROVIDER` in
`.env`:

Local GGUF with llama.cpp (default) — select a model in the UI, or:
```bash
LLM_PROVIDER=llama_cpp
LLAMA_MODEL_PATH="/path/to/model.gguf"
```

Ollama:
```bash
LLM_PROVIDER=ollama
OLLAMA_MODEL="llama3.1:8b"
OLLAMA_URL="http://localhost:11434"
```

OpenAI or an OpenAI-compatible API:
```bash
LLM_PROVIDER=openai_compatible
OPENAI_API_BASE="https://api.openai.com/v1"
OPENAI_API_KEY="your-api-key"
OPENAI_MODEL="gpt-4.1-mini"
```

Local Hugging Face Transformers model:
```bash
LLM_PROVIDER=transformers
HF_MODEL_PATH="Qwen/Qwen2.5-0.5B-Instruct"
```

## Development

Run the test suite (fast — no ML dependencies required, only `pytest`):
```bash
pip install -r requirements-dev.txt
pytest
```

The tests cover the dependency-free helpers in `rag_utils.py` (PDF
chunking, filename sanitization, hashing, citation formatting). The main
app in `ingest.py` imports `chromadb`, `gradio`, `sentence-transformers`,
and `requests` at import time and is exercised manually through the UI —
see `docs/ARCHITECTURE.md` for the full request flow to check when making
changes.

Logging verbosity is controlled by `LOG_LEVEL` in `.env` (`DEBUG` shows
full retrieval details; `INFO`, the default, doesn't).

## Missing packages

If a required package is missing, install project dependencies:
```bash
pip install -r requirements.txt
```

## Packaging

For local development, run the app with:
```bash
./launch_luivatan.sh
```

For a true double-click desktop app, package this project with a Python
app bundler such as PyInstaller or Nuitka on the target operating system.
The app is ready for that flow since model selection happens inside the UI
instead of requiring a hard-coded model path.
