"""Phase 58 API security: rate limiting and CORS configuration."""

from __future__ import annotations

from tests.conftest import DATA_DIR, FakeLLM


def _build_client(settings, ingestion, embeddings, store):
    from fastapi.testclient import TestClient

    from apex_ai.api.server import create_api
    from apex_ai.auth.service import AuthService
    from apex_ai.auth.sessions import SessionStore
    from apex_ai.auth.users import UserStore
    from apex_ai.memory.conversation import ConversationMemory
    from apex_ai.models.manager import ModelManager
    from apex_ai.rag.engine import RagEngine
    from apex_ai.rag.query_processing import QueryProcessor
    from apex_ai.retrieval.keyword import BM25Index
    from apex_ai.retrieval.pipeline import HybridRetriever
    from apex_ai.retrieval.reranker import LexicalReranker
    from apex_ai.runtime import ApexServices

    ingestion.ingest_path(DATA_DIR / "sample_first_aid.pdf", "user-1")
    retriever = HybridRetriever(store, settings, BM25Index(store))
    memory = ConversationMemory(settings.memory_path, settings.memory_turns)
    auth = AuthService(
        UserStore(settings.users_db_path),
        SessionStore(settings.users_db_path),
        session_ttl_days=settings.session_ttl_days,
    )
    default_local_user = auth.ensure_default_local_account()
    engine = RagEngine(
        settings=settings, store=store, retriever=retriever,
        reranker=LexicalReranker(), memory=memory, llm_provider=FakeLLM(),
        query_processor=QueryProcessor(enabled=False),
        user_id=default_local_user.id,
    )
    services = ApexServices(
        settings=settings, embeddings=embeddings, store=store, ingestion=ingestion,
        retriever=retriever, reranker=LexicalReranker(), memory=memory,
        query_processor=QueryProcessor(enabled=False), engine=engine,
        models=ModelManager(settings),
        auth=auth, default_local_user=default_local_user,
    )
    return TestClient(create_api(services))


def test_general_rate_limit_returns_429_with_retry_after(settings, ingestion, embeddings, store):
    from apex_ai.config.settings import with_overrides

    limited = with_overrides(
        settings, rate_limit_enabled=True, rate_limit_requests_per_minute=3
    )
    client = _build_client(limited, ingestion, embeddings, store)

    for _ in range(3):
        assert client.get("/health").status_code in (200, 503)

    blocked = client.get("/health")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.json()["error"]["retryable"] is True
    assert "Retry-After" in blocked.headers


def test_auth_routes_have_a_stricter_limit_than_general_traffic(
    settings, ingestion, embeddings, store
):
    """The strict /auth/login budget must bite before the much larger general
    budget would, even though both count against the same client."""
    from apex_ai.config.settings import with_overrides

    limited = with_overrides(
        settings,
        rate_limit_enabled=True,
        rate_limit_requests_per_minute=1000,
        auth_rate_limit_requests_per_minute=2,
    )
    client = _build_client(limited, ingestion, embeddings, store)
    login_payload = {"email": "nobody@example.test", "password": "wrong-password"}

    for _ in range(2):
        response = client.post("/auth/login", json=login_payload)
        assert response.status_code == 401  # under the limit: real auth failure

    blocked = client.post("/auth/login", json=login_payload)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


def test_static_assets_are_exempt_from_rate_limiting(settings, ingestion, embeddings, store):
    from apex_ai.config.settings import with_overrides

    limited = with_overrides(
        settings, rate_limit_enabled=True, rate_limit_requests_per_minute=1
    )
    client = _build_client(limited, ingestion, embeddings, store)

    assert client.get("/health").status_code in (200, 503)
    assert client.get("/health").status_code == 429  # general budget now spent

    # Static assets are unaffected by the exhausted general budget.
    for _ in range(3):
        assert client.get("/assets/app.css").status_code == 200


def test_rate_limiting_disabled_allows_unlimited_requests(settings, ingestion, embeddings, store):
    from apex_ai.config.settings import with_overrides

    disabled = with_overrides(settings, rate_limit_enabled=False)
    client = _build_client(disabled, ingestion, embeddings, store)

    for _ in range(10):
        assert client.get("/health").status_code in (200, 503)


def test_cors_header_absent_by_default(settings, ingestion, embeddings, store):
    """No CORSMiddleware installed at all when unconfigured (Phase 58's
    secure default: same-origin only, same as before this phase existed)."""
    client = _build_client(settings, ingestion, embeddings, store)

    response = client.get("/health", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_cors_header_reflects_configured_allowed_origin(settings, ingestion, embeddings, store):
    from apex_ai.config.settings import with_overrides

    configured = with_overrides(
        settings, cors_allowed_origins="https://app.example.test, https://other.example.test"
    )
    client = _build_client(configured, ingestion, embeddings, store)

    allowed = client.get("/health", headers={"Origin": "https://app.example.test"})
    assert allowed.headers.get("access-control-allow-origin") == "https://app.example.test"

    disallowed = client.get("/health", headers={"Origin": "https://not-allowed.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in disallowed.headers}
