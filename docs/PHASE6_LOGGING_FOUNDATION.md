# Apex AI Phase 6 — Logging Foundation

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `6652981` (Phase 5 error-handling foundation)
- **Scope:** application-owned console/file logging, structured events, credential
  redaction, private-content minimization, safe exception diagnostics, and deterministic
  verification

## Outcome

Phase 6 strengthens the existing standard-library logging subsystem rather than adding a
new logging dependency or changing the RAG/LLM backends. Apex now writes:

- concise, redacted, human-readable console records in UTC; and
- rotating newline-delimited JSON records to the existing `logs/apex.log` location.

The JSON record schema is versioned with `schema_version: 1`. Every record contains:

| Field | Meaning |
|---|---|
| `schema_version` | Logging schema version, currently `1`. |
| `timestamp` | UTC ISO-8601 timestamp with millisecond precision. |
| `level` | Standard Python logging level name. |
| `logger` | Namespaced application logger such as `apex.runtime`. |
| `event` | Stable event name, or a logger-derived fallback. |
| `message` | Human-readable operational summary after redaction. |
| `source` | Calling module, function, and line. |
| `context` | Optional recursively sanitized structured fields. |
| `exception` | Optional exception type, message-omission marker, and stack frames. |

The existing rotation policy remains three backups at 2,000,000 bytes per active file.
There is no new network sink, telemetry service, or internet requirement.

## Findings before Phase 6

The pre-Phase-6 logger already had useful namespacing, console/file separation, rotation,
third-party noise controls, and a timing context manager. Those pieces were retained. The
audit found important gaps:

- the module described logs as structured while both outputs were formatted prose;
- no central filter redacted passwords, API keys, authorization values, tokens, private
  keys, or credential-bearing URLs;
- default traceback formatting retained arbitrary exception messages;
- structured contextual fields did not exist;
- query-processing DEBUG/WARNING records included full retrieval variants and protected
  names/identifiers;
- several document lifecycle records included filenames even though content hashes and
  counts were sufficient for diagnosis;
- several fallback paths interpolated raw third-party exception messages; and
- switching an existing `apex.log` directly to JSON would have mixed old prose and new JSON
  in one active file.

No evidence required replacing Python logging, adding a hosted collector, or changing
provider/retrieval behavior.

## Logging foundation

### Human console and structured file

`setup_logging()` still configures the `apex` logger tree idempotently. Managed handlers are
now identifiable, can be safely reset by tests/development reloaders, and are not duplicated
when setup runs repeatedly.

The console keeps an operational text format. The rotating file handler uses one compact
JSON object per line, allowing normal JSON tooling to filter on `event`, `level`, logger,
context fields, or exception type without parsing prose.

`log_event()` adds a small standard-library helper for stable event names and structured
context. It is used for runtime readiness/failure, memory-store readiness, model selection,
API boundary failures, retrieval completion, and grounded-answer outcomes.

`timed()` remains source compatible as a context manager, but now emits:

- `operation.completed` with measured `duration_ms` and `status: ok`; or
- `operation.failed` with measured `duration_ms` and `status: failed`, then re-raises the
  original failure.

No benchmark or latency claim is inferred from those per-operation measurements.

### Credential and private-data controls

Both managed handlers apply the same filter before emission. Text redaction covers common
forms of:

- named password, secret, API-key, token, cookie, credential, and authorization values;
- Bearer and Basic authorization material;
- credentials embedded in URL authority components;
- OpenAI-like, GitHub-like, AWS access-key-like, Slack-like, and JWT-shaped tokens; and
- PEM private-key blocks.

Nested mappings and sequences are sanitized recursively. Structured keys that identify
credentials become `[REDACTED]`. Structured keys commonly used for questions, prompts,
answers, messages, document/chunk text, filenames, paths, request/response bodies, and
similar private content become `[PRIVATE]` regardless of their value.

The compatibility `preview()` helper no longer returns a truncated excerpt. It returns only
a content-free character-count summary. Truncation is not anonymization, so keeping the old
behavior would conflict with this phase's privacy goal.

These controls are defense in depth. Application code must still avoid placing user or
document content in free-form log messages.

### Safe exception diagnostics

Managed exception records preserve:

- fully qualified exception type;
- source filename, line, and function for each traceback frame; and
- bounded cause/context-chain structure.

Exception messages and source-code lines are intentionally omitted. This retains stack
shape for diagnosis without copying arbitrary provider responses, submitted values,
document text, or secrets from `str(exception)` into the log.

Call sites that previously interpolated exception values now log an exception type or use
the managed safe exception boundary. Expected startup errors record category/type and stack
shape instead of writing detailed user-message text into logs.

### Private-content minimization at call sites

Phase 6 removes unnecessary values before they reach the formatter:

