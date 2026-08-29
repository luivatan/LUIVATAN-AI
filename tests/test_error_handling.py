"""Phase 5 error-boundary tests for API, streaming, runtime, and browser code."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from apex_ai.api.errors import APIError, install_error_handlers
from apex_ai.core.errors import UNEXPECTED_ERROR_MESSAGE, ProviderError
from tests.conftest import DATA_DIR

_PRIVATE_CANARY = "PRIVATE-ERROR-CANARY-7f04"


class InvalidPayload(BaseModel):
    count: int


def _assert_problem(response, status: int, code: str) -> dict:
    assert response.status_code == status
    payload = response.json()
    assert payload["detail"] == payload["error"]["message"]
    assert payload["error"]["code"] == code
    assert isinstance(payload["error"]["retryable"], bool)
    assert "Traceback" not in response.text
    return payload["error"]


def _error_test_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/missing")
    def missing():
        raise HTTPException(status_code=404, detail="The requested record is missing.")

    @app.get("/unavailable")
    def unavailable():
        raise APIError(
            503,
            "The optional component is unavailable.",
            code="component_unavailable",
            retryable=True,
        )

    @app.post("/validate")
    def validate(payload: InvalidPayload):
        return payload

    @app.get("/expected")
    def expected():
        try:
            raise RuntimeError(_PRIVATE_CANARY)
        except RuntimeError as cause:
            raise ProviderError(
                what="The configured provider failed.",
                why=f"Traceback from dependency: {_PRIVATE_CANARY}",
                fix="Check the provider and try again.",
            ) from cause

    @app.get("/legacy-bad-request")
    def legacy_bad_request():
        raise HTTPException(
            status_code=400,
            detail=f"RuntimeError: {_PRIVATE_CANARY} at /srv/private/request.json",
        )

    @app.get("/legacy-internal")
    def legacy_internal():
        raise HTTPException(
            status_code=500,
            detail=f"Traceback: {_PRIVATE_CANARY}",
        )

    @app.get("/unexpected")
    def unexpected():
        raise RuntimeError(f"Traceback: {_PRIVATE_CANARY}")

    return app


def test_http_errors_share_a_backward_compatible_public_envelope():
    client = TestClient(_error_test_app(), raise_server_exceptions=False)

    missing = _assert_problem(client.get("/missing"), 404, "not_found")
    assert missing["message"] == "The requested record is missing."
    assert missing["retryable"] is False

    unavailable = _assert_problem(
        client.get("/unavailable"), 503, "component_unavailable"
    )
    assert unavailable["retryable"] is True

    unsafe_legacy = _assert_problem(
        client.get("/legacy-bad-request"), 400, "invalid_request"
    )
    assert "diagnostic details were omitted" in unsafe_legacy["message"]
    assert _PRIVATE_CANARY not in unsafe_legacy["message"]
    assert "RuntimeError" not in unsafe_legacy["message"]
    assert "/srv/private" not in unsafe_legacy["message"]


def test_validation_errors_name_fields_without_echoing_submitted_values():
    client = TestClient(_error_test_app(), raise_server_exceptions=False)

    response = client.post("/validate", json={"count": _PRIVATE_CANARY})

    problem = _assert_problem(response, 422, "validation_error")
    assert len(problem["fields"]) == 1
    field = problem["fields"][0]
    assert field["field"] == "count"
    assert field["code"] == "int_parsing"
    assert "integer" in field["message"].lower()
    assert _PRIVATE_CANARY not in response.text
    assert "input" not in field


def test_expected_chained_errors_hide_dependency_details():
    client = TestClient(_error_test_app(), raise_server_exceptions=False)

    response = client.get("/expected")

    problem = _assert_problem(response, 502, "provider_error")
    assert problem["retryable"] is True
    assert "The configured provider failed" in problem["message"]
    assert "Check the provider" in problem["message"]
    assert "WHY:" not in problem["message"]
    assert _PRIVATE_CANARY not in response.text
    assert "RuntimeError" not in response.text


def test_public_expected_errors_redact_paths_endpoints_credentials_and_diagnostics():
    credential = "sk-" + "privatecredential123"
    error = ProviderError(
        what=(
            "The provider at https://internal.example/v1 failed while loading "
            "`/srv/private/models/patient.gguf`."
        ),
        why=f"RuntimeError: {_PRIVATE_CANARY}",
        fix=(
            "Check C:\\Users\\private\\provider.log and "
            f"API key={credential}."
        ),
    )

    public = error.public_message()
    assert "The provider at <configured endpoint> failed" in public
    assert "HOW TO FIX:" in public
    assert "WHY:" not in public
    for private_value in (
        "internal.example",
        "/srv/private",
        "C:\\Users\\private",
        credential,
        _PRIVATE_CANARY,
        "RuntimeError",
    ):
        assert private_value not in public

    diagnostic = ProviderError(
        what=f"RuntimeError({_PRIVATE_CANARY!r})",
        fix="Try again.",
    ).public_message()
    assert "diagnostic details were omitted" in diagnostic
    assert _PRIVATE_CANARY not in diagnostic
    assert "RuntimeError" not in diagnostic


def test_unexpected_api_errors_are_generic_and_non_diagnostic():
    client = TestClient(_error_test_app(), raise_server_exceptions=False)

    for path in ("/legacy-internal", "/unexpected"):
        response = client.get(path)
        problem = _assert_problem(response, 500, "internal_error")
        assert problem == {
            "code": "internal_error",
            "message": UNEXPECTED_ERROR_MESSAGE,
            "retryable": False,
        }
        assert _PRIVATE_CANARY not in response.text
        assert "RuntimeError" not in response.text


def test_create_api_installs_error_handlers_and_hides_blocked_startup_details(settings):
    from apex_ai.api.server import create_api
    from apex_ai.runtime import ApexServices

    services = ApexServices(settings=settings, startup_error=_PRIVATE_CANARY)
    client = TestClient(create_api(services, include_web=False))

    response = client.post("/query", json={"question": "test"})

    problem = _assert_problem(response, 503, "service_not_ready")
    assert "Open Settings" in problem["message"]
    assert _PRIVATE_CANARY not in response.text


def test_unexpected_startup_error_is_safe_while_the_cause_stays_internal(settings):
    from apex_ai.runtime import build_services

    def fail_embedding_initialization(_settings):
        raise RuntimeError(f"Traceback: {_PRIVATE_CANARY}")

    services = build_services(settings, embedding_factory=fail_embedding_initialization)

    assert services.ready is False
    assert services.startup_error == UNEXPECTED_ERROR_MESSAGE
    assert _PRIVATE_CANARY not in services.startup_error


class _ExplodingLLM:
    def stream(self, *args, **kwargs):
        del args, kwargs
        raise RuntimeError(f"Traceback: {_PRIVATE_CANARY}")
        yield  # pragma: no cover - makes this a generator


def test_streaming_errors_use_the_same_safe_problem_shape(settings, embeddings, store):
    from apex_ai.api.server import create_api
    from apex_ai.documents.service import IngestionService
    from apex_ai.memory.conversation import ConversationMemory
    from apex_ai.memory.conversations import ConversationStore
    from apex_ai.models.manager import ModelManager
    from apex_ai.rag.engine import RagEngine
    from apex_ai.rag.query_processing import QueryProcessor
    from apex_ai.retrieval.keyword import BM25Index
    from apex_ai.retrieval.pipeline import HybridRetriever
    from apex_ai.retrieval.reranker import LexicalReranker
    from apex_ai.runtime import ApexServices

    ingestion = IngestionService(settings, store)
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    retriever = HybridRetriever(store, settings, BM25Index(store))
    memory = ConversationMemory(settings.memory_path, settings.memory_turns)
    reranker = LexicalReranker()
    query_processor = QueryProcessor(enabled=False)
    engine = RagEngine(
        settings=settings,
        store=store,
        retriever=retriever,
        reranker=reranker,
        memory=memory,
        llm_provider=_ExplodingLLM(),
        query_processor=query_processor,
    )
    services = ApexServices(
        settings=settings,
        embeddings=embeddings,
        store=store,
        ingestion=ingestion,
        retriever=retriever,
        reranker=reranker,
        memory=memory,
        query_processor=query_processor,
        engine=engine,
        models=ModelManager(settings),
    )
    client = TestClient(
        create_api(
            services,
            conversations=ConversationStore(settings.conversation_db_path),
            include_web=False,
        )
    )

    response = client.post(
        "/chat/stream",
        json={
            "question": "What temperature is a fever in adults?",
            "request_id": "error-shape",
        },
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[0]["type"] == "meta"
    failure = events[-1]
    assert failure["type"] == "error"
    assert failure["message"] == failure["error"]["message"]
    assert failure["error"]["code"] == "provider_error"
    assert failure["error"]["retryable"] is True
    assert _PRIVATE_CANARY not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text


def test_browser_error_helpers_hide_native_and_legacy_server_details():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable; browser helper execution is UNKNOWN")

    javascript = Path("apex_ai/web/static/app.js").read_text(encoding="utf-8")
    prefix = javascript.split("function escapeHTML", 1)[0]
    script = """
      globalThis.localStorage = { getItem: () => null };
      globalThis.FormData = class FormData {};
    """ + prefix + f"""
      (async () => {{
        const secret = {_PRIVATE_CANARY!r};
        const structured = await errorFromResponse({{
          status: 503,
          json: async () => ({{ error: {{
            code: "service_not_ready", message: "Open Settings.", retryable: true
          }} }}),
        }});
        if (!(structured instanceof ApexAPIError) || structured.code !== "service_not_ready") throw new Error("structured");
        if (errorMessage(structured) !== "Open Settings." || !structured.retryable) throw new Error("metadata");

        const legacy = await errorFromResponse({{
          status: 500,
          json: async () => ({{ detail: `Traceback ${{secret}}` }}),
        }});
        if (errorMessage(legacy).includes(secret) || errorMessage(legacy).includes("Traceback")) throw new Error("legacy leak");
        const unsafeLowStatus = await errorFromResponse({{
          status: 400,
          json: async () => ({{ detail: `RuntimeError: ${{secret}} at /srv/private/request.json` }}),
        }});
        if (errorMessage(unsafeLowStatus).includes(secret) || errorMessage(unsafeLowStatus).includes("RuntimeError")) throw new Error("unsafe low-status leak");
        if (errorMessage(new Error(secret)).includes(secret)) throw new Error("native leak");
        const legacyStream = streamErrorFromEvent({{ message: `Traceback ${{secret}}` }});
        if (errorMessage(legacyStream).includes(secret) || legacyStream.code !== "stream_error") throw new Error("legacy stream leak");
        const structuredStream = streamErrorFromEvent({{ error: {{ code: "provider_error", message: "Check the provider.", retryable: true }} }});
        if (errorMessage(structuredStream) !== "Check the provider." || !structuredStream.retryable) throw new Error("structured stream");

        globalThis.fetch = async () => {{ throw new Error(secret); }};
        try {{ await request("/offline"); throw new Error("network accepted"); }}
        catch (error) {{
          if (!(error instanceof ApexAPIError) || error.code !== "network_error") throw error;
          if (errorMessage(error).includes(secret)) throw new Error("network leak");
        }}
        console.log("frontend-errors: passed");
      }})().catch(error => {{ console.error(error); process.exit(1); }});
    """
    completed = subprocess.run(
        [node, "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "frontend-errors: passed"
