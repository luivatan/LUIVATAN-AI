# Apex AI Phase 60 — Security Testing

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `8e2e906` (Phase 59 secret management)
- **Scope:** the roadmap names six categories — authentication, authorization,
  user isolation, file access, API abuse, and common security failure cases.
  Five of the six already had substantial, real coverage built incrementally
  across Phases 51-59; this phase's job was to (1) verify that coverage is
  genuine rather than assumed, (2) find and close the gaps a category-by-
  category audit turns up, and (3) leave one map so the coverage is
  discoverable in one place instead of scattered across nine files.

## Coverage map (verified by running each file directly, not assumed)

| Category | Primary coverage | Tests |
|---|---|---|
| Authentication | `tests/test_auth.py` — password hashing/verification, constant-shape login failures, session create/expire/delete, signup/login/logout end-to-end, cookie precedence over auto-login, default-account idempotency | 32 passed |
| Authorization | `tests/test_api_ui.py`, `tests/test_conversations_web.py` — `require_user` 401 vs 503 distinction, auto-login fallback, every route behind `Depends(require_user)` | 18+3, 26 passed |
| User isolation | `tests/test_long_term_memory.py`, `tests/test_memory_confirmation.py`, `tests/test_vectordb.py`, `tests/test_retrieval.py`, `tests/test_conversations_web.py` — cross-account isolation for conversations, memory, pending candidates, documents, BM25, and one full two-real-accounts-through-the-HTTP-API test | 7, 9, 11, 9, 26 passed |
| File access | `tests/test_documents.py` — path traversal, filename sanitization, oversized upload, unsupported type, owner-only permissions | 17 passed |
| API abuse | `tests/test_api_security.py` — general and auth-specific rate limits, static-asset exemption, CORS absent/present states | 6 passed |
| Common security failure cases | `tests/test_error_handling.py`, `tests/test_memory_safety.py`, `tests/test_evaluation_security.py`, `tests/test_security_regression.py` (new) — safe error shapes, secret-pattern detection in memory content, path containment, SQL injection, cookie hardening flags, malformed-input robustness, client-side XSS escaping order | 9, 19, 12, 5 passed |

Every count above was produced by running `python3 -m pytest tests/<file> -q`
directly during this phase, not carried over from an earlier phase's
self-report.

## What this phase specifically audited and found clean

- **SQL injection.** Searched every `.execute(...)` call site across
  `apex_ai/` for string-built SQL. Found exactly two patterns: (1) DDL
  migration helpers (`_add_owner_column`, `backfill_owner` in
  `apex_ai/memory/long_term.py`) that interpolate table/index *names* via
  f-string — safe, because every call site passes a hardcoded internal
  constant, never a request-derived value, and SQLite's `?` parameter
  binding doesn't support identifier substitution anyway (this is the
  standard, correct pattern for DDL with fixed schema names). (2)
  `ConversationStore.list()`'s search query, which f-string-assembles a
  `WHERE` clause from fixed boolean fragments while the actual search term
  always travels through a bound `?` parameter with explicit `LIKE`-wildcard
  escaping. Everywhere else, every user-supplied value already went through
  parameterized queries. `test_conversation_titles_and_search_treat_sql_payloads_as_literal_text`
  and `test_message_content_with_sql_payloads_is_searchable_as_literal_text`
  (new) lock this in as a regression test: four classic SQLi payloads
  (`'; DROP TABLE conversations; --'`, `' OR '1'='1'`, etc.) as titles,
  search terms, and message content, asserting the payload round-trips as
  literal text and the table survives.
