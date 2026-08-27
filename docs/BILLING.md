# Apex AI subscriptions & business (phases 81–90)

`apex_billing.py` defines Free, Pro, and Business plans; tracks monthly questions/documents; gates premium actions; and returns billing-dashboard summaries. It includes Stripe-compatible signed webhook primitives without requiring the Stripe SDK in the core service.

Webhook payloads use the Stripe signed-payload pattern and timestamp tolerance. Subscription metadata carries internal `user_id` and `plan`. Production adapters should use Stripe Checkout, persist idempotency/event IDs, and never trust client-supplied plan values. Usage enforcement belongs server-side at the operation boundary, not only in UI controls.
