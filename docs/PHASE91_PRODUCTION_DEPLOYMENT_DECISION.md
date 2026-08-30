# Apex AI Phase 91 — Production Deployment (Decision)

- **Decided:** 2026-08-30 (America/Chicago)
- **Baseline:** `f7075da` (Phase 92, database backups)
- **Roadmap scope:** "Deploy frontend and backend using a reproducible
  production configuration."
- **Decision:** declined for real execution this pass — documented here
  instead, following the pattern used for Phases 75, 85, 86, 89, and 90.

## Why this can't be done for real here

"Deploy" is not a code problem this environment can solve by writing more
code. It requires things that only exist outside this sandbox and that only
the user can decide or provide:

- A real hosting target (a cloud account, a VPS, a PaaS) to deploy *to*.
- A real domain name and TLS certificate for the public-facing frontend
  and API.
- Real deployment credentials (SSH keys, cloud API tokens, container
  registry access) — none of which exist in this session, and none of
  which should ever be fabricated or stubbed.
- A decision about *where* — which cloud/provider, which region, what
  budget — that is a business decision, not an engineering one.

Faking any part of this (a Dockerfile nobody has run against a real host,
a "deployed" URL that doesn't resolve, invented environment secrets) would
violate the roadmap's own ground rules against faking model choices,
billing states, or infrastructure that isn't real. So this phase produces
a decision doc instead of a deployment.

## What already exists that a real deployment would build on

Nothing about deployment was blocked on this decision — the application
was already built to be deployment-ready:

- **`apex_ai/api/server.py`** exposes `create_app()` (a standard FastAPI
  `app` factory) and a `main()` entrypoint that calls `apex_ai.web.app.launch()`.
  Any real deployment runs this the same way local development does —
  under a production ASGI server (e.g. `uvicorn apex_ai.api.server:create_app
  --factory`) behind a reverse proxy, rather than through `main()`'s dev
  launcher.
- **Configuration is entirely environment-variable driven**
  (`apex_ai/config/settings.py`'s `load_settings()`, backed by
  `.env.example`) — there are no hardcoded paths or secrets to change for
  a production host. Every store path, model choice, and provider
  credential is already an `APEX_*` environment variable.
- **`requirements.txt`** / **`requirements-dev.txt`** / **`requirements-gpu.txt`**
  already separate runtime, development, and optional GPU dependencies,
  which is exactly the split a container build needs.
- **Phase 92 (database backups)** exists specifically because a real
  deployment needs a real backup story before it needs a fancy
  deployment story — that groundwork is done and independent of *where*
  the app eventually runs.
- **Phase 90's testing work and the full pytest suite** (539 passed, 3
  skipped as of Phase 92) already demonstrate this application runs
  correctly in a clean environment, which is the actual prerequisite for
  any deployment pipeline (CI would run the same suite before promoting
  a build).

## What would make this real

If given real infrastructure and credentials, the concrete next steps
would be, in order:

1. A `Dockerfile` (multi-stage: install `requirements.txt`, copy
   `apex_ai/`, run under a non-root user, expose the ASGI port) and,
   if the target needs it, a `docker-compose.yml` wiring in a persistent
   volume for `data/`.
2. A real reverse proxy / TLS termination config (e.g. Caddy or nginx)
   in front of the ASGI server, using a real domain's certificate.
3. Wiring `scripts/backup.py` (Phase 92) to a real scheduler (cron or a
   systemd timer) on that host, with `--output-dir` pointed at a volume
   that survives container replacement — and, per Phase 92's own
   "deliberately not done" list, an off-host upload step (S3 or
   equivalent) using real destination credentials.
4. A real health check wired into the hosting platform against the
   existing `/health` endpoint (see Phase 93's decision doc).
5. A rollback plan (keep the previous image/build available, or use the
   platform's built-in rollback) — reversibility for the deployment
   process itself, mirroring the non-destructive-by-default discipline
   used throughout this codebase's data operations.

None of this can be authored usefully in the abstract — a Dockerfile
that has never been built against a real target, or a proxy config that
has never terminated real TLS, is exactly the kind of "looks done but
isn't" artifact the roadmap warns against faking. It should be written
once the user has chosen a real hosting target, so it can be verified by
actually deploying to it rather than left as an untested guess.

## Deliberately not done in this phase

- No `Dockerfile`, `docker-compose.yml`, or CI/CD deployment pipeline —
  all of these would be untestable guesses without a real target to
  build and run them against.
- No claimed "production URL" or deployment status anywhere in the repo
  or docs.
- No invented hosting credentials, secrets, or environment values beyond
  what `.env.example` already documents as placeholders.
