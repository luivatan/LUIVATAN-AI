# Apex AI Phase 3 — Environment Configuration

- **Implementation date:** 2026-08-29 (America/Chicago)
- **Roadmap scope:** move operational configuration into environment variables, provide a
  safe example file, and keep secrets out of ordinary configuration representations

## Before

Apex AI already had a frozen `Settings` dataclass, project-root-relative path handling,
`.env` loading, canonical `APEX_*` names, and selected legacy aliases. Phase 3 reused that
system rather than introducing another configuration framework.

The inspection found these concrete gaps:

- `.env.example` did not fully cover settings already read by the application:
  `APEX_COLLECTION`, `APEX_EMBEDDING_BATCH_SIZE`, and an assignable
  `APEX_MAX_UPLOAD_MB` example were missing.
- RAG answer generation hardcoded `768` output tokens and temperature `0.2` in its normal,
  debug, and streaming paths.
- Ollama and OpenAI-compatible requests hardcoded connect/read timeouts of 5/300 seconds.
- `Settings` included the OpenAI API key in its generated dataclass representation.
- The unauthenticated server bound to every interface by default.
- The Transformers provider did not explicitly pass a cache-only policy to both model and
  tokenizer loading when `APEX_OFFLINE=1`.
- The example file described offline operation too broadly and did not explain that a
  configured HTTP provider can transmit prompt data.

## After

### One typed configuration boundary

`apex_ai.config.settings.Settings` remains frozen and is still loaded through
`load_settings()`. Existing entry points, path resolution, `with_overrides()`, and legacy
environment aliases remain available.

The following operational settings were added:

| Environment variable | Default | Accepted environment range | Consumer |
|---|---:|---:|---|
| `APEX_GENERATION_MAX_TOKENS` | `768` | 1–131,072 | Normal, debug, and streaming RAG answer generation |
| `APEX_GENERATION_TEMPERATURE` | `0.2` | finite 0.0–2.0 | Normal, debug, and streaming RAG answer generation |
| `APEX_PROVIDER_CONNECT_TIMEOUT_SECONDS` | `5` | finite 0.1–3,600 seconds | Ollama and OpenAI-compatible HTTP connections |
| `APEX_PROVIDER_READ_TIMEOUT_SECONDS` | `300` | finite 0.1–86,400 seconds | Ollama and OpenAI-compatible HTTP responses |

Malformed, non-finite, or out-of-range values fail safely to the documented default, which
preserves the existing parser's compatibility behavior for malformed values. Additional
resource/network values now receive basic bounds where invalid values would otherwise
reach a model library or server:

| Existing variable | Safe environment range | Invalid-value fallback |
|---|---:|---:|
| `APEX_EMBEDDING_BATCH_SIZE` | 1–4,096 | `32` |
| `APEX_LLM_CONTEXT_SIZE` | 256–1,048,576 | `4096` |
| `APEX_N_THREADS` | 0–4,096 | `0` |
| `APEX_SERVER_PORT` | 1–65,535 | `7860` |

These checks apply to environment parsing. Direct construction of `Settings` remains a
normal typed Python API and was not replaced with runtime coercion.

### Generation stays behind `LLMProvider`

`RagEngine` now reads answer limits from `Settings` and passes them through the existing
`LLMProvider.generate()` and `LLMProvider.stream()` contracts. The defaults are exactly
the previous behavior: 768 output tokens at temperature 0.2.

The smaller fixed limits used for query rewriting and decomposition remain intentionally
separate. Those are bounded retrieval operations, not user-facing answer generation, so a
large answer budget cannot silently inflate query-processing calls.

### Provider network controls

Ollama and OpenAI-compatible providers now pass the configured `(connect, read)` timeout
pair to `requests`. Provider cache identity includes both values, so a provider is rebuilt
when either timeout changes.

Cache identity also tracks API-key changes through a SHA-256 fingerprint. Plaintext keys
are not retained in the cache key. This fixes stale-provider behavior without putting a
secret into cache diagnostics or representations.

### Secret-safe settings representation

