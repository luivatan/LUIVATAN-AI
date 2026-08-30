# Apex AI Phase 100 — Scale Carefully (Decision)

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `c20e61a` (Phase 97, demo experience), alongside Phases
  98 and 99 (customer validation and sales system decisions)
- **Roadmap scope:** "Only after customers use and pay for Apex AI,
  improve infrastructure, model economics, reliability, team features,
  marketing, and automation. Prioritize features based on real customer
  demand and measured usage."
- **Decision:** declined for real execution this pass — documented here
  instead, following the pattern used for Phases 75, 85, 86, 89, 90, 91,
  93, 94, 98, and 99. This is the roadmap's final phase; declining it
  for the reason the roadmap itself states closes the 100-phase roadmap
  honestly rather than papering over the gap.

## Why this can't be done for real here

The roadmap gates this phase on its own precondition, stated in its
first word: **"Only** after customers use and pay for Apex AI." That
precondition is unmet — Phases 85/86/89/90 (real payment integration)
and 98/99 (real customers, real sales) are all declined above for
needing real credentials, real infrastructure, or real people this
session cannot produce. Since the roadmap's own gate hasn't opened,
scaling "based on real customer demand and measured usage" has no real
demand or usage to prioritize against. Guessing at what to scale first
would be exactly the kind of decision the roadmap explicitly says to
make only from real evidence — doing it anyway, without that evidence,
would defeat the entire point of the gate.

## What already exists that real scaling would build on

Every prerequisite this phase would actually need is already real and
already built:

- **Measured usage infrastructure** (Phase 87-88) — `GET /billing/usage`
  already reports real, live per-account usage against real plan limits;
  once real accounts exist, "measured usage" is already there to
  prioritize against, not something to build later.
- **A real backup/recovery story** (Phase 92) — scaling a system without
  a provably restorable backup first would be scaling on top of an
  unverified foundation; that groundwork is already done.
- **A measured, fixed performance bottleneck** (Phase 95) — the
  retrieval path was already profiled and a real O(n) inefficiency
  removed with before/after numbers, rather than left for a future
  "scale" phase to rediscover under real load.
- **Real plan/entitlement architecture** (Phase 81-84) — the "model
  economics" half of this phase's scope (what each tier costs to serve
  vs. what it charges) has real, enforced limits per tier to reason
  about the moment real payment is connected.
- **A real landing page and demo** (Phases 96, 97) — the "marketing"
  half of this phase's scope already has real, working starting
  material rather than nothing.

## What would make this real

Exactly what the roadmap says, in order: get real paying customers
first (which itself requires completing Phases 85/86/89/90 and 98/99
for real, with real credentials and real people), then let their actual
behavior — which features they use, where they hit plan limits, what
they ask for, what breaks under their real load — decide what to build
next. Scaling infrastructure, reliability, or team features ahead of
that evidence isn't "scaling carefully," it's the opposite of what this
phase asks for.

## Closing note: state of the roadmap

This closes Section 9 and the 100-phase Apex AI roadmap. Every phase
that could be built with real, verifiable, testable code was built, run,
tested, and documented (`docs/PHASE*.md` for the buildable phases).
Every phase that genuinely required external credentials, infrastructure
this session doesn't have, or real people this session cannot produce
was declined explicitly, with a decision doc naming exactly what's
missing and what already exists to build on once it's available — never
silently skipped, and never faked. That is itself the roadmap's own
ground rule ("never fake features, data, ... or business/billing
states") applied consistently through to the very last phase.

## Deliberately not done in this phase

- No speculative infrastructure scaling, team-feature build-out, or
  automation work done ahead of real customer demand — the roadmap
  explicitly gates this phase on evidence that doesn't exist yet, and
  building ahead of it would be guessing dressed up as "scaling."
- No fabricated "real customer demand" data used to justify any of the
  above.
