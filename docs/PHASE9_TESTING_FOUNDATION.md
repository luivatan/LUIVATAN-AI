# Apex AI Phase 9 — Testing Foundation

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `4d8fcfa` (Phase 8 health checks)
- **Scope:** the roadmap asks for "unit and integration test structure" and "a
  repeatable test command." Both already existed in substance; this phase makes them
  explicit, accurate, and automated.

## Audit findings

- 234 tests already existed across 24 files, with a shared `tests/conftest.py`
  providing deterministic, offline fixtures (`HashingEmbeddingProvider`, `FakeLLM`) —
  a real unit/integration structure, not a stub.
- The repeatable command (`python -m pytest tests/ -q`) was documented in the README
  and worked, but:
  - **there was no pytest configuration at all** — no `pyproject.toml`, `pytest.ini`,
    or `setup.cfg` — so `pytest` (no path argument) silently discovered nothing
    useful outside `tests/`, and there was nowhere to register markers;
  - `conftest.py`'s own docstring claimed "a separate opt-in marker (`integration`)
    covers the real models," but no such marker existed anywhere and no
    `@pytest.mark.integration` test did either — the doc had drifted from the code;
  - `ruff` was used to verify every prior phase's changes (Phases 4–8 all reference
    it in their verification tables) but was never declared in
    `requirements-dev.txt` — a new contributor running the documented dev-install
    command would not have it;
  - **there was no CI.** "Repeatable" so far meant "documented," not "actually run
    automatically" — nothing enforced that the suite stayed green on push/PR; and
  - the README's test count ("143 tests") was stale — the real count is 234 after
    Phases 41–45 and 7–8.

## Change

- **`pyproject.toml`** (new, tool-config only — no `[build-system]`/`[project]`
  table; the app is still run directly, not installed as a package): sets
  `testpaths = ["tests"]` so bare `pytest` behaves like the documented command, and
  registers the `integration` marker `conftest.py` already referenced.
- **`tests/conftest.py`** docstring corrected to say the marker is registered for
  future real-model tests and that none exist yet, instead of implying coverage that
  isn't there.
- **`requirements-dev.txt`**: added `ruff>=0.9,<1`, and
  `tests/test_dependencies.py::DEVELOPMENT_DEPENDENCIES` (the Phase 4 manifest-audit
  test) updated to include it — the audit test is designed to fail loudly on an
  undeclared dependency, and it did, immediately, which is exactly its job.
- **`.github/workflows/tests.yml`** (new): on every push and PR, installs
  `requirements.txt` + `requirements-dev.txt` on Python 3.11 (matching the README's
  "tested on 3.11"), then runs:
  1. `ruff check --select E9,F63,F7,F82 .` — syntax errors and undefined names,
     **blocking**. This passes cleanly today across the whole repo.
  2. `ruff check .` — the full style/lint pass, **non-blocking**
     (`continue-on-error: true`). The full-repo pass currently reports 75
     pre-existing findings (mostly `BLE001`, `S110`, `SIM102`, `RUF022`, `UP035`,
     `F401`, `C408` — the same categories Phase 6 already documented as accepted
     pre-existing debt). Blocking CI on those now would fail every PR over unrelated
     history rather than the change being reviewed; splitting the gate keeps *new*
     syntax/undefined-name mistakes hard-blocked while surfacing the rest without
     silently hiding it — the report step still runs and is visible in every job.
  3. `python -m pytest tests/ -q`.
- **README** test count corrected to 234, with a pointer to the new CI workflow.

## Deliberately not changed

- No coverage tooling (`pytest-cov` etc.) — the roadmap phase asks for structure and
  a repeatable command, not a coverage target; adding one now would be scope beyond
  what was asked.
- No fix for the 75 pre-existing full-repo Ruff findings — cleaning those up is
  unrelated to "testing foundation" and would be a large, separate diff across
  files this phase didn't otherwise touch. The CI report step now makes them visible
  going forward instead of only discoverable by someone manually running Ruff.
- No test matrix across multiple Python versions — the project documents "Python
  3.10+ (tested on 3.11)"; CI matches that claim on 3.11 rather than silently
  expanding the tested surface.
- `llama-cpp-python` and `gradio` are still installed in CI exactly as the README's
  own `pip install -r requirements.txt` instructs, even though the test suite itself
  needs neither (confirmed directly: the full suite passes with `pytest-importorskip`
  covering the Gradio-dependent tests, and no test imports `llama_cpp`). Installing
  the same manifest a real contributor installs, rather than a hand-trimmed CI-only
  subset, keeps CI honest about what "install and test this repo" actually involves.

## Verification

| Check | Result |
|---|---|
| `python -m pytest tests/ -q` and `python -m pytest -q` (no path, via `testpaths`) | Both: 231 passed, 3 skipped |
| `tests/test_dependencies.py` (manifest audit) | Passes with `ruff` declared |
| `ruff check --select E9,F63,F7,F82 .` | All checks passed (this is CI's blocking step) |
| `.github/workflows/tests.yml` YAML parses | Verified with `yaml.safe_load` |

## Boundaries and remaining unknowns

- CI has not yet actually run on GitHub's runners (only validated locally in this
  sandbox, which lacks `llama-cpp-python`/`gradio` build verification). The first
  real push will be the first true end-to-end confirmation that the full documented
  install works unattended on `ubuntu-latest`.
- `llama-cpp-python`'s C++ compile step is the main risk to CI reliability/speed;
  `timeout-minutes: 30` bounds a hang but does not prevent one. If this proves
  flaky in practice, the next step would be pinning a platform wheel or trimming
  the CI install, not silently disabling the check.
- No branch-protection rule requires this workflow to pass before merge — that is a
  repository-settings change outside this session's write access, not a code change;
  the user should enable "Require status checks to pass" for this workflow if they
  want it enforced rather than advisory.
