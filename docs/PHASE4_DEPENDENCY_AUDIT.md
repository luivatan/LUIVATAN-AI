# Apex AI Phase 4 — Dependency Audit

- **Audit date:** 2026-08-29 (America/Chicago)
- **Scope:** active Apex AI runtime, supported entry points, test/development tooling,
  compatibility ranges, resolver health, and known package advisories
- **Python used for verification:** CPython 3.11.2 on Linux x86_64

## Executive result

Apex AI had a compact requirements file, but it mixed runtime and development concerns in
several places and left some direct imports dependent on transitive installation details.
The audit made measured corrections without replacing ChromaDB, removing an LLM backend,
or changing an application entry point:

- all third-party imports in `apex_ai/` now have an explicit owning distribution in
  `requirements.txt`;
- every direct runtime/development requirement has an upper compatibility boundary;
- redundant runtime `numpy` was moved to development, where it is imported directly;
- deprecated development use of plain `httpx` was replaced with `httpx2`, which current
  Starlette `TestClient` prefers;
- previously undeclared `gguf` and NumPy requirements for `scripts/make_tiny_gguf.py` were
  added to the development manifest;
- the tiny-GGUF generator was corrected for the current `gguf` API so it no longer writes
  the architecture key twice;
- GPU installation guidance now distinguishes portable requirements from host-specific
  CUDA/build choices; and
- manifest/import ownership and the GGUF development tool now have focused regression
  tests.

A fresh resolver run succeeds, the complete requirements install succeeds, `pip check`
reports no broken requirements, and the full offline application suite passes. A security
audit still reports five advisories with no published fixed versions: four in ChromaDB and
one in DiskCache (a llama-cpp-python dependency). They are documented below rather than
hidden or “fixed” by an unsupported downgrade.

## Audit method

The audit used repository and environment evidence rather than package popularity:

1. parsed imports from every Python module under `apex_ai/`;
2. inspected dynamic/optional imports and runtime-discovered upload support;
3. compared those imports with all root and nested requirement files;
4. inspected installed distribution metadata and dependency edges;
5. queried current PyPI release metadata and resolver-compatible versions;
6. ran a fresh `pip --dry-run --ignore-installed` resolution;
7. installed both root manifests, including a native CPU llama-cpp-python build;
8. ran `pip check`, `pip-audit`, provider/tool smoke checks, and the offline test suite; and
9. inspected advisory reachability against the code paths Apex actually invokes.

The dependency lists are compatible ranges, not a generated `pip freeze` and not a
cross-platform lock. Exact transitive results vary by Python, operating system, CPU/GPU,
and package index.

## Classification summary

The classes overlap where the same distribution supports more than one feature:

| Class | Distributions |
|---|---|
| Core active runtime | `python-dotenv`, `pypdf`, `sentence-transformers`, `torch`, `chromadb`, `rank-bm25`, `llama-cpp-python`, `fastapi`, `uvicorn`, `pydantic`, `python-multipart` |
| Optional provider support | `requests` for Ollama/OpenAI-compatible providers; `transformers` plus `torch` for the local Transformers provider |
| Preserved compatibility entry point | `gradio` for `legacy_ui.py` |
| Transitive before this audit but directly imported by Apex | `huggingface-hub`, `torch`, `transformers`, `starlette` |
| Development only | `pytest`, `packaging`, `httpx2`, `fpdf2`, `gguf`, `numpy` |
| GPU-specific | No unconditional package is portable; `requirements-gpu.txt` documents host-specific llama-cpp-python and Torch selection. |
| Archived prototype only | `langchain-text-splitters`, `pdf2image`, `pytesseract`, `datasets`, `peft`, and `trl` remain excluded from active requirements. |

## Runtime dependency ownership

The current Python 3.11 environment resolved the following direct packages. “Verified”
means imports and relevant automated code paths passed in this environment; it does not
claim that every version inside a range has been tested.