`openai_api_key` is a dataclass field with `repr=False` and secret metadata. Therefore
ordinary `repr(settings)` / `str(settings)` output does not include either the key name or
its value. Provider code still receives the string directly when the OpenAI-compatible
provider is selected.

`.env.example` contains only an empty commented key assignment. It contains no real key,
secret-like placeholder, or machine-specific model-file path.

### Safer server default

The default for `APEX_SERVER_NAME` is now `127.0.0.1`. This matches the current
single-user trusted-local boundary while the application has no inbound authentication.

An operator can still explicitly set:

```dotenv
APEX_SERVER_NAME=0.0.0.0
```

That override is appropriate only behind a deliberately protected network or deployment
boundary. Phase 3 does not claim that an all-interface deployment is authenticated.

### Exact offline and egress boundary

When `APEX_OFFLINE=1`, the Transformers provider explicitly loads both
`AutoTokenizer` and `AutoModelForCausalLM` with `local_files_only=True` before constructing
the generation pipeline. A missing cached artifact therefore fails instead of initiating
a model download. Existing cache-only embedding and reranker behavior remains intact.

`APEX_OFFLINE` is a **model-download/cache policy**, not a process firewall. It does not
block endpoints that the operator explicitly selected:

- an OpenAI-compatible endpoint receives the question, bounded conversation history, and
  retrieved evidence used to build the prompt; and
- an Ollama URL is local only when the configured URL points to a local service.

A complete air gap requires a local provider plus operating-system or network controls.
The example environment file states this directly instead of making a broader guarantee.

### Complete example coverage

`.env.example` now documents all 57 canonical `APEX_*` variables read by
`load_settings()`, including collection name, embedding batch size, upload limit,
generation controls, and HTTP timeouts. It retains safe defaults and notes which provider
choices may cause data egress.

## Preserved behavior

- ChromaDB, document ingestion, hybrid retrieval, reranking, citations, memory, and the
  existing API/UI/CLI entry points are unchanged.
- Local llama.cpp, Ollama, OpenAI-compatible, and Transformers implementations remain
  behind the existing `LLMProvider` interface.
- Legacy aliases such as `LLM_PROVIDER`, `LLAMA_MODEL_PATH`, `OLLAMA_*`, and `OPENAI_*`
  remain supported with the existing deprecation warnings.
- Internet access is not required by the deterministic offline test suite or by configured
  local inference with cached/local model artifacts.
- No model response, source, benchmark, or deployment capability was fabricated.

## Verification

Focused Phase 3 configuration/provider/RAG regression run:

```text
APEX_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -m pytest -q \
  tests/test_config.py tests/test_llm.py tests/test_engine.py

52 passed, 2 warnings in 1.23s
```

An isolated tree was built from committed `HEAD`, then only the Phase 3 implementation and
test files were overlaid. This excludes the preserved, unrelated Phase 46 working-tree
changes from the result:

```text
APEX_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -m pytest -q tests/

206 passed, 3 warnings in 10.43s
```

The warnings are one Starlette/httpx deprecation warning and the two intentional legacy
environment-alias deprecation warnings. There were no test failures.

Focused tests cover environment overrides, finite/range fallbacks, API-key redaction,
loopback default and explicit wide binding, example-file coverage, configured timeout
propagation, cache identity without plaintext keys, cache-only Transformers loading, and
generation settings in both single-shot and streaming RAG paths.

Static checks also validate Python compilation, complete canonical environment-name
coverage, and whitespace-safe diffs.

## Known unknowns

- Real generation through llama.cpp, Ollama, OpenAI-compatible endpoints, and cached
  Transformers models is **UNKNOWN** in this environment because no generation model,
  endpoint, or credential was configured. Verification requires an operator-selected
  model/provider and must not use committed credentials.
- Performance and memory use at non-default upper bounds are **UNKNOWN**; the bounds are
  safety limits, not recommended tuning targets or benchmark claims.
- Network isolation is **UNKNOWN** unless verified at the deployment/operating-system
  layer. `APEX_OFFLINE=1` alone must not be used as proof of an air gap.
