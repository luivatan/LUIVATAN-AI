# Apex AI Phase 98 — Customer Validation (Decision)

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `c20e61a` (Phase 97, demo experience)
- **Roadmap scope:** "Put Apex AI in front of real potential customers.
  Track objections, requested features, willingness to pay, and actual
  usage."
- **Decision:** declined for real execution this pass — documented here
  instead, following the pattern used for Phases 75, 85, 86, 89, 90, 91,
  93, and 94.

## Why this can't be done for real here

This phase, by its own wording, requires **real potential customers** —
actual people outside this session, in a real target market, using the
product and giving real reactions. There is no way to satisfy that from
inside a coding session:

- This session has no relationship with, access to, or ability to
  contact real prospective customers.
- "Track objections, requested features, willingness to pay, and actual
  usage" describes data that can only come from real human interactions
  and real usage over time — it cannot be estimated, simulated, or
  invented without becoming exactly the kind of fake data the roadmap's
  own ground rules forbid.
- Fabricating placeholder "customer feedback" (invented quotes, made-up
  objections, a fake willingness-to-pay figure) would be worse than
  doing nothing: it would misdirect real future prioritization decisions
  around data that was never real.

## What already exists that real customer validation would use

Nothing about this phase was blocked on validation — the product and the
materials a validation effort would actually use are already built:

- **Phase 96's landing page** (`GET /welcome`) — a real page explaining
  the problem, solution, features, and real pricing, ready to send to a
  real prospect today.
- **Phase 97's demo** (`docs/DEMO_SCRIPT.md`, `scripts/seed_demo.py`) —
  a real, repeatable, fact-checked walkthrough usable in an actual
  conversation with a prospective customer.
- **Phase 81-84's plan architecture** — real Free/Pro/Business tiers
  with real, enforced limits, so a "willingness to pay" conversation has
  something concrete and already-functioning to react to (even though
  actual checkout isn't wired in yet — Phase 85's decision doc).
- **`GET /billing/usage`** (Phase 87-88) — real, live usage data per
  account, which is exactly the "actual usage" half of this phase's
  scope, ready to observe the moment a real account starts using the
  product.

## What would make this real

1. Identify a specific real target market (the roadmap's own Phase 99
   also depends on this decision, so it would naturally happen together).
2. Put the real landing page and real demo in front of real prospective
   users or customers.
3. Record their actual objections, feature requests, and stated
   willingness to pay — as direct quotes/notes, not paraphrased or
   invented after the fact.
4. Observe real usage through the already-built `GET /billing/usage` and
   usage-ledger data (Phase 87-88) rather than assuming what "actual
   usage" would look like.
5. Feed the findings back into prioritization — which is explicitly what
   Phase 100 (declined below, for the same reason) is gated on.

## Deliberately not done in this phase

- No invented customer quotes, objections, or feature requests anywhere
  in the repo or docs.
- No fabricated "willingness to pay" figure or usage numbers presented
  as if they came from real people.
- No claim anywhere that customer validation has happened.
