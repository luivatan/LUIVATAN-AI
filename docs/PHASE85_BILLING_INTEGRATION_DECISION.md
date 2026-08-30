# Apex AI Phase 85 — Billing Integration: Decision

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `051d77a`, immediately following Phase 81-84 (Subscription Plans)
- **Roadmap text:** "Connect a real payment provider in test mode first."
- **Decision: not implemented in this phase.** Before Section 8 began, the
  user was asked directly how to scope it and chose "architecture only, no
  real payment provider" — this phase's scope was settled by that choice,
  not discovered mid-phase the way Phase 75's web-search decision was.
  This doc records the reasoning for the record, the same way every other
  declined phase in this roadmap pass is documented rather than silently
  skipped.

## Why this can't be done for real right now

Connecting "a real payment provider in test mode" genuinely requires:

- **A real account with a payment provider** (Stripe is the natural fit
  given its `/test` mode and webhook conventions, but any would do) —
  something only the project owner can create, since it ties to a real
  business entity, bank details, and terms of service acceptance.
- **Real test-mode API keys and a webhook signing secret** — secrets that
  must never be invented, guessed, or hardcoded. This project's own
  ground rules are explicit about this: secrets come from the environment
  or `.env` only, never committed, never fabricated.
- **Real product/pricing decisions** — Phase 81-84 already built
  placeholder Free/Pro/Business numbers explicitly flagged as tunable, not
  as final pricing a real Stripe Product/Price object should be created
  from. Wiring a real checkout session against guessed prices would create
  real (if test-mode) billing objects that don't reflect an actual
  decision.

None of these can be satisfied by writing code alone. Building a Stripe
client wrapper with placeholder keys, or a checkout flow nothing can
actually complete, would be exactly the "fake billing-state" scaffolding
this roadmap's own ground rules explicitly forbid ("never fake ...
billing-states").

## What Phase 81-84/87-88 already prepared for this

The architecture is deliberately shaped so a real integration is additive,
not a rewrite:

- `SubscriptionStore.set_plan(user_id, plan_id, status)` is the exact call
  a webhook handler (Phase 86) would make when a checkout completes or a
  subscription changes — it already exists, is tested, and needs no
  changes to be called from real webhook code.
- `Plan`/`PlanLimits` are decoupled from any payment provider's own
  concepts (Stripe Price IDs, Products, etc.) — a real integration maps
  provider objects to these `plan_id` strings, not the other way around.
- Entitlement checks (Phase 87) run against `SubscriptionStore`/
  `UsageStore` only — they have no dependency on a payment provider being
  connected at all, so nothing about enforcement needs to change once
  billing is connected for real.

## What would make this real

1. The project owner creates a payment-provider account and test-mode
   Products/Prices matching (or replacing) the Free/Pro/Business numbers.
2. Test-mode API keys and a webhook signing secret land in `.env`
   (never committed) with new `APEX_*` settings following this project's
   existing secret-handling convention (`openai_api_key`'s
   `metadata={"secret": True}` pattern).
3. A checkout-session-creation endpoint and success/cancel redirect flow
   get built against the real API.
4. Phase 86's webhook handler verifies signatures and calls
   `SubscriptionStore.set_plan()` on real subscription-lifecycle events.

## Deliberately not done in this phase

- **No payment-provider SDK dependency added.** Adding one with no real
  account to use it against would be an unused, untestable dependency.
- **No checkout-session endpoint, redirect flow, or client-side payment
  form.** All would either be non-functional against no real backend, or
  would need to fake success/failure states to appear to work.
- **No placeholder/test API keys invented.** A fabricated key is not "test
  mode" - it's nothing, and pretending otherwise would be dishonest about
  what this integration does.