| Distribution | Reviewed range | Version tested | Why Apex owns it |
|---|---|---:|---|
| `pypdf` | `>=4.0,<7` | 6.16.2 | Extracts page-aware text from PDFs. |
| `sentence-transformers` | `>=3.0,<6` | 5.7.0 | Default semantic embeddings and optional cross-encoder reranking. |
| `huggingface-hub` | `>=0.23,<2` | 1.29.0 | Applies and restores explicit model-cache/offline policy. |
| `chromadb` | `>=1.0,<2` | 1.5.9 | Embedded persistent vector storage and similarity search. |
| `rank-bm25` | `>=0.2.2,<0.3` | 0.2.2 | Keyword retrieval channel and lexical scoring. |
| `llama-cpp-python` | `>=0.3,<0.4` | 0.3.35 | Default local GGUF provider; native CPU build was verified. |
| `requests` | `>=2.31,<3` | 2.34.2 | Ollama and OpenAI-compatible HTTP providers. |
| `transformers` | `>=4.41,<6` | 5.16.1 | Optional local Transformers generation provider. |
| `torch` | `>=1.11,<3` | 2.13.0 | Execution runtime for embedding, reranker, and Transformers models. |
| `gradio` | `>=5.0,<7` | 6.26.0 | Preserved `legacy_ui.py` compatibility entry point. |
| `fastapi` | `>=0.133,<1` | 0.141.1 | Primary web application, API, uploads, and NDJSON streaming. |
| `starlette` | `>=1.2,<2` | 1.6.0 | Direct response primitives and the httpx2-capable test client. |
| `uvicorn` | `>=0.29,<1` | 0.52.4 | ASGI server used by web/API launch functions. |
| `pydantic` | `>=2.6,<3` | 2.13.5 | API request and response validation. |
| `python-multipart` | `>=0.0.18,<1` | 0.0.32 | Runtime-discovered parser required by browser upload routes. |
| `python-dotenv` | `>=1.0,<2` | 1.2.3 | Loads the untracked `.env` configuration file. |

### Why these were retained

- **ChromaDB stays.** It is the implemented persistence boundary, is covered by vector
  store and RAG tests, and no measured migration benefit justifies replacing it in a
  dependency-audit phase.
- **llama-cpp-python stays.** It is the default offline generation provider and its
  import/build/model-load path works. Removing it from the normal install would make the
  documented default incomplete.
- **Gradio stays.** It is no longer the primary UI, but `legacy_ui.py` is a supported
  compatibility entry point and has explicit interface tests.
- **FastAPI, Uvicorn, and Pydantic remain direct.** They are application dependencies, not
  incidental Gradio dependencies.
- **python-multipart stays despite no ordinary `import` statement.** FastAPI checks for it
  while constructing multipart upload routes.
- **Torch, Transformers, Hugging Face Hub, and Starlette are explicit.** Apex imports each
  directly; relying only on another package to install them obscured ownership and let a
  transitive dependency change remove or major-upgrade an Apex code path.

## Development dependencies

| Distribution | Reviewed range | Version tested | Purpose |
|---|---|---:|---|
| `pytest` | `>=8.0,<10` | 9.1.1 | Offline unit/integration-style regression suite. |
| `packaging` | `>=24,<27` | 26.3 | Standards-compliant requirement parsing in manifest tests. |
| `httpx2` | `>=2.0,<3` | 2.12.0 | Current Starlette/FastAPI `TestClient` transport. |
| `fpdf2` | `>=2.7,<3` | 2.8.8 | Regenerates committed PDF fixtures. |
| `gguf` | `>=0.19,<0.20` | 0.19.0 | Generates the structural local-provider GGUF smoke artifact. |
| `numpy` | `>=1.26,<3` | 2.4.6 | Tensor generation for the tiny GGUF development tool. |

Plain `httpx` was removed from the development manifest because the tests do not import it
and current Starlette warns that its `TestClient` fallback is deprecated. Starlette 1.2 is
the first released line with httpx2 TestClient support, and FastAPI 0.133 is the first
FastAPI line that supports Starlette 1.x; those are now the reviewed lower bounds. `httpx`
still appears in resolved environments because ChromaDB, Gradio, and Hugging Face Hub
legitimately use it. Installing `httpx2` removes the Starlette deprecation without
pretending the original HTTPX package disappeared from the dependency graph.

References:

- <https://starlette.dev/testclient/>
- <https://starlette.dev/release-notes/>
- <https://fastapi.tiangolo.com/release-notes/>

NumPy is no longer a direct runtime declaration because active `apex_ai/` code does not
import it. It remains installed transitively for ChromaDB, rank-bm25, Torch, and
Sentence-Transformers, and is declared directly in development because the GGUF generator
imports it. This is an ownership correction, not a claim that the resolved environment no
longer contains NumPy.

## Compatibility and update decisions

### Reviewed upper bounds

The previous manifest bounded only pypdf, Sentence-Transformers, ChromaDB,
llama-cpp-python, and Gradio. Requests, FastAPI, Uvicorn, Pydantic, python-multipart,
python-dotenv, and all development tools could cross a future major release without
review. Every direct requirement now has an upper boundary, and a test prevents accidental
removal of those review gates.

These ranges are intentionally broader than exact pins so platform-specific resolvers can
select compatible wheels. They are not proof that every version combination works. A
lock/constraints strategy needs separate platform policy and remains **UNKNOWN**.

### Sentence-Transformers 6

