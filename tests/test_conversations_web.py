"""Chat-first application tests using the real RAG pipeline with a deterministic LLM."""

from __future__ import annotations

import json

import pytest

from tests.conftest import DATA_DIR, FakeLLM

USER = "user-1"


@pytest.fixture()
def web_services(settings, ingestion, embeddings, store):
    from apex_ai.auth.service import AuthService
    from apex_ai.auth.sessions import SessionStore
    from apex_ai.auth.users import UserStore
    from apex_ai.documents.collections import CollectionStore
    from apex_ai.memory.confirmation import MemoryConfirmationService
    from apex_ai.memory.conversation import ConversationMemory
    from apex_ai.memory.extraction import MemoryCandidateExtractor
    from apex_ai.memory.long_term import LongTermMemoryStore
    from apex_ai.models.manager import ModelManager
    from apex_ai.rag.engine import RagEngine
    from apex_ai.rag.query_processing import QueryProcessor
    from apex_ai.retrieval.keyword import BM25Index
    from apex_ai.retrieval.pipeline import HybridRetriever
    from apex_ai.retrieval.reranker import LexicalReranker
    from apex_ai.runtime import ApexServices
    from apex_ai.security.memory import MemorySafetyPolicy

    retriever = HybridRetriever(store, settings, BM25Index(store))
    memory = ConversationMemory(settings.memory_path, settings.memory_turns)
    query_processor = QueryProcessor(enabled=False)
    reranker = LexicalReranker()
    memory_safety = MemorySafetyPolicy()
    memory_extractor = MemoryCandidateExtractor(memory_safety)
    long_term_memory = LongTermMemoryStore(
        settings.long_term_memory_db_path,
        safety_policy=memory_safety,
    )
    memory_confirmation = MemoryConfirmationService(
        memory_extractor,
        long_term_memory,
    )
    auth = AuthService(
        UserStore(settings.users_db_path),
        SessionStore(settings.users_db_path),
        session_ttl_days=settings.session_ttl_days,
    )
    default_local_user = auth.ensure_default_local_account()
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", default_local_user.id)
    engine = RagEngine(
        settings=settings,
        store=store,
        retriever=retriever,
        reranker=reranker,
        memory=memory,
        llm_provider=FakeLLM(),
        query_processor=query_processor,
        long_term_memory=long_term_memory,
        user_id=default_local_user.id,
    )
    return ApexServices(
        settings=settings,
        embeddings=embeddings,
        store=store,
        ingestion=ingestion,
        retriever=retriever,
        reranker=reranker,
        memory=memory,
        long_term_memory=long_term_memory,
        memory_safety=memory_safety,
        memory_extractor=memory_extractor,
        memory_confirmation=memory_confirmation,
        query_processor=query_processor,
        engine=engine,
        models=ModelManager(settings),
        auth=auth,
        default_local_user=default_local_user,
        collections=CollectionStore(settings.collections_db_path),
    )


@pytest.fixture()
def web_client(web_services):
    from fastapi.testclient import TestClient

    from apex_ai.api.server import create_api
    from apex_ai.memory.conversations import ConversationStore

    conversations = ConversationStore(web_services.settings.conversation_db_path)
    conversations.backfill_owner(web_services.default_local_user.id)
    return TestClient(create_api(web_services, conversations=conversations))


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _fake_memory_key() -> str:
    return "sk-" + ("Qw8_" * 8)


# -- persistent conversation data --------------------------------------------


def test_conversation_store_crud_search_and_persistence(tmp_path):
    from apex_ai.memory.conversations import ConversationStore

    path = tmp_path / "history.db"
    store = ConversationStore(path)
    conversation = store.create(USER)
    user = store.add_message(USER, conversation.id, "user", "Explain oral rehydration therapy")
    store.add_message(USER, conversation.id, "assistant", "Use the cited guidance.")

    assert store.get(USER, conversation.id).title.startswith("Explain oral rehydration")
    assert store.list(USER, search="rehydration")[0].id == conversation.id
    assert store.list(USER, search="cited guidance")[0].id == conversation.id
    assert len(ConversationStore(path).messages(USER, conversation.id)) == 2
    assert store.recent_turns(USER, conversation.id, 8) == [
        {"user": user.content, "assistant": "Use the cited guidance."}
    ]
    assert store.delete(USER, conversation.id)
    assert store.get(USER, conversation.id) is None


