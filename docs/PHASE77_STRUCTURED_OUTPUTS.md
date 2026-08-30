# Apex AI Phase 77 — Structured Outputs

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `97e93e4`, immediately following Phase 76 (Calculator/Data Tools)
- **Scope:** "Support reliable JSON/structured responses for features that
  need them." Same shape as Phase 73 (Tool Architecture): a real,
  provider-verified capability on `LLMProvider`, honestly unsupported
  everywhere it hasn't been verified — not force-connected to a feature
  that doesn't actually need it.

## What was surveyed first

Before writing anything, this phase looked for an existing feature in the
codebase that currently asks an LLM for structured data and parses free
text unreliably as a result — the natural place a "for features that need
them" phase should land. None exists:

- `apex_ai/rag/query_processing.py`'s optional LLM rewrite/decomposition
  (`APEX_QUERY_REWRITE`, off by default) asks for and validates plain
  rewritten query text, not JSON.
- `apex_ai/memory/extraction.py` (candidate detection) and
  `apex_ai/memory/relevance.py` (relevance selection) never call an LLM at
  all — both are deliberately deterministic/local.
- `apex_ai/memory/summarization.py` builds a plain-text summarization
  prompt, not a structured one.
- The core grounded-answer path (`RagEngine.ask`/`ask_stream`) is
  deliberately one free-text generation call with `[n]` citation markers —
  changing that response shape is out of scope for this phase and would be
  a much larger, separate design decision.

Forcing a structured-output requirement onto any of these would be
inventing a need to have something to wire up, not meeting a real one — so
this phase, like Phase 73, ships the verified capability and documents
that nothing in the codebase currently needs it, rather than force-fitting
one.

## Design

`LLMProvider` gained `supports_structured_output: bool = False` and
`generate_structured(messages, schema, *, schema_name="response", ...) ->
dict`. `schema` is a JSON Schema object; the default implementation raises
a clear `ProviderError` naming the provider. `OpenAICompatProvider` is the
one real implementation this phase (`supports_structured_output = True`),
using OpenAI's own documented `response_format: {"type": "json_schema",
"json_schema": {"name", "schema", "strict": true}}` mode — the same
verification standard Phase 73 held `generate_with_tools()` to: only a
provider whose wire format could actually be confirmed gets marked as
supported.

Validation is deliberately light: `strict: true` structured-output mode is
itself the provider's guarantee that the returned JSON matches `schema`, so
Apex AI's own responsibility is just parsing the response and confirming it
decodes to a JSON *object* (not re-implementing a JSON Schema validator for
guarantees the provider already enforces server-side). A response that
isn't valid JSON, or whose top level isn't an object, raises a clear
`ProviderError` rather than returning something the caller has to guard
against.

## Files

- `apex_ai/llm/base.py` — `LLMProvider.supports_structured_output`,
  `generate_structured()`
- `apex_ai/llm/openai_compat.py` — real `generate_structured()`
- `tests/test_llm.py` — unsupported-provider error, a real (mocked HTTP)
  round trip, invalid-JSON rejection, non-object-top-level rejection

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 451 passed, 3 skipped |
| `tests/test_llm.py` | 19 passed |
| `ruff check` on every touched file | only pre-existing findings (verified identical count/rules against baseline) |

## Deliberately not done in this phase

- **No feature wired to consume it.** As surveyed above, nothing in the
  current codebase has an unmet structured-output need; connecting this to
  a real feature is for whichever future phase actually introduces one.
- **No Ollama implementation**, for the same reason Phase 73 declined one
  for tool-calling: its exact structured-output wire format could not be
  verified against a live server in this sandboxed environment.
- **No client-side JSON Schema validator.** `strict: true` mode is the
  provider's own guarantee; re-validating it locally would duplicate a
  guarantee already made server-side rather than add real safety.
