"""API + UI behavior tests (TestClient, no real server, no real models)."""

from __future__ import annotations

import pytest

from tests.conftest import DATA_DIR, FakeLLM


@pytest.fixture()
def wired_services(settings, ingestion, embeddings, store):
    """A services container wired to the fast fake stack."""
    from apex_ai.auth.service import AuthService
    from apex_ai.auth.sessions import SessionStore
    from apex_ai.auth.users import UserStore
    from apex_ai.billing import EntitlementService, SubscriptionStore, UsageStore
    from apex_ai.documents.collections import CollectionStore
    from apex_ai.memory.conversation import ConversationMemory
    from apex_ai.models.manager import ModelManager
    from apex_ai.models.router import ModelRouter
    from apex_ai.projects.store import ProjectStore
    from apex_ai.rag.engine import RagEngine
    from apex_ai.rag.query_processing import QueryProcessor
    from apex_ai.retrieval.keyword import BM25Index
    from apex_ai.retrieval.pipeline import HybridRetriever
    from apex_ai.retrieval.reranker import LexicalReranker
    from apex_ai.runtime import ApexServices

    retriever = HybridRetriever(store, settings, BM25Index(store))
    memory = ConversationMemory(settings.memory_path, settings.memory_turns)
    auth = AuthService(
        UserStore(settings.users_db_path),
        SessionStore(settings.users_db_path),
        session_ttl_days=settings.session_ttl_days,
    )
    default_local_user = auth.ensure_default_local_account()
    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", default_local_user.id)
    engine = RagEngine(
        settings=settings, store=store, retriever=retriever,
        reranker=LexicalReranker(), memory=memory, llm_provider=FakeLLM(),
        query_processor=QueryProcessor(enabled=False),
        user_id=default_local_user.id,
    )
    manager = ModelManager(settings)
    subscriptions = SubscriptionStore(settings.billing_db_path)
    usage = UsageStore(settings.billing_db_path)
    return ApexServices(
        settings=settings, embeddings=embeddings, store=store, ingestion=ingestion,
        retriever=retriever, reranker=LexicalReranker(), memory=memory,
        query_processor=QueryProcessor(enabled=False), engine=engine,
        models=manager,
        model_router=ModelRouter(manager, max_fast_model_mb=settings.max_fast_model_mb),
        subscriptions=subscriptions, usage=usage,
        entitlements=EntitlementService(subscriptions, usage),
        collections=CollectionStore(settings.collections_db_path),
        projects=ProjectStore(settings.projects_db_path),
        auth=auth, default_local_user=default_local_user,
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
    wired_services.long_term_memory.create(
        wired_services.default_local_user.id, canary, kind="preference"
    )

    response = api_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["long_term_memory"]["status"] == "ready"
    # Phase 47: reflects the real setting (default on) once a store exists,
    # not the hardcoded False this field held before that phase.
    assert payload["long_term_memory"]["prompt_use"] is True
    assert canary not in response.text


def test_health_omits_stats_when_not_ready(settings):
    """Phase 7: response_model_exclude_none must not turn absent fields into nulls.
    Phase 8: an unready health check must report it via the status code too."""
    from fastapi.testclient import TestClient

    from apex_ai.api.server import create_api
    from apex_ai.runtime import ApexServices

    broken = ApexServices(settings=settings, startup_error="MODEL NOT FOUND")
    client = TestClient(create_api(broken, include_web=False))

    response = client.get("/health")
    assert response.status_code == 503
    payload = response.json()
    assert payload["ready"] is False
    assert payload["startup_error"] == "MODEL NOT FOUND"
    assert payload["database"] == {"status": "unavailable", "detail": "not_initialized"}
    assert "documents" not in payload
    assert "chunks" not in payload


def test_health_reports_database_probe_failure(api_client, wired_services):
    """Phase 8: /health re-checks the store live instead of trusting startup state."""

    def _broken_count():
        raise RuntimeError("simulated database failure")

    wired_services.store.count = _broken_count

    response = api_client.get("/health")
    assert response.status_code == 503
    payload = response.json()
    assert payload["ready"] is False
    assert payload["database"] == {"status": "unavailable", "detail": "RuntimeError"}


def test_health_reports_llm_configuration_without_claiming_connectivity(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
    llm = response.json()["llm"]
    assert llm["provider"]
    assert isinstance(llm["configured"], bool)
    assert "connectivity is verified when a question is asked" in llm["note"]


def test_openapi_documents_response_schemas(api_client):
    """Phase 7 (API Structure): routes publish real response schemas, not bare dicts."""
    schema = api_client.get("/openapi.json").json()
    conversations_get = schema["paths"]["/conversations"]["get"]
    response_schema = conversations_get["responses"]["200"]["content"]["application/json"]["schema"]
    assert "ConversationOut" in str(response_schema)
    assert "ConversationOut" in schema["components"]["schemas"]
    assert "HealthOut" in schema["components"]["schemas"]


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


def test_phase42_query_endpoint_does_not_trigger_memory_extraction(
    api_client, wired_services
):
    """The compatibility /query route must not silently create new memory
    candidates or confirmed records from an ordinary factual question."""
    from apex_ai.memory.long_term import LongTermMemoryStore

    private_memory = "PRIVATE-LONG-TERM-MEMORY-CANARY"
    wired_services.long_term_memory = LongTermMemoryStore(
        wired_services.settings.long_term_memory_db_path
    )
    wired_services.long_term_memory.create(
        wired_services.default_local_user.id, private_memory, kind="preference"
    )

    response = api_client.post(
        "/query", json={"question": "What temperature is a fever in adults?"}
    )

    assert response.status_code == 200
    assert wired_services.long_term_memory.count(wired_services.default_local_user.id) == 1


def test_phase47_preference_always_reaches_prompt_but_never_becomes_a_citation(
    wired_services,
):
    """A confirmed *preference* applies to every question (Phase 47 design:
    preferences describe HOW to answer, not a topic), so it's expected in the
    prompt here — but it must never be attached to citations, which stay
    document-only regardless of what memory contains."""
    from apex_ai.memory.long_term import LongTermMemoryStore
    from apex_ai.rag.engine import RagEngine

    private_preference = "PRIVATE-PREFERENCE-CANARY: prefers concise answers"
    long_term_memory = LongTermMemoryStore(wired_services.settings.long_term_memory_db_path)
    long_term_memory.create(
        wired_services.default_local_user.id, private_preference, kind="preference"
    )

    engine = RagEngine(
        settings=wired_services.settings,
        store=wired_services.store,
        retriever=wired_services.retriever,
        reranker=wired_services.reranker,
        memory=wired_services.memory,
        llm_provider=FakeLLM(),
        query_processor=wired_services.query_processor,
        long_term_memory=long_term_memory,
        user_id=wired_services.default_local_user.id,
    )

    result = engine.ask("What temperature is a fever in adults?")

    assert private_preference in repr(FakeLLM.last_messages)
    assert "User context" in repr(FakeLLM.last_messages)
    assert result.citations
    assert all(private_preference not in citation.text for citation in result.citations)


def test_phase47_unrelated_ongoing_context_is_filtered_out_by_relevance(wired_services):
    """Unlike preferences, ongoing_context is topic-scoped: a stored note about
    an unrelated task must not leak into a question that shares no keywords
    with it (this is what relevance filtering exists to prevent)."""
    from apex_ai.memory.long_term import LongTermMemoryStore
    from apex_ai.rag.engine import RagEngine

    unrelated_context = "PRIVATE-CONTEXT-CANARY: migrating a payroll spreadsheet to a new vendor"
    long_term_memory = LongTermMemoryStore(wired_services.settings.long_term_memory_db_path)
    long_term_memory.create(
        wired_services.default_local_user.id, unrelated_context, kind="ongoing_context"
    )

    engine = RagEngine(
        settings=wired_services.settings,
        store=wired_services.store,
        retriever=wired_services.retriever,
        reranker=wired_services.reranker,
        memory=wired_services.memory,
        llm_provider=FakeLLM(),
        query_processor=wired_services.query_processor,
        long_term_memory=long_term_memory,
        user_id=wired_services.default_local_user.id,
    )

    engine.ask("What temperature is a fever in adults?")

    assert unrelated_context not in repr(FakeLLM.last_messages)


def test_phase47_memory_prompt_use_setting_disables_injection(wired_services, settings):
    from apex_ai.config.settings import with_overrides
    from apex_ai.memory.long_term import LongTermMemoryStore
    from apex_ai.rag.engine import RagEngine

    preference = "PRIVATE-DISABLED-CANARY: prefers concise answers"
    long_term_memory = LongTermMemoryStore(wired_services.settings.long_term_memory_db_path)
    long_term_memory.create(
        wired_services.default_local_user.id, preference, kind="preference"
    )
    disabled_settings = with_overrides(settings, memory_prompt_use=False)

    engine = RagEngine(
        settings=disabled_settings,
        store=wired_services.store,
        retriever=wired_services.retriever,
        reranker=wired_services.reranker,
        memory=wired_services.memory,
        llm_provider=FakeLLM(),
        query_processor=wired_services.query_processor,
        long_term_memory=long_term_memory,
        user_id=wired_services.default_local_user.id,
    )

    engine.ask("What temperature is a fever in adults?")

    assert preference not in repr(FakeLLM.last_messages)


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
    assert wired_services.long_term_memory.count(wired_services.default_local_user.id) == 0


def test_ingest_endpoint(api_client, tmp_path):
    response = api_client.post(
        "/documents/ingest", json={"path": str(DATA_DIR / "burn_care.md")}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "indexed"


def test_models_endpoint_lists_discovered(api_client):
    assert api_client.get("/models").status_code == 200


def test_recommended_model_endpoint_reports_no_model_when_none_are_discovered(api_client):
    """settings.model_dir has no real GGUF files in this fixture - a real,
    honest "nothing to recommend" answer, not a fabricated one."""
    response = api_client.get("/models/recommended")
    assert response.status_code == 200
    payload = response.json()
    assert payload["task"] == "chat"
    assert payload["model"] is None
    assert "No loadable model" in payload["reason"]


def test_recommended_model_endpoint_picks_the_largest_model_for_chat(api_client, wired_services):
    model_dir = wired_services.settings.model_dir
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "small.gguf").write_bytes(b"GGUF" + b"\x00" * 100)
    (model_dir / "large.gguf").write_bytes(b"GGUF" + b"\x00" * 10_000)

    response = api_client.get("/models/recommended", params={"task": "chat"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["model"]["name"] == "large.gguf"


def test_recommended_model_endpoint_picks_the_smallest_model_for_fast(api_client, wired_services):
    model_dir = wired_services.settings.model_dir
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "small.gguf").write_bytes(b"GGUF" + b"\x00" * 100)
    (model_dir / "large.gguf").write_bytes(b"GGUF" + b"\x00" * 10_000)

    response = api_client.get("/models/recommended", params={"task": "fast"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["model"]["name"] == "small.gguf"


def test_recommended_model_endpoint_rejects_an_unknown_task(api_client):
    response = api_client.get("/models/recommended", params={"task": "does-not-exist"})
    assert response.status_code == 422


def test_billing_plans_endpoint_lists_every_tier_cheapest_first(api_client):
    response = api_client.get("/billing/plans")
    assert response.status_code == 200
    payload = response.json()
    assert [plan["id"] for plan in payload] == ["free", "pro", "business"]
    assert payload[0]["price_cents"] == 0


def test_billing_plan_endpoint_defaults_to_free_for_a_new_account(api_client):
    response = api_client.get("/billing/plan")
    assert response.status_code == 200
    payload = response.json()
    assert payload["plan"]["id"] == "free"
    assert payload["status"] == "active"


def test_billing_plan_endpoint_reflects_an_administratively_set_plan(
    api_client, wired_services
):
    wired_services.subscriptions.set_plan(wired_services.default_local_user.id, "pro")
    response = api_client.get("/billing/plan")
    assert response.status_code == 200
    assert response.json()["plan"]["id"] == "pro"


# ---------------- Phase 87-88: entitlement enforcement + usage tracking ----------------


def _events(response):
    import json

    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_creating_a_collection_beyond_the_plan_limit_is_rejected(api_client, wired_services):
    for index in range(3):  # the free plan's max_collections
        assert api_client.post("/collections", json={"name": f"Collection {index}"}).status_code == 201

    response = api_client.post("/collections", json={"name": "One too many"})
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "plan_limit_exceeded"


def test_creating_a_project_beyond_the_plan_limit_is_rejected(api_client, wired_services):
    assert api_client.post("/projects", json={"name": "First"}).status_code == 201
    response = api_client.post("/projects", json={"name": "One too many"})  # free plan: 1 max
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "plan_limit_exceeded"


def test_upgrading_the_plan_lifts_a_previously_hit_collection_limit(api_client, wired_services):
    for index in range(3):
        api_client.post("/collections", json={"name": f"Collection {index}"})
    blocked = api_client.post("/collections", json={"name": "Blocked"})
    assert blocked.status_code == 402

    wired_services.subscriptions.set_plan(wired_services.default_local_user.id, "pro")

    allowed = api_client.post("/collections", json={"name": "Now allowed"})
    assert allowed.status_code == 201


def test_uploading_a_document_beyond_the_plan_document_limit_is_rejected(
    api_client, wired_services, monkeypatch
):
    """wired_services already ingests one document during fixture setup;
    monkeypatching a 1-document ceiling onto the free plan avoids uploading
    20 real files just to reach the real default limit."""
    from apex_ai.billing.plans import PLANS, Plan, PlanLimits

    tiny_free = Plan(
        id="free", name="Free", price_cents=0,
        limits=PlanLimits(
            max_documents=1, max_storage_mb=1000, max_collections=10,
            max_projects=10, max_messages_per_month=1000, max_tool_calls_per_month=1000,
        ),
    )
    monkeypatch.setitem(PLANS, "free", tiny_free)

    content = (DATA_DIR / "burn_care.md").read_bytes()
    response = api_client.post(
        "/documents/upload", files={"file": ("burn_care.md", content, "text/markdown")}
    )
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "plan_limit_exceeded"


def test_chat_stream_rejects_a_new_message_beyond_the_plan_message_limit(
    api_client, wired_services, monkeypatch
):
    from apex_ai.billing.plans import PLANS, Plan, PlanLimits

    no_messages = Plan(
        id="free", name="Free", price_cents=0,
        limits=PlanLimits(
            max_documents=1000, max_storage_mb=1000, max_collections=100,
            max_projects=100, max_messages_per_month=0, max_tool_calls_per_month=1000,
        ),
    )
    monkeypatch.setitem(PLANS, "free", no_messages)

    response = api_client.post(
        "/chat/stream", json={"question": "hello", "request_id": "rate-limited"}
    )
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "plan_limit_exceeded"


def test_chat_stream_records_message_usage_on_a_new_message(api_client, wired_services):
    user_id = wired_services.default_local_user.id
    assert wired_services.usage.total_this_month(user_id, "messages") == 0

    response = api_client.post(
        "/chat/stream",
        json={"question": "What temperature is a fever in adults?", "request_id": "usage-1"},
    )
    final = next(item for item in _events(response) if item["type"] == "final")
    assert final is not None
    assert wired_services.usage.total_this_month(user_id, "messages") == 1


def test_chat_stream_regenerate_does_not_double_count_message_usage(api_client, wired_services):
    user_id = wired_services.default_local_user.id
    first = api_client.post(
        "/chat/stream",
        json={"question": "What temperature is a fever in adults?", "request_id": "usage-a"},
    )
    conversation_id = next(item for item in _events(first) if item["type"] == "meta")[
        "conversation"
    ]["id"]
    assert wired_services.usage.total_this_month(user_id, "messages") == 1

    api_client.post(
        "/chat/stream",
        json={"conversation_id": conversation_id, "regenerate": True, "request_id": "usage-b"},
    )
    assert wired_services.usage.total_this_month(user_id, "messages") == 1


def test_billing_usage_endpoint_reports_real_counts(api_client, wired_services):
    api_client.post("/collections", json={"name": "One"})
    response = api_client.get("/billing/usage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["subscription"]["plan"]["id"] == "free"
    by_resource = {item["resource"]: item for item in payload["entitlements"]}
    assert by_resource["collections"]["used"] == 1
    assert by_resource["documents"]["used"] == 1  # ingested during fixture setup
    assert "messages" in by_resource
    assert "tool_calls" in by_resource


def test_memory_confirmation_endpoint_degrades_when_optional_store_is_unavailable(
    api_client,
):
    response = api_client.get("/memory/candidates")
    assert response.status_code == 503
    assert "Core chat remains available" in response.json()["detail"]


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
