# Apex AI Phase 99 — Sales System (Decision)

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `c20e61a` (Phase 97, demo experience), alongside Phase 98
  (customer validation decision)
- **Roadmap scope:** "Create repeatable outreach, demos, onboarding,
  customer support, and feedback processes focused on a specific target
  market."
- **Decision:** declined for real execution this pass — documented here
  instead, following the pattern used for Phases 75, 85, 86, 89, 90, 91,
  93, 94, and 98.

## Why this can't be done for real here

A "sales system" is a set of real, repeated business processes run
against real people in a real target market — outreach that actually
reaches prospects, support that actually responds to real customers,
feedback loops that actually process real input. None of that has
anything to run against yet:

- **No specific target market has been chosen.** That's explicitly a
  business decision (which vertical, which buyer, what messaging) — it
  depends on Phase 98's customer validation, which is itself declined
  above for needing real customers this session cannot produce.
- **"Repeatable" implies it has been run at least once for real.** A
  sales process nobody has ever executed isn't repeatable — it's a
  guess. Writing a detailed outreach/onboarding/support playbook against
  a market that hasn't been chosen and prospects that don't exist would
  produce untested process documentation dressed up as if it were
  proven — the same "looks done but isn't" problem this roadmap's ground
  rules warn against throughout.
- **Customer support and feedback processes need real customers to
  support.** There's nothing to build a support process around yet.

## What already exists that a real sales system would use

- **Phase 96's landing page** and **Phase 97's demo** are themselves the
  first two links in an outreach → demo chain — a real sales process
  would start by pointing prospects at them.
- **Phase 81-84's real plan/pricing architecture** gives a sales
  conversation concrete tiers to discuss, not a made-up price.
- **`GET /billing/usage`** (Phase 87-88) gives a support/success process
  a real, live source of truth for "what is this account actually
  doing" the moment there's a real account to look at.
- **This project's own decision-doc discipline** (this file included) is
  itself the honest starting point for a feedback process: every
  declined phase names exactly what's missing and what would need to be
  true to build it for real, which is the same information a sales/
  customer-success process would need to prioritize against.

## What would make this real

1. Choose a specific target market (depends on Phase 98).
2. Run real outreach to real prospects and record what actually happens
   — reply rates, objections, what messaging lands — rather than
   authoring a playbook in the abstract.
3. Turn what actually works into a repeatable process only after it has
   worked more than once.
4. Stand up real customer support once there are real customers to
   support (a shared inbox, a support tool, or a simple documented
   process — sized to actual, not hypothetical, volume).

## Deliberately not done in this phase

- No invented target-market persona, outreach templates, or sales
  playbook presented as if it were tested — writing one without a real
  market and real prospects to test it against would be exactly the
  "looks done but isn't" artifact this roadmap's ground rules exist to
  prevent.
- No fabricated customer-support process or feedback pipeline for
  customers that don't exist yet.