PyPI reports 6.0.0 as the latest release, while Apex intentionally remains on the reviewed
5.x line. The upstream 5-to-6 migration changes minimum Transformers/Torch/Hub versions,
custom model-code trust behavior, and some scoring/output behavior. Apex's configured
embedding and reranker model behavior has not been validated against that migration, so
blindly widening to `<7` would be unsupported.

Required verification before that major upgrade:

1. cached offline loading for the configured embedding and reranker models;
2. embedding dimensions and normalization;
3. reranker score ordering and evidence gates;
4. index compatibility or an explicit rebuild decision; and
5. full retrieval evaluation, not merely a successful import.

Reference: <https://sbert.net/docs/migration_guide.html>

### NumPy release selection

PyPI's overall latest NumPy at audit time is 2.5.2 and requires Python 3.12+. The Python
3.11 resolver correctly selected 2.4.6 inside `>=1.26,<3`. It is therefore not treated as
an unresolved outdated package in this environment.

### Native and accelerator dependencies

A plain requirements install built llama-cpp-python 0.3.35 successfully with the available
compiler, and `llama_supports_gpu_offload()` correctly reported false for that CPU build.
A generated structural GGUF loaded through `LocalLLMProvider`; no fabricated answer was
generated or evaluated.

`requirements-gpu.txt` remains guidance rather than an active portable requirement because
GPU wheels are coupled to the host backend and toolkit. It now tells operators to choose a
currently supported CUDA index or upstream backend instructions and to avoid silently
reusing a cached CPU wheel.

Reference: <https://github.com/abetlen/llama-cpp-python#installation>

## Resolver size and transitive cost

On this Linux/Python 3.11 host, a fresh resolve selected 143 distributions. Two important
sources of transitive weight are outside Apex's direct control:

- current Torch wheels selected by Sentence-Transformers include substantial accelerator
  components even though Apex can run on CPU; and
- full embedded ChromaDB brings server/client infrastructure dependencies, including
  Kubernetes and telemetry packages, even though Apex uses only `PersistentClient`.

Installing packages with `--no-deps` or deleting selected transitive dependencies was
rejected because it creates an unsupported environment. Moving the default embedding
backend, replacing ChromaDB, or defining platform-specific CPU wheel indexes would be an
architectural/deployment change requiring separate compatibility testing. Their potential
installation-size reduction is **UNKNOWN** until measured in representative clean
platform environments.

## Security advisory review

`pip-audit 2.10.1 --local` reports five known advisories in two installed packages. The
command exits nonzero, and Phase 4 intentionally records that result rather than declaring
a clean security scan.

| Package | Advisory IDs | Scanner fix version | Relevant upstream behavior |
|---|---|---|---|
| ChromaDB 1.5.9 | PYSEC-2026-311 / CVE-2026-45829; CVE-2026-45830; CVE-2026-45831; CVE-2026-45833 | None published | Python server collection configuration, multi-tenant authorization, and server/client embedding-function configuration paths |
| DiskCache 5.6.3 | PYSEC-2026-2447 / CVE-2025-69872 | None published | Pickle deserialization if an attacker can write data later read from a disk cache |

### ChromaDB reachability and mitigation

The critical Chroma advisories primarily describe network server and attacker-controlled
collection/embedding-function configuration. Apex constructs exactly one
`chromadb.PersistentClient` against a local project path. It does not instantiate
`chromadb.HttpClient`, launch Chroma's Python API server, expose Chroma collection-creation
parameters through its own FastAPI routes, or ask Chroma to run an embedding function;
Apex supplies numeric embeddings itself.

That code inspection substantially reduces the described network attack surface, but it
is not an exploit proof. Exact reachability is **UNKNOWN** without an advisory-specific
security test. Until a fixed release exists:

- never expose Chroma's Python API server as part of an Apex deployment;
- keep the local database directory writable only by the Apex service account;
- treat externally supplied Chroma databases/collection configuration as untrusted; and
- monitor upstream advisories and upgrade to a compatible fixed 1.x release when one is
  published and tested.

Downgrading was rejected: several advisories cover older releases too, and an unsupported
old version is not a demonstrated security fix. Replacing ChromaDB was also rejected
because the current embedded path is functional and the roadmap explicitly requires it to
remain unless evidence makes replacement necessary.

References:

- <https://github.com/advisories/GHSA-f4j7-r4q5-qw2c>
- <https://github.com/advisories/GHSA-2wm9-hf6c-p5cr>
- <https://github.com/advisories/GHSA-xph7-9rjv-w5fr>
- <https://github.com/advisories/GHSA-36p7-vc44-83pf>

### DiskCache reachability and mitigation

DiskCache is required transitively by llama-cpp-python. Apex never imports DiskCache,
constructs `LlamaDiskCache`, or calls `Llama.set_cache`; llama-cpp-python leaves its cache
as `None` by default in the exercised provider path. The advisory also requires an
attacker-controlled writable cache followed by deserialization.

