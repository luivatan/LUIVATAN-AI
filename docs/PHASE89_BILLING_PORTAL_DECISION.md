# Apex AI Phase 89 — Billing Portal: Decision

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `b9c4335`, immediately following Phase 87-88 (Entitlements & Usage)
- **Roadmap text:** "Allow customers to manage subscriptions, payment
  methods, and cancellations through the supported billing system."
- **Decision: not implemented in this phase.** A billing portal manages
  real subscriptions and real payment methods against a real payment
  provider. Phase 85 (connecting one) was declined, so there is nothing
  for a portal to actually manage — building one now would mean a UI that
  can only ever show fabricated state.

## Why this can't be done for real right now

"Through the supported billing system" is the operative phrase: a real
billing portal is either the payment provider's own hosted portal (e.g.
Stripe's Billing Portal, which requires a connected Stripe account and a
configured portal session) or a custom UI built against that provider's
real subscription/payment-method objects. Without Phase 85:

- **No payment method exists to display, add, or remove.** There is no
  card, no bank account, nothing on file anywhere — a "payment methods"
  section would have to either show an empty state forever or fabricate
  entries, and the latter is exactly the fake billing-state this roadmap's
  ground rules prohibit.
- **"Cancellation" already has a real, honest answer today.**
  `SubscriptionStore.cancel()` (Phase 81) reverts an account to the free
  plan immediately — real, tested, callable right now by whoever
  administers plans. What a portal would add on top is a *real payment
  provider's* cancellation semantics (e.g. "stays on the paid plan until
  the current billing period ends, refund policy X") — details that don't
  exist without a real subscription with a real period to reference.

## What Phase 81-88 already prepared for this

Nothing about a future portal needs to change what already exists:
`GET /billing/plan` and `GET /billing/usage` (Phase 81-84/88) already give
a caller everything true and real about their own subscription and usage
today — a portal's "current plan" and "usage this month" sections would
read from exactly these endpoints, unchanged. `SubscriptionStore.set_plan()`/
`cancel()` are the same calls a portal's "change plan"/"cancel" actions
would ultimately make once there's a real payment provider's confirmation
flow in front of them.

## What would make this real

1. Phase 85 exists: a real payment provider connected, with real
   subscriptions and payment methods on file for at least one account.
2. Either the provider's own hosted portal is configured and linked to
   from Settings (the lower-effort, well-trodden path most SaaS products
   take), or a custom UI is built against the provider's real
   payment-method/subscription API.
3. A cancellation flow surfaces the provider's real period-end semantics,
   not just the immediate free-plan reversion `SubscriptionStore.cancel()`
   gives today.

## Deliberately not done in this phase

- **No payment-method management UI or API**, since there is nothing real
  to manage.
- **No custom "manage subscription" flow.** `GET /billing/plan` already
  exists and is real; a management *action* beyond what
  `SubscriptionStore` already provides needs a real payment provider
  behind it first.
