# Apex AI Phase 73 — Tool Architecture

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `ed0fc53`, immediately following Phase 71/72 (Projects)
- **Scope:** "Create a safe abstraction for tools the model can call." This
  phase delivers the reusable execution boundary and the provider-level
  capability only — it does not decide which tools exist, does not wire
  tool-calling into `RagEngine`/live chat, and does not enforce permissions.
  Those are Phase 74 (permissions) and Phase 76 (the first real tool,
  calculator/data). Wiring an empty registry into the live request path with
  nothing in it yet would be exactly the kind of premature scaffolding this
  project's "never fake features" rule warns against, so this phase stops at
  a fully tested, real library.

## Design

**`apex_ai/tools/base.py`** — `Tool` wraps one real, deterministic Python
callable (`handler: Callable[[dict], str]`) plus its OpenAI-style JSON
Schema (`name`, `description`, `parameters`). `ToolRegistry` is the only
place a tool call is actually executed (`register`/`get`/`list`/`schema`/
`execute`), and `execute()` never raises: an unknown tool name, malformed
JSON arguments, or an exception inside the handler all come back as a
normal, bounded `ToolResult(is_error=True, ...)` instead of propagating —
a model hallucinating a tool name or bad arguments is an expected outcome
for this boundary, not an exceptional one. A result is also capped at
`MAX_RESULT_CHARS` (4000), the same kind of fixed safety bound
`APEX_CONTEXT_CHAR_LIMIT` already gives retrieved evidence, so a runaway or
verbose tool can't blow up the prompt budget either. Handler exception text
is never included in the returned `ToolResult.content` — only a generic
"could not complete this request" message — mirroring how the rest of this
codebase never surfaces raw internals to a model or a user.

**`apex_ai/llm/base.py`** — `LLMProvider` gained `supports_tools: bool =
False` and `generate_with_tools(messages, tools, ...)`. `tools` is the
OpenAI-style function-schema list (`[{"type": "function", "function":
{"name", "description", "parameters"}}]`) — the one shape every provider
that genuinely implements this already speaks natively, so no
provider-specific translation layer sits in the base class. The default
implementation raises a clear `ProviderError` naming the provider and
suggesting a real alternative; Apex AI never simulates tool-calling through
prompt tricks for a provider whose API doesn't actually support it. The
response type, `ToolCallResult(content, tool_calls: tuple[ToolCall, ...])`,
and `ToolCall(id, name, arguments_json)`, live here because they describe
what a *provider* returns; `ToolCall.arguments_json` is left as the raw JSON
string from the wire rather than parsed at this layer — a malformed or
hallucinated payload is a tool-execution concern (`ToolRegistry.execute`),
not something a provider should have to validate on the model's behalf.

**`apex_ai/llm/openai_compat.py`** — the only provider with a real
implementation this phase: `supports_tools = True`,
`generate_with_tools()` posts the standard `tools` parameter to
`/chat/completions` and parses `message.tool_calls` into `ToolCall` objects,
reusing the exact error handling (`ProviderError` on request failure or an
unparseable response) the existing `generate()` already has.

## Why only one provider gets a real implementation

Ollama's `/api/chat` also documents tool support, but its wire shape differs
from OpenAI's in ways this session could not verify against a live server in
this sandboxed, network-restricted environment (in particular, whether
`arguments` comes back as a JSON string like OpenAI's or an already-parsed
object). Shipping an unverified implementation and claiming it works would
be exactly the kind of overclaiming the roadmap's own ground rules forbid
("never fake ... model-choices"). `llama_cpp` (local GGUF) and the local
`transformers` provider have no tool-calling wired up in this codebase at
all — `supports_tools` stays at its honest default of `False` for all three,
and calling `generate_with_tools()` on any of them raises the same clear,
actionable error the base class provides. A future phase can add a verified
Ollama implementation once its exact response shape can be confirmed against
a real server.

## Files

- `apex_ai/tools/__init__.py`, `apex_ai/tools/base.py` (new)
- `apex_ai/llm/base.py` — `ToolCall`, `ToolCallResult`,
  `LLMProvider.supports_tools`/`generate_with_tools()`
- `apex_ai/llm/openai_compat.py` — real `generate_with_tools()`
- `tests/test_tools.py` (new), additions to `tests/test_llm.py`

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 384 passed, 3 skipped |
| `tests/test_tools.py` | 12 passed |
| `ruff check` on every touched/new file | only pre-existing findings (verified identical count and rules against baseline) |

## Deliberately not done in this phase

- **No live wiring into `RagEngine`/`stream_chat`.** There is no real tool
  registered yet (that's Phase 76); wiring inert infrastructure into the
  chat path now would be scaffolding with nothing behind it.
- **No permission/consent layer.** Any registered tool would currently run
  unconditionally once called — Phase 74 adds the explicit boundary the
  roadmap requires before that's safe to expose.
- **No Ollama `generate_with_tools()` implementation**, for the verification
  reason above — `supports_tools` honestly reports `False` rather than
  guessing.
- **No `services.tools` registry wired into `runtime.py` yet.** An empty
  registry with nothing registered and nothing reading it would be exactly
  the premature scaffolding described above; Phase 76 wires it in alongside
  its first real tool.
