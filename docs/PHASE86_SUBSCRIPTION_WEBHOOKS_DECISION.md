# Apex AI Phase 86 — Subscription Webhooks: Decision

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `b239dba`, immediately following Phase 85 (Billing Integration decision)
- **Roadmap text:** "Implement secure webhook processing for subscription
  changes."
- **Decision: not implemented in this phase.** This phase depends entirely
  on Phase 85, which was declined — there is no payment provider connected
  to send a webhook, no signing secret to verify one against, and no real
  subscription-lifecycle event ("checkout completed," "subscription
  renewed," "payment failed") that could ever actually arrive.

## Why this can't be done for real right now

A webhook handler that's real, not decorative, needs:

- **A real event source.** Without Phase 85, no payment provider ever
  sends anything to this application. A route that exists but never
  receives a genuine event is untestable against the one thing that
  matters — a real signed payload — and any test built against a
  hand-crafted fake payload would only prove the code parses *fake*
  Stripe-shaped JSON, not that it handles the real thing correctly.
- **A real webhook signing secret**, to verify a payload actually came
  from the payment provider and not an attacker who found the endpoint
  URL. Building signature verification against a secret that doesn't
  exist would mean either skipping verification (a real security hole:
  "secure webhook processing" requires exactly this check) or verifying
  against a fabricated secret that would reject every real webhook a
  connected provider ever sent — neither is "secure webhook processing,"
  both are the appearance of it.

## What Phase 81-84/85 already prepared for this

`SubscriptionStore.set_plan(user_id, plan_id, status)` (Phase 81) is
already the exact, tested call a webhook handler would make on a
subscription-lifecycle event — a payment succeeded → `set_plan(user_id,
new_plan_id, "active")`; a subscription canceled → `cancel(user_id)`
(reverts to free). Building the webhook layer once Phase 85 exists is
wiring a verified event to a call that already works, not new
architecture.

## What would make this real

1. Phase 85 exists: a real payment provider account, connected, with
   `Product`/`Price` objects and a way to actually reach checkout.
2. A webhook endpoint verifies the provider's signature against
   `APEX_STRIPE_WEBHOOK_SECRET` (or equivalent), read from `.env` like
   every other secret in this project — never hardcoded, never logged.
3. The handler maps the provider's `customer`/`subscription` identifiers
   to an Apex AI `user_id` (a mapping this phase would also need to
   establish — e.g. storing the provider's customer ID on the
   `subscriptions` row) and calls `SubscriptionStore.set_plan()`/
   `cancel()` accordingly.
4. Idempotency: the same webhook event can be delivered more than once by
   real payment providers: the handler needs to recognize and no-op a
   repeat delivery (e.g. by event ID), not double-apply a plan change.

## Deliberately not done in this phase

- **No webhook route, signature verification, or event-parsing code.**
  All would be unreachable/untestable against a real signed payload
  without Phase 85, and would risk shipping a security-sensitive code path
  (payload verification) that was never actually exercised against the
  real thing it exists to verify.
- **No fabricated webhook secret or hand-crafted "sample" Stripe payloads
  treated as if they proved real handling.** That would be exactly the
  "fake billing-state" scaffolding this roadmap's ground rules prohibit —
  it would look tested without actually being verified against anything
  real.
