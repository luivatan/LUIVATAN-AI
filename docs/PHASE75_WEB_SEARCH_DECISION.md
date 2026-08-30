# Apex AI Phase 75 — Web Search Integration: Decision

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `bd74cbf`, immediately following Phase 74 (Tool Permissions)
- **Roadmap text:** "If implemented, create controlled web search with
  source attribution and clear separation between web evidence and
  document evidence."
- **Decision: not implemented in this phase.** The roadmap's own "if
  implemented" qualifier is genuine license to evaluate this against the
  project's core principles and decline when it conflicts — the same
  standard this session has applied to every other "if implemented"/
  "where useful" phase — and it does conflict, on more than one axis.

## Why this phase is declined

**It breaks the offline-first guarantee, not just extends it.**
`README.md` states as the very first line of "Features": *"Offline-first —
after one-time model downloads, everything runs locally: no internet is
needed for inference, embeddings, retrieval, or chat."* This is not an
incidental detail — it is stated first, repeated in the project description
("an offline-first, model-agnostic RAG assistant"), and it is the reason
Apex AI has a local-provider-first architecture (`llama_cpp` as the default
`APEX_LLM_PROVIDER`) instead of defaulting to a hosted API. A "controlled
web search" feature means chat can now, for some questions, require a live
network call to a third-party search API before an answer exists at all.
That is not a configuration knob within the offline-first design — it is a
different guarantee, silently narrower than what the README currently
promises for every other feature. Retrieval, embeddings, and generation
staying local was true before this phase and remains true after declining
it.

**It doesn't fit the anti-hallucination contract as it exists today.**
The core prompt contract (`apex_ai/rag/prompts.py`) is built around exactly
one evidence class: retrieved document chunks, numbered `[n]`, with a
SOURCE/PAGE/SECTION header, cited only when they directly support a claim.
Web search would need to become a *second*, differently-trusted evidence
class — unmoderated, unindexed, not subject to the same chunking/embedding/
retrieval pipeline, and updated on every query rather than at ingestion
time. "Clear separation between web evidence and document evidence" is
exactly right as a requirement, but satisfying it honestly means a second
citation system, a second confidence/support gate, and a second set of
citation UI affordances (the existing source drawer assumes a chunk with a
page/section from an indexed document) — a substantial net-new subsystem,
not an extension of the existing one.

**It raises the risk profile in a domain this project treats carefully.**
Apex AI carries an explicit medical-use disclaimer and is designed, by its
own description, to be medically careful: informational only, never a
diagnosis, always pointing back to what an uploaded, presumably
user-vetted, document says. Web search results are not vetted by the user
the way an uploaded document is — introducing live, uncurated web content
into the same conversation as citations from a medical document a user
chose to trust is a materially different trust boundary than anything else
in this codebase, and deserves a deliberate design pass of its own rather
than a checkbox feature bolted onto Phase 76-80's remaining, much smaller
scope.

## What would change this decision

This is not a permanent architectural prohibition — it is a scoping
decision for *this* roadmap pass, given the offline-first guarantee as it
is stated today. A future phase could reconsider web search once:

- it is explicitly opt-in and clearly labeled as changing the offline
  guarantee for that request (the same way choosing `APEX_LLM_PROVIDER=
  openai_compatible` is already an explicit, documented choice to leave
  the local-only default), and
- a genuinely separate evidence/citation pipeline for web results exists
  (not reusing the document-evidence prompt contract wholesale), and
- the medical-safety implications of mixing web and document evidence in
  one answer have their own explicit design review.

## Deliberately not done in this phase

- **No web search provider, HTTP client, or API key configuration added.**
  Nothing about this decision required writing code; adding an unused
  provider stub would be exactly the "fake feature" scaffolding the
  roadmap's own ground rules prohibit.
- **No README/`.env.example` changes for a feature that doesn't exist.**
  Only the `Limitations` section notes the decision and why, so it is
  discoverable without implying partial support exists.