def test_conversation_memory_adapter_excludes_pending_user(tmp_path):
    from apex_ai.memory.conversations import (
        ConversationMemoryAdapter,
        ConversationStore,
    )

    store = ConversationStore(tmp_path / "history.db")
    conversation = store.create(USER)
    store.add_message(USER, conversation.id, "user", "First question")
    store.add_message(USER, conversation.id, "assistant", "First answer")
    pending = store.add_message(USER, conversation.id, "user", "Follow-up")
    adapter = ConversationMemoryAdapter(
        store, USER, conversation.id, 8, exclude_user_message_id=pending.id
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
    assert "memoryConfirmationRegion" in response.text
    assert "conversationCollection" in response.text
    assert "collectionFilterRow" in response.text
    assert "dashboard" not in response.text.lower()
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_login_page_is_served_with_the_same_security_headers(web_client):
    response = web_client.get("/login")
    assert response.status_code == 200
    assert "authForm" in response.text
    assert "authEmail" in response.text
    assert "authPassword" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    login_js = web_client.get("/assets/login.js")
    assert login_js.status_code == 200
    assert "auth/login" in login_js.text and "auth/signup" in login_js.text


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
    assert ".memory-confirmation-card" in css.text
    assert "/memory/candidates/" in javascript.text
    assert "Review first · never save secrets" in javascript.text
    # Phase 15: Markdown tables.
    assert "table-wrap" in css.text
    assert "isTableSeparator" in javascript.text
    # Phase 16: dependency-free syntax highlighting (no CDN script for the product UI).
    assert "tok-keyword" in css.text
    assert "function highlightCode" in javascript.text
    assert "cdn." not in javascript.text.lower()  # no CDN script loaded for the product UI
    # Phase 17: per-message feedback controls, alongside copy/regenerate.
    assert "feedback-up" in javascript.text and "feedback-down" in javascript.text
    assert "/feedback" in javascript.text
    # Phase 46: memory management (view/delete/clear) in Settings.
    assert ".memory-row" in css.text
    assert "function loadMemories" in javascript.text
    assert "clearAllMemories" in javascript.text
    # Phase 49: conflict warning on the memory-confirmation card.
    assert ".memory-confirmation-conflict" in css.text
    assert "conflicts_with" in javascript.text
    # Phase 52: account/sign-out section in Settings.
    assert "function loadAccount" in javascript.text
    assert "auth/logout" in javascript.text
    # Phase 66/67: document collections and knowledge-base selection.
    assert ".collection-chip" in css.text
    assert ".document-collection-select" in css.text
    assert "function loadCollections" in javascript.text
    assert "function renderCollectionFilterRow" in javascript.text
    assert "conversationCollection" in javascript.text
    assert "/collections" in javascript.text


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


def test_memory_candidate_requires_approval_then_becomes_relevant_context(
    web_client, web_services
):
    """Phase 45: nothing reaches the prompt before explicit approval. Phase 47:
    after approval, a confirmed preference legitimately reaches the prompt as
    clearly separated, never-cited user context — never as document evidence."""
    preference = "I prefer concise answers."
    stream = events(
        web_client.post(
            "/chat/stream",
            json={
                "question": preference + " What temperature is a fever in adults?",
                "request_id": "memory-approval",
            },
        )
    )
    proposal = stream[0]["memory_candidates"][0]

    assert proposal["content"] == preference
    assert web_services.long_term_memory.count(web_services.default_local_user.id) == 0
    assert web_client.get("/memory/candidates").json() == [proposal]

    approved = web_client.post(
        f"/memory/candidates/{proposal['id']}/approve"
    )

    assert approved.status_code == 200
    assert approved.json()["memory"]["content"] == preference
    assert web_services.long_term_memory.count(web_services.default_local_user.id) == 1
    assert web_client.get("/memory/candidates").json() == []

    follow_up = web_client.post(
        "/chat/stream",
        json={
            "question": "What temperature is a fever in adults?",
            "request_id": "memory-now-in-prompt",
        },
    )
    assert follow_up.status_code == 200
    prompt = repr(FakeLLM.last_messages)
    assert preference in prompt
    assert "User context" in prompt

    final = next(item for item in events(follow_up) if item["type"] == "final")
    assert all(preference not in citation["text"] for citation in final["citations"])
    assert all(preference not in citation.get("source", "") for citation in final["citations"])


def test_memory_candidate_can_be_rejected_without_storing_content(
    web_client, web_services
):
    question = (
        "Please always include exact version numbers. "
        "What temperature is a fever in adults?"
    )
    first = events(
        web_client.post(
            "/chat/stream",
            json={"question": question, "request_id": "memory-reject"},
        )
    )
    proposal = first[0]["memory_candidates"][0]

    rejected = web_client.post(
        f"/memory/candidates/{proposal['id']}/reject"
    )

    assert rejected.status_code == 200
    assert rejected.json() == {"rejected": True}
    assert web_services.long_term_memory.count(web_services.default_local_user.id) == 0
    assert web_client.get("/memory/candidates").json() == []

    repeated = events(
        web_client.post(
            "/chat/stream",
            json={"question": question, "request_id": "memory-repeated"},
        )
    )
    assert repeated[0]["memory_candidates"] == []


def test_unsafe_memory_candidate_is_never_offered(web_client, web_services):
    question = (
        "Remember that my API key is "
        + _fake_memory_key()
        + ". What temperature is a fever in adults?"
    )

    stream = events(
        web_client.post(
            "/chat/stream",
            json={"question": question, "request_id": "unsafe-memory"},
        )
    )

    assert stream[0]["memory_candidates"] == []
    assert web_services.long_term_memory.pending(web_services.default_local_user.id) == []
    assert web_services.long_term_memory.count(web_services.default_local_user.id) == 0


def test_conversation_summary_folds_old_turns_and_reaches_later_prompts(
    web_client, web_services
):
    """Phase 50: off by default (proven by the companion test below); when
    enabled, turns that fall out of the live short-term window get folded into
    a rolling summary that later turns' prompts can see."""
    from apex_ai.config.settings import with_overrides
    from apex_ai.memory.conversations import ConversationStore

    web_services.settings = with_overrides(
        web_services.settings, conversation_summary=True, memory_turns=1
    )
    conversation_id = None
    for i in range(3):
        stream = events(
            web_client.post(
                "/chat/stream",
                json={
                    "question": "What temperature is a fever in adults?",
                    "conversation_id": conversation_id,
                    "request_id": f"summary-turn-{i}",
                },
            )
        )
        conversation_id = stream[0]["conversation"]["id"]

    store = ConversationStore(web_services.settings.conversation_db_path)
    summary, summarized_count = store.summary_state(
        web_services.default_local_user.id, conversation_id
    )
    assert summary  # FakeLLM's canned response, folded in as the "summary" text
    assert summarized_count > 0

    # A stored summary is always read into the prompt regardless of whether new
    # summarization work is currently enabled (only *generating more* summary is
    # gated by the setting) - turn it off here so this turn's own summarization
    # pass doesn't overwrite FakeLLM.last_messages before this assertion runs.
    web_services.settings = with_overrides(web_services.settings, conversation_summary=False)
    web_client.post(
        "/chat/stream",
        json={
            "question": "What temperature is a fever in adults?",
            "conversation_id": conversation_id,
            "request_id": "summary-turn-final",
        },
    )
    prompt = repr(FakeLLM.last_messages)
    assert "Summary of earlier conversation" in prompt


def test_conversation_summary_is_off_by_default(web_client, web_services):
    from apex_ai.config.settings import with_overrides
    from apex_ai.memory.conversations import ConversationStore

    web_services.settings = with_overrides(web_services.settings, memory_turns=1)
    conversation_id = None
    for i in range(3):
        stream = events(
            web_client.post(
                "/chat/stream",
                json={
                    "question": "What temperature is a fever in adults?",
                    "conversation_id": conversation_id,
                    "request_id": f"no-summary-turn-{i}",
                },
            )
        )
        conversation_id = stream[0]["conversation"]["id"]

    store = ConversationStore(web_services.settings.conversation_db_path)
    summary, summarized_count = store.summary_state(
        web_services.default_local_user.id, conversation_id
    )
    assert summary == ""
    assert summarized_count == 0


def test_memory_candidate_flags_a_conflict_with_an_existing_memory(web_client, web_services):
    """Phase 49: detection only - the existing memory is untouched; a human
    still decides via approve/reject (Phase 45) or delete (Phase 46)."""
    existing = web_services.long_term_memory.create(
        web_services.default_local_user.id, "I prefer detailed answers.", kind="preference"
    )

    stream = events(
        web_client.post(
            "/chat/stream",
            json={
                "question": "I prefer concise answers. What temperature is a fever in adults?",
                "request_id": "conflict-check",
            },
        )
    )
    proposal = stream[0]["memory_candidates"][0]
    assert proposal["conflicts_with"]["id"] == existing.id
    assert proposal["conflicts_with"]["content"] == "I prefer detailed answers."

    listed = web_client.get("/memory/candidates").json()
    assert listed[0]["conflicts_with"]["id"] == existing.id

    # Nothing was auto-resolved: both memories still exist independently.
    assert web_services.long_term_memory.count(web_services.default_local_user.id) == 1
    web_client.post(f"/memory/candidates/{proposal['id']}/approve")
    assert web_services.long_term_memory.count(web_services.default_local_user.id) == 2
    assert existing == web_services.long_term_memory.get(web_services.default_local_user.id, existing.id)


def test_memory_management_list_delete_and_clear(web_client, web_services):
    """Phase 46: confirmed-memory CRUD is a direct store operation, not routed
    through candidate proposal/approval."""
    first = web_services.long_term_memory.create(
        web_services.default_local_user.id, "Prefers concise answers.", kind="preference"
    )
    web_services.long_term_memory.create(
        web_services.default_local_user.id, "Working on a Q3 budget review.", kind="ongoing_context"
    )

    listed = web_client.get("/memory").json()
    assert {item["content"] for item in listed} == {
        "Prefers concise answers.",
        "Working on a Q3 budget review.",
    }

    only_preferences = web_client.get("/memory", params={"kind": "preference"}).json()
    assert [item["content"] for item in only_preferences] == ["Prefers concise answers."]

    deleted = web_client.delete(f"/memory/{first.id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert web_client.delete(f"/memory/{first.id}").status_code == 404

    cleared = web_client.delete("/memory")
    assert cleared.json() == {"deleted": 1}
    assert web_client.get("/memory").json() == []


def test_memory_management_degrades_when_optional_store_is_unavailable(settings):
    from fastapi.testclient import TestClient

    from apex_ai.api.server import create_api
    from apex_ai.runtime import ApexServices

    broken = ApexServices(settings=settings, startup_error="MODEL NOT FOUND")
    client = TestClient(create_api(broken, include_web=False))
    assert client.get("/memory").status_code == 503
    assert client.delete("/memory/anything").status_code == 503
    assert client.delete("/memory").status_code == 503


def test_candidate_failure_does_not_interrupt_chat(
    web_client, web_services, monkeypatch
):
    def fail_safely(user_message):
        raise RuntimeError("simulated candidate failure")

    monkeypatch.setattr(
        web_services.memory_confirmation,
        "propose_from_user_message",
        fail_safely,
    )
    stream = events(
        web_client.post(
            "/chat/stream",
            json={
                "question": "What temperature is a fever in adults?",
                "request_id": "candidate-failure",
            },
        )
    )

    assert stream[0]["memory_candidates"] == []
    assert stream[-1]["type"] == "final"


def test_regeneration_never_creates_a_second_memory_proposal(web_client):
    initial = events(
        web_client.post(
            "/chat/stream",
            json={
                "question": (
                    "I prefer concise answers. "
                    "What temperature is a fever in adults?"
                ),
                "request_id": "proposal-initial",
            },
        )
    )
    conversation_id = initial[0]["conversation"]["id"]
    assert len(initial[0]["memory_candidates"]) == 1

    regenerated = events(
        web_client.post(
            "/chat/stream",
            json={
                "conversation_id": conversation_id,
                "request_id": "proposal-regenerate",
                "regenerate": True,
            },
        )
    )

    assert regenerated[0]["memory_candidates"] == []


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


def test_message_feedback_set_toggle_and_clear(web_client):
    stream = events(
        web_client.post(
            "/chat/stream",
            json={"question": "What temperature is a fever in adults?", "request_id": "fb-1"},
        )
    )
    final = next(item for item in stream if item["type"] == "final")
    conversation_id = final["conversation"]["id"]
    message_id = final["message"]["id"]

    up = web_client.post(f"/conversations/{conversation_id}/messages/{message_id}/feedback", json={"feedback": "up"})
    assert up.status_code == 200
    assert up.json()["feedback"] == "up"

    down = web_client.post(f"/conversations/{conversation_id}/messages/{message_id}/feedback", json={"feedback": "down"})
    assert down.json()["feedback"] == "down"

    cleared = web_client.post(f"/conversations/{conversation_id}/messages/{message_id}/feedback", json={})
    assert cleared.json()["feedback"] is None

    # Persisted, not just returned in the response.
    saved = web_client.get(f"/conversations/{conversation_id}").json()
    assert saved["messages"][1]["feedback"] is None

    invalid = web_client.post(f"/conversations/{conversation_id}/messages/{message_id}/feedback", json={"feedback": "sideways"})
    assert invalid.status_code == 422

    missing = web_client.post(f"/conversations/{conversation_id}/messages/does-not-exist/feedback", json={"feedback": "up"})
    assert missing.status_code == 404

    user_message_id = saved["messages"][0]["id"]
    on_user_message = web_client.post(
        f"/conversations/{conversation_id}/messages/{user_message_id}/feedback", json={"feedback": "up"}
    )
    assert on_user_message.status_code == 404


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


def test_browser_upload_rejects_oversized_file(web_client, web_services):
    """Phase 57: the streaming size check aborts mid-upload rather than
    buffering the whole file first, and no partial file is left indexed."""
    from apex_ai.config.settings import with_overrides

    web_services.settings = with_overrides(web_services.settings, max_upload_mb=1)
    oversized = b"x" * (2 * 1024 * 1024)

    response = web_client.post(
        "/documents/upload",
        files={"file": ("too-big.txt", oversized, "text/plain")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"
    documents = {d["name"] for d in web_client.get("/documents").json()}
    assert "too-big.txt" not in documents


def test_browser_upload_sanitizes_a_path_traversal_filename(web_client):
    """Phase 57 end-to-end: a filename attempting to escape the uploads
    directory through the real HTTP API is sanitized, not honored."""
    content = (DATA_DIR / "burn_care.md").read_bytes()

    response = web_client.post(
        "/documents/upload",
        files={"file": ("../../../../etc/passwd.md", content, "text/markdown")},
    )

    assert response.status_code == 200
    documents = web_client.get("/documents").json()
    assert all("/" not in d["name"] and ".." not in d["name"] for d in documents)


def test_uploaded_documents_are_isolated_between_accounts(web_services):
    """Phase 55 end-to-end: a real second account, reached through the actual
    HTTP API (not a direct store call), must never see or retrieve-cite the
    default account's document, and vice versa."""
    from fastapi.testclient import TestClient

    from apex_ai.api.server import create_api
    from apex_ai.memory.conversations import ConversationStore

    conversations = ConversationStore(web_services.settings.conversation_db_path)
    conversations.backfill_owner(web_services.default_local_user.id)
    app = create_api(web_services, conversations=conversations)

    default_client = TestClient(app)  # no cookie -> auto-login default account
    other_client = TestClient(app)
    signup = other_client.post(
        "/auth/signup",
        json={"email": "second-account@example.test", "password": "correct horse battery"},
    )
    assert signup.status_code == 201

    content = (DATA_DIR / "burn_care.md").read_bytes()
    uploaded = other_client.post(
        "/documents/upload", files={"file": ("burn_care.md", content, "text/markdown")}
    )
    assert uploaded.status_code == 200

    default_docs = {d["name"] for d in default_client.get("/documents").json()}
    other_docs = {d["name"] for d in other_client.get("/documents").json()}
    assert default_docs == {"sample_first_aid.pdf"}
    assert other_docs == {"burn_care.md"}

    # A question only the other account's document can answer must come back
    # unsupported for the default account - never silently cited from a
    # document it cannot see.
    stream = events(
        default_client.post(
            "/chat/stream",
            json={"question": "How should burns be cooled?", "request_id": "cross-account"},
        )
    )
    final = next(item for item in stream if item["type"] == "final")
    assert all("burn_care" not in citation.get("source", "") for citation in final["citations"])


def test_collection_crud_via_the_api(web_client):
    created = web_client.post("/collections", json={"name": "Medical research"})
    assert created.status_code == 201
    collection = created.json()
    assert collection["name"] == "Medical research"

    listed = web_client.get("/collections").json()
    assert [c["id"] for c in listed] == [collection["id"]]

    renamed = web_client.patch(f"/collections/{collection['id']}", json={"name": "Renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"

    assert web_client.delete(f"/collections/{collection['id']}").json() == {"deleted": True}
    assert web_client.get("/collections").json() == []
    assert web_client.delete(f"/collections/{collection['id']}").status_code == 404


def test_upload_into_a_collection_and_filter_documents_by_it(web_client):
    collection = web_client.post("/collections", json={"name": "Burns"}).json()
    content = (DATA_DIR / "burn_care.md").read_bytes()

    uploaded = web_client.post(
        "/documents/upload",
        files={"file": ("burn_care.md", content, "text/markdown")},
        data={"collection_id": collection["id"]},
    )
    assert uploaded.status_code == 200

    scoped = web_client.get("/documents", params={"collection_id": collection["id"]}).json()
    assert {d["name"] for d in scoped} == {"burn_care.md"}
    unscoped = web_client.get("/documents").json()
    assert {d["name"] for d in unscoped} >= {"sample_first_aid.pdf", "burn_care.md"}


def test_upload_into_a_nonexistent_collection_is_rejected(web_client):
    content = (DATA_DIR / "burn_care.md").read_bytes()
    response = web_client.post(
        "/documents/upload",
        files={"file": ("burn_care.md", content, "text/markdown")},
        data={"collection_id": "does-not-exist"},
    )
    assert response.status_code == 404


def test_move_document_between_collections_via_the_api(web_client):
    collection = web_client.post("/collections", json={"name": "Work"}).json()
    documents = web_client.get("/documents").json()
    document_id = documents[0]["document_id"]

    moved = web_client.patch(
        f"/documents/{document_id}/collection", json={"collection_id": collection["id"]}
    )
    assert moved.status_code == 200
    assert moved.json()["collection_id"] == collection["id"]

    unassigned = web_client.patch(
        f"/documents/{document_id}/collection", json={"collection_id": None}
    )
    assert unassigned.json()["collection_id"] is None


def test_move_document_to_a_nonexistent_collection_is_rejected(web_client):
    documents = web_client.get("/documents").json()
    document_id = documents[0]["document_id"]

    response = web_client.patch(
        f"/documents/{document_id}/collection", json={"collection_id": "does-not-exist"}
    )
    assert response.status_code == 404


def test_moving_a_nonexistent_document_is_rejected(web_client):
    response = web_client.patch(
        "/documents/does-not-exist/collection", json={"collection_id": None}
    )
    assert response.status_code == 404


def test_deleting_a_collection_unassigns_its_documents_not_deletes_them(web_client):
    collection = web_client.post("/collections", json={"name": "Temp"}).json()
    documents = web_client.get("/documents").json()
    document_id = documents[0]["document_id"]
    web_client.patch(
        f"/documents/{document_id}/collection", json={"collection_id": collection["id"]}
    )

    assert web_client.delete(f"/collections/{collection['id']}").json() == {"deleted": True}

    remaining = web_client.get("/documents").json()
    assert any(d["document_id"] == document_id for d in remaining)  # document survives
    moved_back = next(d for d in remaining if d["document_id"] == document_id)
    assert moved_back["collection_id"] is None


def test_conversation_scoped_to_a_collection_only_retrieves_from_it(web_client):
    """Phase 67 end-to-end: a conversation created against one collection
    must answer from that collection's documents and refuse a question only
    a document outside the collection could answer."""
    collection = web_client.post("/collections", json={"name": "Burns only"}).json()
    content = (DATA_DIR / "burn_care.md").read_bytes()
    web_client.post(
        "/documents/upload",
        files={"file": ("burn_care.md", content, "text/markdown")},
        data={"collection_id": collection["id"]},
    )

    conversation = web_client.post(
        "/conversations", json={"collection_id": collection["id"]}
    ).json()
    assert conversation["collection_id"] == collection["id"]

    burns_answer = events(
        web_client.post(
            "/chat/stream",
            json={
                "question": "How should burns be cooled?",
                "conversation_id": conversation["id"],
                "request_id": "scoped-supported",
            },
        )
    )
    final = next(item for item in burns_answer if item["type"] == "final")
    assert not final["insufficient_evidence"]
    assert final["citations"]

    fever_answer = events(
        web_client.post(
            "/chat/stream",
            json={
                "question": "What temperature is a fever in adults?",
                "conversation_id": conversation["id"],
                "request_id": "scoped-unsupported",
            },
        )
    )
    fever_final = next(item for item in fever_answer if item["type"] == "final")
    assert fever_final["citations"] == []  # sample_first_aid.pdf is outside this collection


def test_conversation_collection_can_be_changed_after_creation(web_client):
    collection = web_client.post("/collections", json={"name": "Later"}).json()
    conversation = web_client.post("/conversations", json={}).json()
    assert conversation["collection_id"] is None

    updated = web_client.patch(
        f"/conversations/{conversation['id']}/collection",
        json={"collection_id": collection["id"]},
    )
    assert updated.status_code == 200
    assert updated.json()["collection_id"] == collection["id"]


def test_chat_stream_lazily_creates_a_collection_scoped_conversation(web_client):
    """A brand-new chat (no conversation_id yet) can pick its knowledge base
    on the very first message, not only after the conversation exists."""
    collection = web_client.post("/collections", json={"name": "First message scope"}).json()
    content = (DATA_DIR / "burn_care.md").read_bytes()
    web_client.post(
        "/documents/upload",
        files={"file": ("burn_care.md", content, "text/markdown")},
        data={"collection_id": collection["id"]},
    )

    stream = events(
        web_client.post(
            "/chat/stream",
            json={
                "question": "How should burns be cooled?",
                "collection_id": collection["id"],
                "request_id": "lazy-create-scoped",
            },
        )
    )
    assert stream[0]["conversation"]["collection_id"] == collection["id"]
    final = next(item for item in stream if item["type"] == "final")
    assert final["citations"]


def test_chat_stream_rejects_a_nonexistent_collection_on_lazy_create(web_client):
    response = web_client.post(
        "/chat/stream",
        json={
            "question": "Anything",
            "collection_id": "does-not-exist",
            "request_id": "lazy-create-bad-collection",
        },
    )
    assert response.status_code == 404


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