- **Cookie hardening.** `_set_session_cookie` (`apex_ai/api/auth.py`) already
  set `httponly=True` and `samesite="lax"` since Phase 52, but nothing
  verified those flags actually reached the wire — a regression removing
  either would have shipped silently. `test_session_cookie_is_httponly_and_samesite_lax`
  (new) reads the real `Set-Cookie` header from a live `/auth/signup`
  response and asserts both flags are present. `HttpOnly` blocks JavaScript
  from reading the token (the standard mitigation for token theft via XSS,
  already documented in Phase 51-53); `SameSite=Lax` is the primary CSRF
  defense for every mutating route Phase 54 gated behind authentication —
  worth re-verifying now specifically *because* Phase 54 made those routes
  real, which is exactly the moment the Phase 51-53 doc flagged as worth
  revisiting ("worth revisiting once real mutations are gated behind real
  accounts").
- **Malformed session input.** `SessionStore.get()` already parameterizes
  its lookup and returns `None` for any non-matching token rather than
  raising, but this was unverified against a hostile-shaped value.
  `test_garbage_session_cookie_is_rejected_not_crashed` (new) sends a SQL-
  injection-shaped string as the session cookie itself and asserts the
  request fails safely (falls back to auto-login or `401`) rather than
  raising or leaking a `500`.
- **Client-side XSS.** Read `renderMarkdown()` and `highlightCode()` in
  `apex_ai/web/static/app.js` in full. Confirmed the design is
  escape-then-transform: raw source text is passed through `escapeHTML()`
  immediately after code-block extraction, *before* any markdown-to-HTML
  regex transform runs — so literal `<script>`/`<img onerror>` text
  anywhere in an LLM answer or a retrieved citation becomes `&lt;script&gt;`
  before the markdown parser ever sees it, and link URLs are separately
  constrained to `http:`/`https:` only via `safeLink()`. This is the correct
  pattern for a hand-rolled markdown renderer without an HTML allowlist
  parser. **Caveat, stated plainly:** this project has no JavaScript
  execution harness (no Node/jsdom test runner; the existing JS test
  coverage — already established before this phase, e.g.
  `test_static_assets_include_responsive_themes_and_code_blocks` — is
  entirely static string-presence assertions against the shipped bundle,
  never actual execution). This phase could not add a genuine *behavioral*
  test proving `renderMarkdown("<script>...</script>")` produces safe
  output, because nothing in the test stack can run that JavaScript. What
  it *could* honestly add is `test_escape_html_runs_before_markdown_transforms_in_the_chat_renderer`
  (new): a regression guard asserting the escaping call appears before the
  first HTML-producing transform in the function's source — it would catch
  a future edit that reordered or removed the escape step, but it cannot
  substitute for real execution. Documented here rather than passed off as
  full behavioral coverage, per the roadmap's own honesty rule.

## Files

- `tests/test_security_regression.py` (new) — the five tests described
  above, explicitly scoped to cross-cutting checks that don't belong to any
  single earlier phase's test file.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 311 passed, 3 skipped |
| `tests/test_security_regression.py` | 5 passed |
| `ruff check tests/test_security_regression.py` | clean |
| Manual audit: every `.execute(` call site in `apex_ai/` for string-built SQL | 2 patterns found, both safe (see above) |
| Manual read: `renderMarkdown()`/`highlightCode()`/`safeLink()` in `apex_ai/web/static/app.js` | escape-then-transform confirmed; execution-testing gap documented, not hidden |

## Deliberately not done in this phase

- **No JS execution test harness added** (Node + jsdom, or similar) to get
  genuine behavioral XSS coverage. This would be real, valuable
  infrastructure work, but it's a build-tooling addition orthogonal to
  "test the six security categories with what exists" — flagged here as a
  legitimate follow-up rather than silently worked around with a test that
  only *looks* like it proves runtime safety.
- **No dependency/CVE scanning pass** (`pip-audit`, `safety`, or similar).
  Static dependency-vulnerability scanning is a CI/tooling concern most
  naturally paired with Phase 91+ (Production & Reliability)'s broader
  pipeline work, not a one-off manual pass here.
- **No penetration-testing-style fuzzing.** The tests added are targeted,
  hypothesis-driven regressions for the specific gaps this audit actually
  found, not a generic fuzz harness — consistent with the roadmap's
  "no complexity for its own sake" principle.
