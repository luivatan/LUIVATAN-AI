"""Chat-first application tests using the real RAG pipeline with a deterministic LLM."""

from __future__ import annotations

import json

import pytest

from tests.conftest import DATA_DIR, FakeLLM


@pytest.fixture()
def web_services(settings, ingestion, embeddings, store):
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
    query_processor = QueryProcessor(enabled=False)
    reranker = LexicalReranker()
    engine = RagEngine(
        settings=settings,
        store=store,
        retriever=retriever,
        reranker=reranker,
        memory=memory,
        llm_provider=FakeLLM(),
        query_processor=query_processor,
    )
    return ApexServices(
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


@pytest.fixture()
def web_client(web_services):
    from fastapi.testclient import TestClient

    from apex_ai.api.server import create_api
    from apex_ai.memory.conversations import ConversationStore

    conversations = ConversationStore(web_services.settings.conversation_db_path)
    return TestClient(create_api(web_services, conversations=conversations))


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


# -- persistent conversation data --------------------------------------------


def test_conversation_store_crud_search_and_persistence(tmp_path):
    from apex_ai.memory.conversations import ConversationStore

    path = tmp_path / "history.db"
    store = ConversationStore(path)
    conversation = store.create()
    user = store.add_message(conversation.id, "user", "Explain oral rehydration therapy")
    store.add_message(conversation.id, "assistant", "Use the cited guidance.")

    assert store.get(conversation.id).title.startswith("Explain oral rehydration")
    assert store.list(search="rehydration")[0].id == conversation.id
    assert store.list(search="cited guidance")[0].id == conversation.id
    assert len(ConversationStore(path).messages(conversation.id)) == 2
    assert store.recent_turns(conversation.id, 8) == [
        {"user": user.content, "assistant": "Use the cited guidance."}
    ]
    assert store.delete(conversation.id)
    assert store.get(conversation.id) is None


def test_conversation_memory_adapter_excludes_pending_user(tmp_path):
    from apex_ai.memory.conversations import (
        ConversationMemoryAdapter,
        ConversationStore,
    )

    store = ConversationStore(tmp_path / "history.db")
    conversation = store.create()
    store.add_message(conversation.id, "user", "First question")
    store.add_message(conversation.id, "assistant", "First answer")
    pending = store.add_message(conversation.id, "user", "Follow-up")
    adapter = ConversationMemoryAdapter(
        store, conversation.id, 8, exclude_user_message_id=pending.id
    )
    assert adapter.recent() == [{"user": "First question", "assistant": "First answer"}]


# -- browser/API integration --------------------------------------------------


def test_web_shell_is_chat_first_and_has_security_headers(web_client):
    response = web_client.get("/")
    assert response.status_code == 200
    assert "New chat" in response.text
    assert "conversationSearch" in response.text
    assert "Message Apex AI" in response.text
    assert "Documents" in response.text
    assert "Settings" in response.text
    assert "dashboard" not in response.text.lower()
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_static_assets_include_responsive_themes_and_code_blocks(web_client):
    css = web_client.get("/assets/app.css")
    javascript = web_client.get("/assets/app.js")
    assert css.status_code == javascript.status_code == 200
    assert "@media (max-width: 720px)" in css.text
    assert 'html[data-theme="light"]' in css.text
    assert ".code-block" in css.text
    assert "renderMarkdown" in javascript.text
    assert "navigator.clipboard" in javascript.text
    assert 'regenerate: true' in javascript.text


def test_streaming_chat_uses_real_engine_and_persists_verified_citations(web_client):
    response = web_client.post(
        "/chat/stream",
        json={"question": "What temperature is a fever in adults?", "request_id": "request-1"},
    )
    assert response.status_code == 200
    stream = events(response)
    assert stream[0]["type"] == "meta"
    assert any(item["type"] == "token" for item in stream)
    final = next(item for item in stream if item["type"] == "final")
    assert "38 C" in final["message"]["content"]
    assert final["citations"]
    assert final["citations"][0]["page"] >= 1
    assert final["citations"][0]["text"]  # source drawer receives exact used chunk

    conversation_id = final["conversation"]["id"]
    saved = web_client.get(f"/conversations/{conversation_id}").json()
    assert [message["role"] for message in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][1]["citations"] == final["citations"]


def test_second_conversation_and_history_are_real_records(web_client):
    first = events(web_client.post(
        "/chat/stream", json={"question": "What counts as fever?", "request_id": "first"}
    ))[-1]
    second = events(web_client.post(
        "/chat/stream", json={"question": "When should help be sought?", "request_id": "second"}
    ))[-1]
    assert first["conversation"]["id"] != second["conversation"]["id"]
    history = web_client.get("/conversations").json()
    assert len(history) == 2
    assert all(item["message_count"] == 2 for item in history)


def test_regenerate_replaces_answer_without_duplicating_user(web_client):
    initial = events(web_client.post(
        "/chat/stream", json={"question": "What temperature is fever?", "request_id": "initial"}
    ))
    conversation_id = initial[0]["conversation"]["id"]
    regenerated = events(web_client.post(
        "/chat/stream",
        json={"conversation_id": conversation_id, "request_id": "regen", "regenerate": True},
    ))
    assert regenerated[-1]["type"] == "final"
    saved = web_client.get(f"/conversations/{conversation_id}").json()["messages"]
    assert [message["role"] for message in saved] == ["user", "assistant"]


def test_conversation_rename_search_delete_and_clear(web_client):
    created = web_client.post("/conversations", json={"title": "Original"}).json()
    renamed = web_client.patch(
        f"/conversations/{created['id']}", json={"title": "Renamed research"}
    )
    assert renamed.status_code == 200
    assert web_client.get("/conversations?search=research").json()[0]["id"] == created["id"]
    assert web_client.delete(f"/conversations/{created['id']}").status_code == 200
    web_client.post("/conversations", json={"title": "One"})
    assert web_client.delete("/conversations").json()["deleted"] == 1


def test_browser_upload_connects_to_existing_ingestion_pipeline(web_client):
    content = (DATA_DIR / "burn_care.md").read_bytes()
    response = web_client.post(
        "/documents/upload",
        files={"file": ("burn_care.md", content, "text/markdown")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "indexed"
    assert payload["chunks"] > 0
    documents = web_client.get("/documents").json()
    assert {document["name"] for document in documents} >= {
        "sample_first_aid.pdf", "burn_care.md"
    }


def test_browser_upload_rejects_unsupported_type(web_client):
    response = web_client.post(
        "/documents/upload", files={"file": ("malware.exe", b"not a doc", "application/octet-stream")}
    )
    assert response.status_code == 415


def test_generation_manager_supports_stop_and_one_request_per_conversation():
    from apex_ai.api.chat import GenerationManager

    manager = GenerationManager()
    event = manager.register("one", "conversation")
    assert manager.request_stop("one") is True
    assert event.is_set()
    with pytest.raises(ValueError):
        manager.register("two", "conversation")
    manager.unregister("one")
    assert manager.request_stop("missing") is False
