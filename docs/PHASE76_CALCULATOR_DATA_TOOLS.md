# Apex AI Phase 76 — Calculator / Data Tools

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `c8b9e59`, immediately following Phase 75 (Web Search decision)
- **Scope:** "Add reliable tools for calculations and structured data tasks
  instead of asking the LLM to guess arithmetic." This is the first phase
  with a real, registered tool — and the first to actually reach the model,
  closing the loop Phase 73/74 built but deliberately left unconnected.

## The two tools

**`calculator`** (`apex_ai/tools/calculator.py`) evaluates a numeric
arithmetic expression exactly — addition, subtraction, multiplication,
division, floor division, modulo, exponentiation, and
`abs`/`round`/`min`/`max`/`sqrt`/`floor`/`ceil`. It never calls `eval()` or
`exec()`: expressions are parsed with `ast.parse(mode="eval")` and walked
node by node against a strict whitelist of node types, operators, and
function names. Anything else — attribute access, subscripting, name
references, list/dict/tuple literals, comprehensions, lambdas, string
literals, `__import__`, `open`, `exec` itself — falls through to a single
"not allowed" branch. A pathological expression (`9**9**9`, which is
`9**(9**9)` — an exponent in the hundreds of millions) is refused before it
is ever computed: exponents above 1000 and results above a 1e15 magnitude
cap are rejected explicitly, so an expression that would otherwise take
unbounded time or memory to actually evaluate never gets the chance to run.

**`data_stats`** (`apex_ai/tools/data_stats.py`) computes an exact
aggregate — sum, mean, median, min, max, count, or stdev — over a list of
numbers, rejecting non-numeric items (including Python `bool`, which is a
subtype of `int` and would otherwise silently count as 0/1) and capping the
list at 10,000 items.

Both are registered with `requires_permission=False` (Phase 74's opt-out):
pure computation, no I/O, no state mutation, no network — the textbook case
that category exists for.

## Wiring: the loop finally closes

`apex_ai/tools/build_default_registry()` builds a `ToolRegistry` with both
tools; `runtime.py` constructs `services.tools` and
`services.tool_executor = PermissionedToolExecutor(services.tools)` at
startup — genuine, live application infrastructure now, not inert library
code.

`RagEngine.ask_with_tools(question, ..., tool_executor=..., granted_tools=...)`
is the new entry point that actually lets a model call these. It is a safe
superset of `ask()`: it falls back to plain `ask()` whenever tools cannot
actually be used this turn (the active provider's `supports_tools` is
`False`, or nothing is registered/granted) — an honest fallback, not a
degraded feature, and every existing caller of `ask()`/`ask_stream()` is
completely unaffected since neither of those methods changed at all. When
tools *are* usable, one round happens: the model is offered the
permission-aware schema (`tool_executor.schema(granted_tools=...)` — a
gated, ungranted tool isn't even offered), any tool calls it makes are
executed through `PermissionedToolExecutor` (so the per-turn call budget
and the permission gate both apply for real), and a second, final
generation call (offering no further tools) produces the answer that
incorporates the results. `runtime.py`'s `_LazyLLM` wrapper — the adapter
the shared engine singleton and every per-conversation engine in
`api/chat.py` actually use — now proxies `supports_tools`/
`generate_with_tools()` to whichever provider is currently active, so this
reflects real runtime model switching the same way `supports_streaming`
already does.

`AnswerResult` gained `tool_calls_used: list[dict]` (empty for every turn
that didn't go through `ask_with_tools`) recording exactly which tools ran,
with what arguments, and their result — internal audit data in the same
spirit as the existing `context_chunk_ids`/`queries_used` fields.

## Files

- `apex_ai/tools/calculator.py`, `apex_ai/tools/data_stats.py` (new)
- `apex_ai/tools/__init__.py` — `build_default_registry()`
- `apex_ai/core/types.py` — `AnswerResult.tool_calls_used`
- `apex_ai/rag/engine.py` — `RagEngine.ask_with_tools()`
- `apex_ai/runtime.py` — `services.tools`/`services.tool_executor`;
  `_LazyLLM.supports_tools`/`generate_with_tools()`
- `tests/test_calculator_tool.py`, `tests/test_data_stats_tool.py`,
  `tests/test_engine_tool_calling.py` (new)

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 447 passed, 3 skipped |
| `tests/test_calculator_tool.py` | 46 passed (shared with `test_data_stats_tool.py`) |
| `tests/test_engine_tool_calling.py` | 7 passed (fallback, direct-answer, real tool execution, ungranted-tool refusal, per-turn budget, evidence gate still enforced) |
| `ruff check` on every touched/new file | only the pre-existing `apex_ai/rag/engine.py:381` finding (unchanged from baseline) |

## Deliberately not done in this phase

- **No wiring into `/chat/stream` or the frontend.** `ask_with_tools()` is
  real, tested, and reachable by any caller (including a future API route),
  but is genuinely useful today only for `APEX_LLM_PROVIDER=openai_compatible`
  (the one provider with a real `generate_with_tools()`, Phase 73). Touching
  the live HTTP chat endpoint and its NDJSON event stream — the most
  trafficked code path in the app, used by every deployment regardless of
  provider — for a capability most default (local) setups cannot exercise
  yet is a deliberately separate decision, not an oversight.
- **No multi-round agentic tool loop.** One round (offer tools → execute
  what was called → one final answer with no further tools offered) covers
  the realistic case ("what's 47 × 892 plus the sum of my numbers") without
  open-ended looping; `PermissionedToolExecutor`'s own per-turn call budget
  already bounds calls *within* that one round.
- **No streaming for the tool-augmented path.** `ask_with_tools()` mirrors
  `ask()` (a complete `AnswerResult`), not `ask_stream()`. Streaming the
  final round is plausible future work once this is actually wired into the
  live chat endpoint above.
- **No new settings/env vars.** Both tools are always registered — they are
  safe, free, and have no configuration surface worth exposing.
