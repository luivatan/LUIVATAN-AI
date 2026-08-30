# Apex AI Phase 80 — AI Reliability Layer

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `585b179`, immediately following Phase 79 (Model Routing)
- **Scope:** "Add timeouts, retries, fallbacks, and graceful handling of
  unavailable models or tools." This is the roadmap's final Section 7
  phase, so it opens with a full audit of what earlier phases already
  built toward this goal, then closes the one genuine gap the audit found
  (retries) and documents, with real reasoning, what is deliberately not
  attempted.

## Audit: what already existed before this phase

| Concern | Status before Phase 80 |
|---|---|
| **Timeouts** (network providers) | Already real. `APEX_PROVIDER_CONNECT_TIMEOUT_SECONDS`/`APEX_PROVIDER_READ_TIMEOUT_SECONDS` bound every Ollama/OpenAI-compatible HTTP call, tested since before this session's roadmap pass. |
| **Graceful handling of an unavailable local model** | Already real. `LocalLLMProvider.validate()` raises `ModelNotFoundError` with the exact expected path and two concrete fixes when `APEX_MODEL_PATH` is empty or missing; a corrupted/unsupported GGUF file gets a specific `ProviderError`; a mid-generation failure is wrapped, never a raw traceback. |
| **Graceful handling of an unavailable/hallucinated tool** | Already real (Phase 73/74). `ToolRegistry.execute()` never raises: an unknown tool name, malformed arguments, or a raising handler all come back as a bounded `ToolResult(is_error=True, ...)`. `PermissionedToolExecutor` adds a per-turn call budget and a permission gate on top, both refusing gracefully rather than crashing the turn. |
| **Retries on transient failure** | **Missing.** No provider retried a connection error, a timeout, or a 429/5xx response — every one of these failed on the first attempt, indistinguishable from a permanent failure. |
| **Cross-provider/model fallback** | Never attempted (see below — a deliberate decision, not a gap this phase closes). |

The one real gap this phase closes is retries; everything else in the
"timeouts ... graceful handling" list was already solid, built
incrementally across earlier phases in this same roadmap pass.

## What this phase adds: retry-with-backoff

`apex_ai/llm/retry.py`'s `call_with_retries()` retries a callable on a
genuinely transient failure only: `requests.ConnectionError`,
`requests.Timeout`, or an HTTP response whose status is 429 or in the 5xx
range. A definitively non-transient failure — 401 unauthorized, 400 bad
request, 404 unknown model — is never retried: retrying it wastes time and
risks masking a real configuration problem behind repeated identical
failures instead of surfacing it immediately, exactly as it does today.
Backoff is exponential (`base_delay * 2^(attempt-1)`), and the delay
function is injectable (`sleep: Callable[[float], None]`) so every test in
this phase controls or eliminates real waiting — no test in this codebase
actually sleeps for a retry.

`OllamaProvider._post()` and `OpenAICompatProvider._post()` (a new shared
helper introduced for the latter, replacing four near-identical inline
`requests.post()` call sites) both route through `call_with_retries()`.
Retries only ever cover *establishing* the response — the `requests.post`
call and its status check — never token iteration. This is safe for
`stream()` as much as `generate()`: every provider calls this helper
exactly once per request, before any streamed line has been read or
yielded to the caller, so a retry here can never duplicate or corrupt
output a caller has already seen. `APEX_PROVIDER_RETRY_MAX_ATTEMPTS`
(default 3) and `APEX_PROVIDER_RETRY_BASE_DELAY_SECONDS` (default 0.5) are
configurable, matching the existing timeout settings' pattern;
`max_attempts=1` disables retries entirely (fail-fast, e.g. behind a load
balancer that already retries, or for a test that wants immediate,
deterministic failure).

## Why no cross-provider or cross-model fallback

The roadmap names "fallbacks" alongside timeouts/retries/graceful
handling. A fallback that silently switches to a *different* model or
provider when the configured one fails was deliberately not built, for the
same transparency reason Phase 79 declined live model hot-swapping: Apex
AI goes out of its way elsewhere (`get_model_info()`, the `/health` and
`/app-config` endpoints, the citation/evidence system itself) to be honest
about exactly what answered a question. A silent fallback would mean a
user could receive an answer from a materially different, unrequested
model without any signal that happened — the opposite of that
transparency posture, and a real trust problem for an app whose whole
premise is "answer only from retrieved evidence, honestly cited." A
fallback that instead surfaces clearly (an event, a banner, a citation
note: "answered by fallback model X because Y failed") would be legitimate
future work, but is a UI/API design decision of its own, not something to
bolt onto a reliability-layer phase as a side effect.

## Files

- `apex_ai/llm/retry.py` (new) — `call_with_retries()`, `is_retryable()`,
  `RETRYABLE_STATUS_CODES`
- `apex_ai/llm/ollama.py` — `_post()` retries via `call_with_retries()`
- `apex_ai/llm/openai_compat.py` — new shared `_post()` helper (replacing
  four inline `requests.post()` call sites in `generate`/`stream`/
  `generate_with_tools`/`generate_structured`), retrying via
  `call_with_retries()`
- `apex_ai/config/settings.py`, `.env.example`, `README.md` —
  `provider_retry_max_attempts` / `APEX_PROVIDER_RETRY_MAX_ATTEMPTS`,
  `provider_retry_base_delay_seconds` / `APEX_PROVIDER_RETRY_BASE_DELAY_SECONDS`
- `tests/test_provider_retry.py` (new) — the generic retry helper
- `tests/test_llm.py` — real (mocked HTTP) provider-level retry/no-retry
  round trips for both Ollama and OpenAI-compatible

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 491 passed, 3 skipped |
| `tests/test_provider_retry.py` | 9 passed |
| `tests/test_llm.py` | 24 passed |
| Test suite wall-clock time | 38.9s (no real sleeping added by any retry test — every one injects or disables the delay function) |
| `ruff check` on every touched file | only pre-existing findings (verified identical count/rules against baseline) |

## Deliberately not done in this phase

- **No cross-provider or cross-model fallback**, for the transparency
  reasoning above.
- **No retry for `stream()`'s token iteration**, only for establishing the
  response — retrying after any content has reached the caller would
  duplicate or corrupt output, which no amount of "helpfulness" justifies.
- **No timeout/retry changes for local providers** (`llama_cpp`,
  `transformers`). They run in-process, not over HTTP, so "timeout" and
  "retry" as network concepts don't directly apply; a real, safe
  cross-platform cancellation mechanism for a blocking native call inside
  `llama-cpp-python` is a substantially different (and substantially
  riskier) problem than HTTP retries, and forcing one into this phase
  alongside the network-provider work above would risk the most central
  code path in the app for a benefit this audit did not find a concrete,
  reported need for.
- **No change to how many times a request is retried across the whole
  system.** Retries are scoped per network call, not per user-facing
  question; a chat turn does not itself get "retried" as a unit — only the
  transient HTTP failures inside generating its answer do.
