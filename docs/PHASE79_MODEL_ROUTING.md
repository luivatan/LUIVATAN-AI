# Apex AI Phase 79 — Model Routing

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `1ebec0d`, immediately following Phase 78 (CSV/TSV Support)
- **Scope:** "Allow Apex AI to select an appropriate available model based
  on task, latency, and configured limits." Scoped to what this
  architecture can honestly deliver: a real, tested selection utility over
  already-discovered local models — not live per-request model hot-swapping.

## Why this is a decision-support utility, not live model switching

Apex AI runs exactly one active local model per process (`llama_cpp`'s
per-process model lifecycle — the same constraint `_LazyLLM`/
`select_model()` already work within). Loading a *second* full GGUF model
into memory just to answer one side-call (e.g. a "fast" model for query
rewriting) would cost far more latency and memory than it saves — worse
than the very problem routing-for-latency is trying to solve. Faking
"instant model switching" per request would be exactly the kind of
overclaiming this project's ground rules forbid. So this phase builds the
real, useful half of "select an appropriate available model": ranking and
choosing among the models `ModelManager` already discovers, honestly, as a
decision a caller (a person via the API, or a future feature) can act on —
not a live per-call swap this architecture cannot actually do cheaply.

## Design

`apex_ai/models/router.py`'s `ModelRouter.select(task)` ranks every
*loadable* model `ModelManager.discover()` finds against one of two task
profiles:

- **`"chat"`** — the main grounded-answer generation. Picks the *largest*
  loadable model (favor quality).
- **`"fast"`** — a latency-sensitive side call (an optional query rewrite,
  conversation summarization). Picks the *smallest* loadable model.

File size is the latency proxy used: without loading each candidate model
(which would defeat the point of a cheap-to-call decision utility), it is
the only signal discovery has, and it is a real, if imperfect, one — a
smaller GGUF quantization generally does generate faster. This limitation
is stated explicitly in the module's own docstring, not left implicit.

`APEX_MAX_FAST_MODEL_MB` (Phase 79's "configured limits", unset by
default) caps which models are eligible for the `"fast"` task; the `"chat"`
task is deliberately never constrained by it — a knob meant for
latency-sensitive side calls must not silently degrade the main answer's
model choice. When nothing fits a configured ceiling, `select()` returns
`entry=None` with a clear reason rather than silently ignoring the
configured limit or guessing.

`ModelEntry` (the existing `ModelManager` discovery result) gained
`size_bytes: int` — the raw byte count `size`'s human-readable string
(`"123.4 MB"`) is formatted from, needed because ranking requires comparing
sizes, not re-parsing a formatted string back into a number.

## Wiring

`runtime.py` builds `services.model_router = ModelRouter(services.models,
max_fast_model_mb=settings.max_fast_model_mb)` at startup — real,
constructed infrastructure, immediately reachable. A new read-only
endpoint, `GET /models/recommended?task=chat|fast`, surfaces the
recommendation without selecting or loading anything itself; an operator
or a future UI feature can act on it explicitly (e.g. call the existing
`POST /models/select` themselves) rather than this phase silently changing
what model gets loaded at startup or per request — startup/selection
behavior is completely unchanged from before this phase.

## Files

- `apex_ai/models/router.py` (new) — `ModelRouter`, `RoutingDecision`
- `apex_ai/models/manager.py` — `ModelEntry.size_bytes`
- `apex_ai/config/settings.py`, `.env.example`, `README.md` —
  `max_fast_model_mb` / `APEX_MAX_FAST_MODEL_MB`
- `apex_ai/runtime.py` — `services.model_router`
- `apex_ai/api/server.py`, `apex_ai/api/schemas.py` —
  `GET /models/recommended`, `RecommendedModelOut`
- `tests/test_model_routing.py` (new), additions to `tests/test_api_ui.py`

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 477 passed, 3 skipped |
| `tests/test_model_routing.py` | 12 passed |
| `tests/test_api_ui.py` | 22 passed (includes the new `/models/recommended` cases) |
| `ruff check` on every touched/new file | clean |

## Deliberately not done in this phase

- **No live per-request model switching.** Explained above — this
  architecture's single-active-local-model constraint makes that
  prohibitively expensive to do for real, so this phase doesn't fake it.
- **No automatic startup model selection.** `APEX_MODEL_PATH` empty still
  means "not preselected; UI/manager decides," exactly as before — the
  router only recommends when asked, so existing boot behavior for every
  current deployment is completely unchanged.
- **No frontend UI for the recommendation.** The endpoint is real and
  usable via the API today; a "Recommended" badge in the Models tab is
  additive future work, not required to make this phase's capability real.
- **No remote-provider routing (Ollama/OpenAI-compatible).** Those
  providers name one configured model each (`APEX_OLLAMA_MODEL`/
  `APEX_OPENAI_MODEL`) with no local discovery step to rank against;
  routing among multiple *remote* model options is a different feature.
