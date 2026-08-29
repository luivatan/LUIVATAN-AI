# Apex AI Phases 51–53 — User Accounts, Authentication, Password Security

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `00989e1` (Phase 50, end of Section 4)
- **Scope:** the first three phases of Section 5. Real accounts (51), sign-in/sign-up
  with session handling (52), and the password-hashing choice/audit (53) are one
  cohesive piece of work — you cannot meaningfully build "an account" without
  deciding how it authenticates, so they land together, same as Phases 41–45 did for
  memory. **Authorization enforcement and per-user data isolation (Phases 54/55) are
  separate, later work — this phase adds the capability, not yet the enforcement.**

## A real architectural pivot, not an incremental phase

Every phase before this one operated inside "single-user local application" as a
fixed premise (`docs/PHASE2_ARCHITECTURE_MAP.md` states this explicitly as the
supported trust boundary). Section 5 changes that premise. Given the scale and
irreversibility of that shift — it changes what "using Apex AI" means for whoever
runs it — this was the one point in the session where I stopped and asked the user
how it should behave, rather than picking a default myself: should local usage
require login at all, and if so, does today's frictionless `python ui.py` experience
survive it? The user chose **"auth required, single default account"** — a real login
screen exists, but a default local account is auto-provisioned and auto-logged-in so
a single machine still needs no visible login step, while the same server can now
safely host real, distinct accounts too. Everything below implements that choice.

## Design

### Storage: SQLite, not JWT

Sessions are an opaque `secrets.token_urlsafe(32)` token mapped to a row in
`data/users.db` (`sessions` table, sharing the file with `users` — same
one-file-per-concern pattern as `conversations.db` holding both `conversations` and
`messages`). No JWT library, no signing key to manage or rotate, no blocklist for
revocation: logout is one `DELETE`. This is the same "boring, auditable, easy to
reason about" choice the codebase already makes elsewhere (deterministic query
processing over LLM rewriting by default, keyword overlap over embeddings for memory
relevance) — a signed/stateless token scheme would be more complex for no benefit at
this scale.

### Password hashing: Argon2id via `argon2-cffi`

OWASP's current recommended default for new applications. `apex_ai/auth/passwords.py`
is intentionally the entire cryptographic surface of this phase — one call to hash,
one to verify, no custom key derivation, no reversible encoding. `verify_password`
never raises, even on a malformed/foreign hash string; a bad hash is just "not a
match," not an application error worth crashing a login attempt over.

### Auto-login-local, precisely

`Settings.auto_login_local` (default `True`) is read on every request via a FastAPI
dependency (`require_user` in `apex_ai/api/auth.py`), in this order:

1. A valid session cookie present → that real account, always. An explicit login
   never gets silently overridden by the local fallback.
2. No cookie, `auto_login_local=True` → the auto-provisioned default local account
   (`ApexServices.default_local_user`, bootstrapped once at startup, idempotent
   across restarts — verified directly, not assumed).
3. No cookie, `auto_login_local=False` → `401`. This is the real multi-tenant
   posture a shared/hosted deployment should use.

The default account's own password is a random 24-byte token nobody is ever shown —
there is no default/known credential to guess, even though the normal flow never
needs it at all.

### Constant-shape login failures

`UserStore.verify_credentials` runs a real Argon2 verification against a
precomputed dummy hash when the email doesn't exist, instead of returning
immediately — so a login response doesn't let a timing difference reveal whether an
email is registered. `AuthService.login` raises one `InvalidCredentialsError`
regardless of which part was wrong ("Incorrect email or password.", never "no such
account" vs. "wrong password").

## Files

- `apex_ai/auth/passwords.py`, `users.py`, `sessions.py`, `service.py` — the store
  and service layer (no FastAPI, no HTTP — testable and tested in complete
  isolation).
- `apex_ai/api/auth.py` — `/auth/signup`, `/auth/login`, `/auth/logout`, `/auth/me`,
  plus `get_current_user`/`make_require_user_dependency` for later routers (Phase 54)
  to reuse — this is the first place in the codebase using FastAPI's `Depends()`
  injection rather than the closure-over-`services` pattern every other router uses;
  a `pyproject.toml` Ruff exception (`extend-immutable-calls`) was added once for it
  rather than a `# noqa` on every future `Depends(require_user)` call.
- `apex_ai/runtime.py` — `ApexServices.auth`/`default_local_user`; account/session
  construction is now its own guarded startup boundary, built *before* the RAG stack
  (accounts are foundational, not an optional add-on the way long-term memory is).
- `apex_ai/web/templates/login.html`, `apex_ai/web/static/login.js` — a standalone
  sign-in/create-account page (has to work before any authenticated app state
  exists, so it isn't wired into the SPA's view system). Reuses `app.css`'s tokens
  and existing `.primary-button`/`.quiet-button`/`.welcome-mark` classes rather than
  inventing new visual language for one page.
- Settings gained an "Account" section (current user + Sign out), reusing the exact
  `.backend-card`/`.backend-item` rendering `loadConfig()` already established.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 290 passed, 3 skipped |
| `tests/test_auth.py` (passwords, UserStore, SessionStore, AuthService, `build_services()` wiring, `/auth/*` routes including cookie-precedence-over-auto-login and server-side logout invalidation) | 32 passed |
| `node --check` on `login.js` | Passes |
| `ruff check` on every new/touched file | All checks passed (the pre-existing 42-finding baseline in untouched files is unchanged) |

## Deliberately not done in this phase

- **No enforcement on existing routes.** `/conversations`, `/memory`, `/documents`,
  `/chat/stream` are unchanged — anyone can still reach them exactly as before. This
  is Phase 54.
- **No per-user data isolation.** All conversations/memory/documents remain global.
  Phase 55.
- **No rate limiting on login/signup.** A real deployment needs brute-force
  protection on `/auth/login`; that belongs with Phase 58 (API Security), which
  covers rate limiting generally rather than one endpoint at a time.
- **No password reset / email verification flow.** Not asked for by Phase 51/52's
  wording ("secure sign-in/sign-up and session handling"); a real product would need
  it before real customers (Section 9), but it's out of scope here.
- **No account deletion/update endpoints.** Same reasoning as Phase 46's decision not
  to build a memory-edit UI: not asked for, easy to add later against the existing
  `UserStore`, not worth speculative scope now.

## Boundaries and remaining unknowns

- README and this doc are explicit that the app is **not yet actually multi-tenant**
  despite having real accounts — every account can currently see every conversation,
  memory, and document. Shipping accounts without saying this loudly would be
  exactly the kind of misleading-by-omission the roadmap's honesty rules exist to
  prevent.
- `secure=request.url.scheme == "https"` on the session cookie means the cookie is
  not marked `Secure` when served over plain HTTP (the loopback default). Correct
  for local HTTP use; a deployment behind TLS gets the flag automatically since the
  scheme is read from the actual request, not hardcoded — but this hasn't been
  verified against a real reverse-proxy setup that terminates TLS in front of Apex AI
  (where `request.url.scheme` could report `http` even though the browser used
  `https`, depending on proxy header handling — a real deployment would need
  `X-Forwarded-Proto` support, which is not implemented).
- No CSRF token — the session cookie is `SameSite=Lax`, which blocks cross-site
  `POST` submissions from following the cookie in the most common attack shape, but
  this is not a full CSRF defense. Acceptable for now given nothing is enforced yet
  (Phase 54); worth revisiting once real mutations are gated behind real accounts.
