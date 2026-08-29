# Contributing to Apex AI

This is a guide for a developer picking this project up for the first time — human
or AI agent. If you already know Python tooling, skip to
[Everyday commands](#everyday-commands). If you're new to it, the steps below spell
out what each command does and why.

## 1. Get the code running

```bash
git clone https://github.com/luivatan/LUIVATAN-AI.git
cd LUIVATAN-AI
python3 -m venv .venv              # creates an isolated Python environment in .venv/
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env               # your local, untracked config; edit values as needed
```

A **virtual environment** (`venv`) keeps this project's Python packages separate from
anything else installed on your machine, so `pip install` here can't break (or be
broken by) some other project. You activate it once per terminal session; your prompt
will show `(.venv)` when it's active.

`.env` holds configuration — model paths, provider selection, upload limits — as
`APEX_*` environment variables (see the table in [`README.md`](README.md#configuration)).
It is listed in `.gitignore` on purpose: **never commit `.env`** or anything with a
real API key or secret in it. `.env.example` documents every variable with safe
placeholder/default values.

You do not need a model to run the tests (they use a deterministic fake LLM and a
fake embedding provider — see `tests/conftest.py`). You do need one to actually chat;
see the README's ["Model setup"](README.md#model-setup-offline-operation) section.

## 2. Run the app

```bash
python ui.py
```

Open `http://127.0.0.1:7860`. See the README for the other entry points
(`python -m apex_ai.api.server`, `python legacy_ui.py`, `python chat.py`) and what
each one is for.

## 3. Everyday commands

```bash
python -m pytest tests/ -q     # run the test suite (234 tests, fully offline)
ruff check .                   # lint (see "Linting" below for what's blocking vs. not)
python evaluate_rag.py         # measure retrieval/citation quality against eval/dataset.example.jsonl
```

Run the test suite before every commit. `.github/workflows/tests.yml` runs the same
suite (plus `ruff`) on every push and pull request — if it's red there, it would have
been red locally too.

### Linting

`ruff check .` will show pre-existing findings that are tracked but not yet fixed
(documented in [`docs/PHASE9_TESTING_FOUNDATION.md`](docs/PHASE9_TESTING_FOUNDATION.md)).
Don't let those block you. What matters for a change to be mergeable:

- `ruff check --select E9,F63,F7,F82 .` (syntax errors, undefined names) must be
  clean — this is CI's hard gate.
- Files you actually touched should be clean under plain `ruff check <file>` — fix
  what your change introduces; you don't need to fix unrelated pre-existing findings
  in files you're passing through, though you're welcome to in a separate change.

## 4. How this repository develops: the roadmap-phase workflow

Look at `git log --oneline` and you'll see a pattern: most commits correspond to one
numbered phase from [`AI roadmap.md`](AI%20roadmap.md) (a 100-phase plan covering
foundation → RAG → memory → auth/security → documents → agents → billing →
production/sales), and most phases with real code changes have a matching
`docs/PHASEN_<NAME>.md` write-up. This isn't enforced by tooling — it's a convention,
and it's worth following because it's what makes a large, incrementally-built project
like this reviewable months later:

1. **Read the phase's one-paragraph goal** in `AI roadmap.md` before starting.
2. **Inspect before changing.** Read the relevant existing code and tests first — a
   phase's real job is often "close a specific gap," not "build from scratch." Several
   phases in this repo turned out to be mostly-already-done on inspection; the
   resulting doc says so explicitly instead of padding out unnecessary work.
3. **Implement the smallest change that closes the real gap.** Don't add abstractions,
   config flags, or features the phase didn't ask for.
4. **Write (or extend) tests that would fail without your change**, then run the full
   suite — not just the new tests — before considering the phase done. A regression
   in an unrelated area is still your regression if your commit introduced it.
5. **Write `docs/PHASEN_<NAME>.md`** covering: what was audited/found, what changed,
   what was deliberately *not* changed and why, a verification table (test counts,
   lint results), and known boundaries/unknowns. Future readers — including future
   agent sessions — rely on this to avoid re-litigating settled decisions or, worse,
   assuming something works that was explicitly deferred.
6. **Commit with a message that says what changed and why**, not just "Phase N."

The roadmap's own ground rules (from `AI roadmap.md`) apply to every phase: no fake
features, fake data, fake citations, fake model choices, or fake billing states; keep
secrets out of source and logs; test every phase before moving to the next.

## 5. Where things live

| Looking for... | Start here |
|---|---|
| What each variable in `.env` does | [`README.md`](README.md#configuration), [`.env.example`](.env.example) |
| Full system architecture (components, trust boundaries, data flow) | [`docs/PHASE2_ARCHITECTURE_MAP.md`](docs/PHASE2_ARCHITECTURE_MAP.md) — pinned to its baseline commit; later phase docs note what has changed since |
| The original state of the codebase before the Apex AI rewrite | [`docs/AUDIT.md`](docs/AUDIT.md) |
| Browser chat UI internals (streaming protocol, components) | [`docs/CHAT_INTERFACE_ARCHITECTURE.md`](docs/CHAT_INTERFACE_ARCHITECTURE.md) |
| Why a specific phase is built the way it is | `docs/PHASEN_*.md` for that phase number |
| The full 100-phase plan | [`AI roadmap.md`](AI%20roadmap.md) |

## 6. Ground rules (apply to every change, not just roadmap phases)

- Preserve working functionality; don't refactor code you're not touching for the
  task at hand.
- Don't invent fake data, fake citations, fake successful states, or silently swallow
  errors — see `AI roadmap.md`'s development rule at the top of the file.
- Never commit secrets, real API keys, or real user/document content. If you're
  unsure whether something is sensitive, don't commit it — ask first.
- This is currently a **single-user, local-only application** (no auth yet — see
  Section 5 of the roadmap). Don't expose a running instance to an untrusted network.
