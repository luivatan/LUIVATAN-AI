# Apex AI Phase 59 — Secret Management

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `9179997` (Phase 58 API security)
- **Scope:** "Move production secrets into secure deployment/provider secret
  storage." Audited the whole codebase for what actually counts as a secret,
  how each one is handled today, and whether anything leaks it — then added
  the deployment guidance the roadmap's wording asks for. No code gap
  requiring a fix was found; this phase is primarily verification plus
  documentation, and says so rather than inventing work to look busy.

## What counts as a secret in Apex AI today

A deliberate, explicit inventory rather than an assumption:

- **`APEX_OPENAI_API_KEY`** — the one real runtime secret. Only relevant
  when `APEX_LLM_PROVIDER=openai` or `openai_compatible`.
- **Session tokens** (`secrets.token_urlsafe(32)`, Phase 52) are
  per-session, server-generated, and stored hashed nowhere — they're opaque
  bearer values in `data/users.db`, functionally secrets but not
  *configuration* secrets: there's nothing to inject from a deployment's
  secret store, since the app generates and revokes them itself.
- **Password hashes** (Argon2id, Phase 53) are one-way; not secrets that
  need injecting, and already never logged (`UserStore` never logs
  passwords or hashes — verified by re-reading every `log.*` call in
  `apex_ai/auth/`).
- **No signing/encryption key exists.** Sessions are opaque lookups, not
  JWT/HMAC-signed tokens (a deliberate Phase 52 design choice — see
  `docs/PHASE51-53_ACCOUNTS_AUTHENTICATION.md`), so there is no secondary
  secret to manage the way a JWT deployment would need to manage a signing
  key. Local llama.cpp and the default local Ollama URL need no credential
  at all.

That's the complete list — one configuration secret.

## Verified handling of `APEX_OPENAI_API_KEY`

- **Never committed.** `.gitignore` excludes `.env`/`.env.*` while
  explicitly allowing `.env.example`; `.env.example`'s
  `APEX_OPENAI_API_KEY=` line documents the variable with an empty value,
  confirmed by the existing `test_env_example_documents_phase3_settings_without_a_key_value`.
- **Never appears in `Settings`'s own `repr()`.** The dataclass field is
  declared `field(repr=False, metadata={"secret": True})` — excluded from
  the auto-generated `__repr__` by a real dataclasses feature, not a
  decorative comment. `test_api_key_is_redacted_from_settings_repr` proves
  it: constructing `Settings(openai_api_key=secret)` and taking `repr()`
  contains neither the value nor even the field name.
- **Never fully serialized elsewhere.** Searched for any `asdict(settings)`,
  `vars(settings)`, or `settings.__dict__` access anywhere in the codebase —
  none exist. This matters because `repr=False` only changes `__repr__`;
  `dataclasses.asdict()` would still walk every field regardless. Every
  route that reports configuration status (`/app-config`, `/health`) builds
  its own explicit, named dict of non-secret fields instead of dumping the
  settings object, so there's no code path that *could* leak it even by a
  future careless edit reusing `asdict`.
- **Never embedded in a provider error message shown to users.** Checked
  `apex_ai/llm/openai_compat.py`'s exception handling: `str(error)` on a
  `requests.RequestException` (connection failure or `raise_for_status()`)
  reports the URL and status/reason, not the request's headers — the
  `Authorization: Bearer <key>` header is never included in what becomes a
  `ProviderError`'s `why=`, which is what reaches both logs and the user.

## What this phase adds

A "Secret management" section in the README explains the one secret's full
lifecycle and gives concrete integration points for common secret managers
(Docker secrets, Kubernetes `Secret`, systemd `EnvironmentFile=`, and
cloud-provider secret stores) — all of which work by injecting environment
variables into the process, which is exactly the mechanism
`apex_ai/config/settings.py` already reads from. No code changes were
needed for this to work in production; the gap was documentation, not
capability.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 306 passed, 3 skipped (no code change) |
| `tests/test_config.py::test_api_key_is_redacted_from_settings_repr` | passing (pre-existing, re-confirmed) |
| `tests/test_config.py::test_env_example_documents_phase3_settings_without_a_key_value` | passing (pre-existing, re-confirmed) |
| Searched for `asdict(settings)` / `vars(settings)` / `settings.__dict__` anywhere in `apex_ai/` | none found |
| Read every `log.*` call in `apex_ai/auth/` for a password/hash/key leak | none found |
| Read `apex_ai/llm/openai_compat.py`'s exception handling for a header leak into `ProviderError` | none found |

## Deliberately not done in this phase

- **No secrets-manager SDK integration** (e.g., calling AWS Secrets Manager
  directly from Python at startup). Reading from the environment is the
  standard, tool-agnostic integration point every secret manager already
  supports; adding a specific SDK would create a dependency on one
  provider's API for something environment variables already solve, and
  nothing in the roadmap's wording ("move secrets into secure
  deployment/provider secret storage") asks the *application* to talk to
  the secret store directly — that's the deployment tooling's job.
- **No secret rotation tooling.** `APEX_OPENAI_API_KEY` is read once at
  startup; rotating it means restarting the process with a new value,
  which is standard for an env-var-configured service and not something
  this phase's scope calls for automating.
