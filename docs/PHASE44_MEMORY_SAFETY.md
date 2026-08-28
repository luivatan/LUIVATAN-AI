# Apex AI Phase 44 — Memory Safety

**Audit date:** 2026-08-28 (America/Chicago)
**Roadmap scope:** never retain passwords, API keys, authentication tokens, or other
secrets; avoid unnecessary sensitive information

## BEFORE — inspection at Phase 43

Phase 43's candidate extractor is conservative and is not connected to chat or
persistence. However, an explicit “remember” sentence can still become an ephemeral
candidate even when it contains a credential. More importantly, the Phase 42
`LongTermMemoryStore.create()` and `update()` methods validate kind/content shape but do
not enforce a content safety policy. Any future confirmation or management caller could
therefore persist a secret if safety were enforced only in the UI.

The repository has strong file/path helpers and a `SecurityError` type, but no reusable
text policy for long-term memory. API keys currently remain in Settings and provider
headers as intended; this phase must not change provider configuration or scan ordinary
chat/document content. It must protect only the durable-memory boundary.

## Phase 44 design

Add one local, deterministic, fail-closed `MemorySafetyPolicy` and apply it in two places:

1. **before candidate output**, reducing the chance that a secret is shown in a later
   confirmation flow; and
2. **inside the store's create/update methods**, which is the authoritative boundary and
   cannot be bypassed by a future UI/API caller.

The policy will report reason codes only—not matched values—and will cover:

- labeled passwords, PINs, keys, client secrets, session/cookie values, and auth tokens;
- common provider/token formats, JWTs, bearer/basic credentials, private-key blocks, and
  credentials embedded in URLs;
- likely high-entropy opaque credentials while exempting ordinary UUID/hex identifiers;
- Social Security and payment-card numbers (with Luhn validation);
- labeled banking, recovery phrase, government ID, contact/address, personal-health, and
  similarly unnecessary sensitive profile details.

On opening an existing Phase 42 database, recognized unsafe legacy rows must be deleted
transactionally and only an aggregate removal count may be surfaced. Silent retention
would leave the new invariant false; copying them to a quarantine would still retain the
secret. The policy cannot promise perfect semantic secret detection, so documentation
must state its limits and direct users to a password/secret manager rather than memory.

## Non-goals

- Do not scan or alter normal chat history, documents, prompts, logs, or provider keys.
- Do not redact a secret into a partial memory; partial strings can still leak data or
  change meaning.
- Do not expose matched secret text in exceptions, diagnostics, or tests.
- Do not add confirmation UI yet; Phase 45 remains the next gate.

## AFTER — enforced safety boundary

`apex_ai.security.memory` now provides a content-free safety result model and a reusable
`MemorySafetyPolicy`:

```text
user text
  -> conservative candidate rules
  -> MemorySafetyPolicy.inspect()
       unsafe: omit candidate (no value in diagnostics)
       safe:   ephemeral candidate

approved text in a future phase
  -> LongTermMemoryStore.create/update()
  -> MemorySafetyPolicy.require_safe()
       unsafe: raise UnsafeMemoryError before SQL
       safe:   parameterized SQLite write
```

`MemorySafetyResult` contains only `MemorySafetyFinding(code=...)` values. It never stores
the matched substring. `UnsafeMemoryError` says that the text was not saved, names only
the applicable reason codes, and directs the user to a dedicated secret manager without
echoing the rejected value.

The detector currently recognizes:

- explicit credential labels such as password, PIN, API/access key, client secret,
  private key, auth/access/refresh token, session, and cookie assignments;
- bearer/basic authorization values, private-key headers, credentials in URLs, JWT
  shapes, and common OpenAI-style, GitHub, Slack, Google, and AWS key formats;
- 32–256 character mixed-class/high-entropy opaque values, while exempting ordinary
  hexadecimal hashes and standard UUIDs so exact technical identifiers remain usable;
- Social Security and Luhn-valid payment-card numbers;
- labeled banking, recovery phrase, government/medical IDs, date of birth, and similar
  values; and
- directly stated personal contact/address, health, and sensitive profile attributes
  that are unnecessary for the Phase 42 preference/project-context scope.

Both `MemoryCandidateExtractor` and `LongTermMemoryStore` receive the same policy object
from `build_services()`. Candidate filtering is defense in depth; create/update checks are
the authoritative invariant. Safety failures happen before SQL and leave an existing
record unchanged.

When a store opens, it scans Phase 42-era rows. Any row matching the current policy is
deleted in the same SQLite transaction. The only exposed migration diagnostic is
`removed_unsafe_on_startup`, an integer. Runtime logs that integer when nonzero and never
log the row's content. Quarantining was rejected because it would continue retaining the
secret, and redaction was rejected because a partial value may remain sensitive or alter
the memory's meaning.

## Interaction with existing systems

- Normal conversations and indexed documents are not scanned, rewritten, or deleted.
- Provider keys still come only from environment configuration and are used by the
  existing provider implementation; they are not copied into memory.
- RAG retrieval, prompts, citations, LLM providers, conversation context, and UI behavior
  are unchanged.
- Long-term-memory failure isolation remains intact. A policy or store initialization
  failure leaves memory unavailable while core chat/RAG continues.
- No candidate is automatically extracted or stored, and no memory enters prompts.

## Limits and tradeoffs

Pattern and entropy checks materially reduce common accidental retention but cannot
prove that arbitrary text is non-secret. A novel token format, an encoded secret, or a
secret described without recognizable structure can evade deterministic detection.
Conversely, a legitimate random-looking identifier can be rejected. This is why the
system is conservative, why confirmation remains required in Phase 45, and why the UI
must continue warning that AI memory is not a secret manager.

The policy intentionally blocks directly stated personal health/contact/profile details
rather than trying to infer whether they are necessary. Future requirements should add a
narrow, explicit consent policy instead of silently weakening this default.

## Verification

Focused safety/extraction/storage/API regression run:

```text
.venv/bin/python -m pytest -q \
  tests/test_memory_safety.py tests/test_memory_extraction.py \
  tests/test_long_term_memory.py tests/test_api_ui.py
45 passed, 1 warning in 3.99s
```

Complete regression suite:

```text
.venv/bin/python -m pytest tests/ -q
179 passed, 3 warnings in 11.00s
```

The 19 new test cases cover every listed reason family (including environment-style
credential labels), content-free findings, Luhn
validation, safe UUID/hash/project identifiers, rejected create and update operations,
unchanged safe data after rejection, candidate filtering, unsafe legacy-row removal, and
one shared runtime policy. The warnings remain the existing dependency deprecation and
two intentional legacy-environment deprecations; no test failed.