- retrieval-query variants are replaced by variant/strategy counts;
- dropped protected terms are replaced by counts;
- document extraction/chunk/duplicate events use bounded document IDs instead of names;
- ingestion timing labels no longer include filenames;
- evaluation ingestion records no longer include source filenames;
- local-model timing does not include a path;
- document IDs written by vector-store lifecycle messages are bounded to 12 characters; and
- fallback logs no longer interpolate raw exception strings.

The RAG result, citation, persistence, provider, and successful API payloads are unchanged.
Developer-only RAG trace objects remain separate from application logs.

### Failure and migration behavior

File-handler initialization is isolated from the console handler. If the directory or file
cannot be opened, Apex continues with console logging and emits a non-diagnostic warning.
A logging-path problem therefore does not disable local chat/RAG startup.

If the active `apex.log` begins with the former prose format, startup moves it to the first
available `apex.log.legacy*` name before creating the JSON file. Historical diagnostics are
preserved without producing a mixed-format active stream. Existing legacy files are not
rewritten or claimed to be sanitized.

## Compatibility decisions

- `get_logger()`, `setup_logging()`, and `timed()` retain their established call patterns.
- `setup_logging()` adds only an optional keyword-only `force` argument for deterministic
  reload/test isolation.
- The configured directory and active filename remain `APEX_LOG_DIR/apex.log`.
- Console logging remains human-readable at INFO by default; file logging remains DEBUG.
- The active file format deliberately changes from prose to JSON Lines.
- A pre-existing prose file is archived, never silently deleted.
- The privacy-preserving `preview()` behavior is an intentional tightening; repository code
  did not depend on the former content-bearing output.
- No provider, model, retrieval, ingestion, citation, storage, route, or UI contract changes
  are part of Phase 6.

## Security properties tested

Deterministic tests place unique canaries into:

- environment-style and JSON-style named credentials;
- quoted passwords containing spaces;
- Bearer authorization, credential-bearing URLs, private-key blocks, JWT-shaped values,
  and several provider-shaped token forms;
- nested secret fields and private question fields;
- free-form operational messages;
- exception messages containing private medical-style text and credentials;
- a real runtime-initialization failure routed through managed logging; and
- query text/protected identifiers passed through query processing.

Tests verify that canaries/private text do not reach JSON records or safe exception output,
while schema fields, event names, safe counts, exception types, and stack frames remain.
They also verify idempotent setup, measured timing fields, plain-log migration, and
console-only degradation when file initialization fails.

## Verification

| Check | Result |
|---|---|
| Dedicated Phase 6 logging suite | 11 passed. |
| Full current working tree, including the separately preserved Phase 46 overlay | 236 passed, 2 intentional legacy-environment warnings in 12.56 seconds. |
| Isolated tree built from committed Phase 5 plus only staged Phase 6 files | 230 passed, 2 intentional legacy-environment warnings in 12.07 seconds. |
| Ruff on the logging foundation, tests, and clean touched modules | Passed. |
| Ruff syntax/undefined-name gate across every Phase 6 Python path | Passed. |
| Broader Ruff audit on legacy-touched modules | 23 pre-existing findings before and after Phase 6; no net-new findings. |
| Python compilation and `git diff --check` | Passed. |
| Phase 46 isolation check | No Phase 46-only path is staged. |

The two pytest warnings intentionally exercise deprecated environment-variable aliases.
The broader Ruff findings are existing `BLE001`, `S110`, `SIM102`, `RUF022`, `UP035`,
`F401`, and `C408` items outside this phase's logging changes; Phase 6 does not conceal or
silently rewrite them.

## Boundaries and remaining unknowns

- Pattern-based redaction cannot prove detection of every proprietary, encrypted, split, or
  newly invented credential format. Secret values must not be deliberately logged.
- Historical `apex.log.legacy*` files predate this policy and may contain sensitive values.
  Operators must protect or delete them according to their own retention requirements.
- Third-party processes and loggers with independently configured handlers are not converted
  into Apex JSON records. Their output policy remains controlled by those components.
- File permissions still depend on the operating system, parent-directory permissions, and
  process umask. Deployment-specific access control is **UNKNOWN** until validated on the
  target host.
- Concurrent multi-process writes/rotation, remote aggregation, retention policy,
  tamper-evident storage, alerting, metrics, and distributed tracing are not implemented or
  claimed by this phase.
- Logs remain local operator data and are not suitable for normal-user display.
- Abrupt process termination can leave a final partial JSON line; crash consistency across
  power loss and filesystem failure is **UNKNOWN**.

## Phase 6 conclusion

Apex now has a dependency-free structured logging foundation with useful event/context
fields, measured operation timing, safe stack shape, centralized credential redaction, and
explicit private-content minimization. It preserves offline-first operation and existing
application behavior while establishing a safer base for later API, health, deployment, and
observability phases.
