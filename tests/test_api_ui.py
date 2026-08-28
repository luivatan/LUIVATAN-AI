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
    assert payload["long_term_memory"] == {
        "status": "unavailable",
        "optional": True,
        "prompt_use": False,
    }


def test_health_reports_memory_availability_without_exposing_contents(
    api_client, wired_services
):
    from apex_ai.memory.long_term import LongTermMemoryStore

    canary = "PRIVATE-MEMORY-CANARY"
    wired_services.long_term_memory = LongTermMemoryStore(
        wired_services.settings.long_term_memory_db_path
    )
    wired_services.long_term_memory.create(canary, kind="preference")

    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["long_term_memory"]["status"] == "ready"
    assert canary not in response.text


def test_query_endpoint_returns_citations(api_client):
    response = api_client.post(
        "/query", json={"question": "What temperature is a fever in adults?"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"]
    assert payload["citations"]
    assert payload["citations"][0]["page"] >= 1
    assert "context_text" not in payload
    assert "context_chunk_ids" not in payload


def test_phase42_does_not_extract_or_prompt_with_long_term_memory(
    api_client, wired_services
):
    from apex_ai.memory.long_term import LongTermMemoryStore

    private_memory = "PRIVATE-LONG-TERM-MEMORY-CANARY"
    wired_services.long_term_memory = LongTermMemoryStore(
        wired_services.settings.long_term_memory_db_path
    )
    wired_services.long_term_memory.create(private_memory, kind="preference")

    response = api_client.post(
        "/query", json={"question": "What temperature is a fever in adults?"}
    )

    assert response.status_code == 200
    assert wired_services.long_term_memory.count() == 1
    assert private_memory not in repr(FakeLLM.last_messages)


def test_phase43_candidate_like_chat_is_not_automatically_persisted(
    api_client, wired_services
):
    from apex_ai.memory.extraction import MemoryCandidateExtractor
    from apex_ai.memory.long_term import LongTermMemoryStore

    question = "I prefer concise answers. What temperature is a fever in adults?"
    wired_services.memory_extractor = MemoryCandidateExtractor()
    wired_services.long_term_memory = LongTermMemoryStore(
        wired_services.settings.long_term_memory_db_path
    )
    assert wired_services.memory_extractor.extract(question)

    response = api_client.post("/query", json={"question": question})

    assert response.status_code == 200
    assert wired_services.long_term_memory.count() == 0


def test_ingest_endpoint(api_client, tmp_path):
    response = api_client.post(
        "/documents/ingest", json={"path": str(DATA_DIR / "burn_care.md")}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "indexed"


def test_models_endpoint_lists_discovered(api_client):
    assert api_client.get("/models").status_code == 200


def test_rag_debug_route_does_not_exist_for_normal_users(api_client):
    response = api_client.post("/debug/rag", json={"question": "fever"})
    assert response.status_code == 404


def test_rag_debug_route_is_explicitly_gated(wired_services):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from apex_ai.api.server import create_api

    debug_settings = replace(wired_services.settings, rag_debug=True)
    wired_services.settings = debug_settings
    wired_services.engine.settings = debug_settings
    client = TestClient(create_api(wired_services, include_web=False))
    memory_turns = len(wired_services.memory.turns)
    response = client.post(
        "/debug/rag", json={"question": "What temperature is a fever?"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieval"]["candidates"]
    assert payload["conversation_context"]["character_count"] >= 0
    assert payload["conversation_context"]["character_count"] <= (
        wired_services.settings.history_char_limit
    )
    assert payload["final_context"]
    assert payload["model_response"]
    assert payload["sources"]
    assert payload["timings_ms"]["prepare_total"] >= 0
    assert len(wired_services.memory.turns) == memory_turns
    assert "/debug/rag" not in client.get("/openapi.json").text


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
