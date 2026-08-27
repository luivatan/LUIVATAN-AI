# Apex AI production checklist (phases 91–100)

## Admin and security
- Protect admin routes with server-side role checks and audit all account, billing, and document actions.
- Use `apex_security.security_headers()` at the HTTP boundary.
- Use `secure_upload()` for random, traversal-safe filenames; validate extension, size, MIME/content, and malware-scan files before indexing.
- Store SQLite/Chroma/uploads outside the repository with least-privilege filesystem permissions and encrypted backups.
- Keep API keys in a secret manager; use HTTPS, Secure/HttpOnly/SameSite cookies, CSRF protection, rate limits, and generic auth errors.

## Performance and mobile
- Keep embedding/model initialization lazy, batch embeddings, cap retrieval/context sizes, and process documents through the queue.
- Run workers separately from the web process for production workloads.
- The website uses responsive layouts, scalable typography, touch-sized controls, and a mobile navigation fallback.

## Testing and launch
- Unit-test auth, billing, document validation, chunking, retrieval, answer grounding, and security helpers.
- Add browser end-to-end coverage for registration → verification → upload → ask → citation → billing gate, using temporary databases and fake model providers.
- Deploy behind a TLS reverse proxy with health checks, structured redacted logs, backups, migrations, monitoring, and rollback artifacts.
- Before launch: set explicit environment values, disable debug, verify webhook secrets, test restore procedures, run dependency/license scanning, review medical safety copy, and perform a staged smoke test.
