"""API + UI behavior tests (TestClient, no real server, no real models)."""

from __future__ import annotations

import pytest

from tests.conftest import DATA_DIR, FakeLLM


@pytest.fixture()
def wired_services(settings, ingestion, embeddings, store):
    """A services container wired to the fast fake stack."""
    from apex_ai.memory.conversation import ConversationMemory
    from apex_ai.models.manager import ModelManager
    from apex_ai.rag.engine import RagEngine
    from apex_ai.rag.query_processing import QueryProcessor
    from apex_ai.retrieval.keyword import BM25Index
    from apex_ai.retrieval.pipeline import HybridRetriever
    from apex_ai.retrieval.reranker import LexicalReranker
    from apex_ai.runtime import ApexServices

    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf")
    retriever = HybridRetriever(store, settings, BM25Index(store))
    memory = ConversationMemory(settings.memory_path, settings.memory_turns)
    engine = RagEngine(
        settings=settings, store=store, retriever=retriever,
        reranker=LexicalReranker(), memory=memory, llm_provider=FakeLLM(),
        query_processor=QueryProcessor(enabled=False),
    )
    return ApexServices(
        settings=settings, embeddings=embeddings, store=store, ingestion=ingestion,
        retriever=retriever, reranker=LexicalReranker(), memory=memory,
        query_processor=QueryProcessor(enabled=False), engine=engine,
        models=ModelManager(settings),
    )


@pytest.fixture()
def api_client(wired_services):
    from fastapi.testclient import TestClient

    from apex_ai.api.server import create_api

    return TestClient(create_api(wired_services))


# ---------------- API ----------------


def test_health_reports_ready(api_client, wired_services):
    response = api_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["documents"] == 1
    assert payload["chunks"] > 0


def test_query_endpoint_returns_citations(api_client):
    response = api_client.post(
        "/query", json={"question": "What temperature is a fever in adults?"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"]
    assert payload["citations"]
    assert payload["citations"][0]["page"] >= 1


def test_ingest_endpoint(api_client, tmp_path):
    response = api_client.post(
        "/documents/ingest", json={"path": str(DATA_DIR / "burn_care.md")}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "indexed"


def test_models_endpoint_lists_discovered(api_client):
    assert api_client.get("/models").status_code == 200


def test_select_missing_model_returns_400_with_message(api_client):
    response = api_client.post("/models/select", json={"name": "ghost.gguf"})
    assert response.status_code == 400
    assert "HOW TO FIX" in response.json()["detail"]


# ---------------- UI ----------------


def test_ui_builds_blocks(wired_services):
    gradio = pytest.importorskip("gradio")
    from apex_ai.ui.gradio_app import create_app

    interface = create_app(wired_services)
    assert isinstance(interface, gradio.Blocks)


def test_ui_builds_even_when_startup_failed(settings):
    """The UI must open with a banner when the store is broken, not crash."""
    gradio = pytest.importorskip("gradio")
    from apex_ai.runtime import ApexServices
    from apex_ai.ui.gradio_app import create_app

    broken = ApexServices(settings=settings, startup_error="MODEL NOT FOUND\n\nSet APEX_MODEL_PATH")
    interface = create_app(broken)
    assert isinstance(interface, gradio.Blocks)


def test_ui_brands_as_apex_ai(wired_services):
    pytest.importorskip("gradio")
    from apex_ai.ui.gradio_app import create_app

    interface = create_app(wired_services)
    assert "Apex AI" in (interface.title or "")
