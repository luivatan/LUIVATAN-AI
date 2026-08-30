# Apex AI Phase 81-84 — Subscription Architecture & Plans

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `f75a0b2`, immediately following Phase 80 (Reliability Layer)
- **Scope:** Phase 81 — "Define plans, limits, entitlements, and usage
  rules before connecting billing." Phase 82/83/84 — the Free/Pro/Business
  tiers themselves. Documented together: the roadmap's own ordering makes
  82-84 direct instances of the architecture 81 defines, not separate
  technical work. Built as the **architecture-only** scope the user chose
  for Section 8 — no real payment provider is connected (see
  `docs/PHASE85_BILLING_INTEGRATION_DECISION.md`), so a plan change here is
  an administrative action (`SubscriptionStore.set_plan()`), not a
  self-serve checkout flow.

## Design: two kinds of limit, checked two different ways

`apex_ai/billing/plans.py`'s `PlanLimits` distinguishes:

- **Capacity** (`max_documents`, `max_storage_mb`, `max_collections`,
  `max_projects`) — a live current-state cap. Deleting a document frees
  room again; this is never a cumulative-ever-created tally. Checked
  against a caller-supplied live count (Phase 87 wires the real counts in).
- **Rate** (`max_messages_per_month`, `max_tool_calls_per_month`) — a flow
  cap over the current calendar month, the one period granularity this
  architecture uses. Checked against `UsageStore`'s ledger (Phase 88).

`None` means unlimited for either kind. This split exists because the two
genuinely behave differently — a document count naturally goes back down
when something is deleted; a month's message count never "un-happens."

## The three tiers

| | Free | Pro ($19/mo) | Business ($99/mo) |
|---|---|---|---|
| Documents | 20 | 500 | unlimited |
| Storage | 200 MB | 5 GB | 50 GB |
| Collections | 3 | 25 | unlimited |
| Projects | 1 | 10 | unlimited |
| Messages/month | 100 | 2,000 | 10,000 |
| Tool calls/month | 50 | 1,000 | 5,000 |

These are real, sensible placeholder numbers — genuinely enforceable
(Phase 87 wires real enforcement against them) but explicitly not
researched pricing/market decisions; tune them via `SubscriptionStore`
once real usage data or business input exists. **Free is a real usable
tier**, not a crippled demo: 20 documents and 100 messages/month is enough
to genuinely try the product with a real small document set and a real
conversation history, matching Phase 82's "useful free tier" requirement.

**Business tier is deliberately not "team/seats" functionality.** Phase
84's roadmap text says "team-oriented features where justified" — this
codebase has no multi-user-per-account (organization) data model at all
today: Phase 55's isolation discipline scopes every store strictly to one
account, with no concept of inviting another person into your account's
data. Building real seats/shared-workspace functionality is a separate,
much larger feature (an entirely new tenancy model) than a subscription
tier, and was not justified as part of an architecture-only billing pass.
Business is a real, distinct tier — materially higher numeric limits, its
own feature labels — without pretending team functionality exists.

**`Plan.features`** is a set of string labels (`"priority_support"`,
`"dedicated_support"` on Pro/Business) a future feature could key off of
via `EntitlementService.has_feature()` — a real, tested check. Nothing in
the current codebase gates any behavior on these yet: there is no priority
support ticketing system, no capability today that would legitimately
differ by plan beyond the numeric limits. Rather than invent fake
enforcement for a capability that doesn't exist (a support system, a
premium model tier), this phase leaves the labels real and checkable but
honestly unused — the same restraint Phase 73/77 applied to
tool-calling/structured-output capabilities with no live caller yet.

## Files

- `apex_ai/billing/__init__.py`, `plans.py`, `subscriptions.py` (new
  package; `usage.py`/`entitlements.py` also land in this phase's package
  but their real *enforcement* wiring is Phase 87-88, documented
  separately)
- `apex_ai/config/settings.py`, `.env.example`, `README.md` —
  `billing_db_path` / `APEX_BILLING_DB_PATH`
- `apex_ai/runtime.py` — `services.subscriptions`, `services.usage`,
  `services.entitlements`
- `apex_ai/api/billing.py` (new) — `GET /billing/plans` (the public
  catalog), `GET /billing/plan` (the caller's current subscription)
- `apex_ai/api/schemas.py` — `PlanOut`, `PlanLimitsOut`, `SubscriptionOut`
  (plus `EntitlementOut`/`UsageSummaryOut`, used starting Phase 87-88)
- `tests/test_billing.py` (new), additions to `tests/test_api_ui.py`

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 522 passed, 3 skipped |
| `tests/test_billing.py` | 28 passed |
| `ruff check` on every touched/new file | clean |

## Deliberately not done in this phase

- **No real payment provider, checkout flow, or self-serve upgrade.**
  `SubscriptionStore.set_plan()` is real and tested, but nothing in the API
  lets a user call it themselves yet — see
  `docs/PHASE85_BILLING_INTEGRATION_DECISION.md`.
- **No entitlement enforcement wired into a live request path yet.** This
  phase defines what accounts are entitled to; Phase 87-88 wires the real
  checks into chat/uploads/collections/projects and records real usage.
- **No team/seat/organization data model**, for the reasoning above.
- **No feature-gated behavior for `Plan.features` labels.** Real and
  checkable, honestly unused until a feature exists that should differ by
  plan.
