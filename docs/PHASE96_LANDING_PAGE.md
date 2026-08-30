# Apex AI Phase 96 — Landing Page

- **Completed:** 2026-08-30 (America/Chicago)
- **Baseline:** `f248680` (Phase 95, retrieval performance optimization)
- **Scope:** "Create a polished public website explaining the customer
  problem, solution, features, pricing, and call to action."

## Design: real content, never a fake checkout

`GET /welcome` (new) serves the public marketing page, separate from the
authenticated app shell at `/`. It covers, in order: the problem (generic
AI chatbots can't be trusted with private documents and hallucinate),
the solution (grounded, cited, offline-capable answers), six real
product features drawn directly from what's actually built (hybrid
retrieval, honest citations, collections/projects, model-agnostic
providers, verified backups, entitlement enforcement), a pricing
section, and two calls to action.

**Pricing is rendered from real data, not written into the template.**
`apex_ai/web/landing.py`'s `render_landing_html()` builds the pricing
cards directly from `apex_ai.billing.plans.PLANS` (the real Free/Pro/
Business plan objects from Phase 81-84) — prices, capacity limits, and
rate limits are formatted from the same `Plan`/`PlanLimits` dataclasses
the entitlement service enforces against. If those plans ever change,
this page updates automatically; it cannot silently drift into
advertising numbers the product doesn't actually enforce.

**No fake billing state.** Billing integration itself is deliberately
not connected (`docs/PHASE85_BILLING_INTEGRATION_DECISION.md`) — every
new account starts on the Free plan regardless of which pricing card its
owner looked at first. Rather than presenting a "Subscribe" button that
would silently do nothing (or worse, imply payment was collected), every
plan's call to action is the same honest "Get started" link to the real
`/login` sign-up flow, and the pricing section states plainly that
self-serve upgrades to paid tiers aren't available yet. A test
(`test_landing_page_never_implies_a_working_checkout`) pins this: the
page must never contain "credit card," "checkout," or "enter payment."

## Implementation notes

- No JavaScript, no inline `<style>`/`style=""` attributes — the app's
  existing `Content-Security-Policy` (`script-src 'self'; style-src
  'self'`, no `'unsafe-inline'`) applies to every route via
  `BrowserSecurityHeaders`, including this new one, and the page is
  static server-rendered HTML that doesn't need a script to work.
- `apex_ai/web/static/landing.css` holds only the page's own layout
  (nav, hero, feature grid, pricing cards); it relies on `app.css`'s
  existing `:root` design tokens and shared `.primary-button`/
  `.quiet-button`/`.brand-mark` classes for visual consistency with the
  rest of the app rather than duplicating them.
- `login.html` gained a small "← About Apex AI" link back to `/welcome`
  for discoverability; `/` (the authenticated app shell) is unchanged.
- Verified in a real browser: launched the app with
  `HashingEmbeddingProvider` (no network/model download needed) and used
  the pre-installed Playwright/Chromium to render and screenshot both
  `/welcome` and `/login` at 1280px width, confirming layout, the
  Pro-plan highlight, and real plan data all render correctly. Caught
  and fixed a real copy bug this way: the Free plan's `max_projects=1`
  rendered as "1 projects" — `_format_count()` now singularizes when the
  value is exactly 1.

## Files

- `apex_ai/web/landing.py` (new) — `render_landing_html()` and pure
  formatting helpers (`_format_price`, `_format_count`,
  `_format_storage`, `_limit_lines`, `_plan_card`)
- `apex_ai/web/static/landing.css` (new)
- `apex_ai/web/app.py` — new `GET /welcome` route
- `apex_ai/web/templates/login.html`, `apex_ai/web/static/app.css` — small
  back-link to the new page
- `README.md` — new "Landing page" section plus a features-list bullet
- `tests/test_landing.py` (new) — 6 tests for the pure formatting helpers
  and the rendered HTML's shape
- `tests/test_conversations_web.py` — 4 new integration tests via the
  existing `web_client` fixture

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 553 passed, 3 skipped (up from 544) |
| `tests/test_landing.py` | 6 passed |
| New tests in `tests/test_conversations_web.py` | 4 passed |
| Real browser check (Playwright/Chromium, `/welcome` and `/login`) | rendered correctly; caught and fixed a real pluralization bug |
| `ruff check` on every new/touched file | clean |

## Deliberately not done in this phase

- **No working checkout or payment collection** — see "No fake billing
  state" above; this is the same reasoning behind Phases 85/86/89/90.
- **No analytics/visitor tracking** — would need a real third-party
  service and a real privacy policy decision, neither of which exists
  here; adding a tracking script without deciding on user consent/privacy
  handling would be a real product decision made silently.
- **No SEO/sitemap/meta-tag work beyond the basics already present**
  (title, description) — meaningful SEO needs a real deployed domain
  (Phase 91, declined) to mean anything.
- **`/` (the authenticated app shell) was left unchanged** — replacing it
  with the marketing page would be a real behavior change for the
  existing single-default-account deployment model this session's
  architecture decision established, not something this phase's scope
  asked for.