No fixed DiskCache version is published. Removing a mandatory llama-cpp-python dependency
with `--no-deps` would be unsupported. Current mitigation is to keep service directories
private, never enable a disk prompt cache over attacker-writable storage, and monitor for a
fixed dependency or upstream serialization change. Advisory-specific exploitability in a
future Apex configuration is **UNKNOWN** and must be reassessed if disk caching is added.

Reference: <https://github.com/advisories/GHSA-w8v5-vhqr-4h9v>

## Package-license metadata

Installed metadata reports BSD, MIT, Apache, and mixed permissive expressions/classifiers
across direct runtime distributions. The development-only `fpdf2` package reports
`LGPL-3.0-only`. This is an inventory observation, not legal advice or a commercial-use
approval. A complete transitive license review, notice-file review, distribution-method
analysis, and model-license review are **UNKNOWN** and require qualified legal review.
Model weights have licenses independent of these Python packages.

## Historical prototype boundary

`pu/medical-rag/` is a preserved pre-Apex prototype, not an active package or supported
entry point. Its four-line nested manifest lists Sentence-Transformers, llama-cpp-python,
ChromaDB, and pypdf, but is incomplete and partially stale relative to its own source:

| Prototype classification | Packages/evidence | Phase 4 decision |
|---|---|---|
| Shared with active Apex | `sentence-transformers`, `llama-cpp-python`, `chromadb`, and training-only use of `transformers` | Versions are owned by the active root manifest for active features, not to promise prototype compatibility. |
| Archived prototype-only imports | `langchain-text-splitters`, `pdf2image`, `pytesseract`, `datasets`, `peft`, and `trl` | Do not add to supported root requirements. OCR also needs untracked system tools. |
| Stale nested declaration | `pypdf` is listed but the prototype OCR script imports `pdf2image` instead. | Preserve as historical evidence; do not treat the nested file as installable support. |

The prototype scripts also retain obsolete behavior identified in the Phase 1/2 audits.
Those heavy experimental dependencies were not copied into the active root manifest, and
Phase 4 did not rewrite or delete the historical prototype. Running it remains
**UNKNOWN/unsupported** unless it is separately isolated, configured, and audited.

## Verification

All commands below completed on the audited Linux/Python 3.11 host:

| Check | Result |
|---|---|
| `pip install -r requirements.txt -r requirements-dev.txt` | Completed; llama-cpp-python 0.3.35 built from source. |
| Fresh `pip install --dry-run --ignore-installed` resolution | Passed; 143 distributions selected. |
| `python -m pip check` | `No broken requirements found.` |
| Dependency/tool regression module | 4 passed. |
| Dependency, configuration, LLM, and API focused suite | 53 passed, 2 intentional legacy-environment warnings. |
| Isolated Phase 4 tree built from committed Phase 3 plus only Phase 4 files | 210 passed, 2 intentional legacy-environment warnings in 10.82 seconds. |
| Full current working tree, including the separately preserved Phase 46 overlay | 216 passed, 2 intentional legacy-environment warnings in 10.19 seconds. |
| Ruff on changed Python files | Passed. |
| Python compilation and `git diff --check` | Passed. |
| `pip-audit --local` | Expected nonzero result: five unresolved findings detailed above. |

The isolated suite proves that Phase 4 passes without depending on the unrelated preserved
Phase 46 working-tree changes. Neither full run emitted Starlette's deprecated plain-httpx
fallback warning. The only warnings deliberately exercise backward-compatible legacy Apex
environment-variable aliases.

The native-provider smoke check separately imported llama-cpp-python, confirmed this build
has no GPU-offload support, validated a generated structural GGUF through
`LocalLLMProvider.validate()`, and loaded it through `llama_cpp.Llama`. The synthetic
fixture was not used to claim generation quality.

## Remaining unknowns

- Clean installs on Python 3.10, Python 3.12+, Windows, macOS, ARM, and musl Linux are
  **UNKNOWN**; only Linux x86_64 / Python 3.11 was exercised.
- CUDA, Metal, ROCm/HIP, Vulkan, and SYCL installation/runtime behavior is **UNKNOWN**
  because this host has no configured accelerator backend.
- Real embedding/reranker models and real LLM generation quality remain **UNKNOWN** in this
  audit; no production model artifact was downloaded or benchmarked.
- Sentence-Transformers 6 compatibility is **UNKNOWN** pending the model/retrieval checks
  listed above.
- Advisory exploit reachability is **UNKNOWN** beyond code-path inspection; no absence-of-
  vulnerability claim is made.
- Full transitive licensing and commercial redistribution conclusions are **UNKNOWN**.
