"""Phase 60 dedicated security-regression pass.

Authentication, authorization, and user-isolation behavior already have
extensive coverage spread across tests/test_auth.py (Phase 51-53),
tests/test_*_memory*.py and tests/test_conversations_web.py (Phase 54/55),
tests/test_documents.py (Phase 57), and tests/test_api_security.py
(Phase 58). This file adds the handful of cross-cutting checks that don't
belong to any single phase: SQL injection, cookie hardening flags, and
malformed-input robustness at the authentication boundary.
"""

from __future__ import annotations

import re
from pathlib import Path

from apex_ai.memory.conversations import ConversationStore

USER = "user-1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SQLI_PAYLOADS = [
    "'; DROP TABLE conversations; --",
    "' OR '1'='1",
    "x'; DELETE FROM conversations WHERE '1'='1",
    "Robert'); DROP TABLE conversations;--",
]


def test_conversation_titles_and_search_treat_sql_payloads_as_literal_text(tmp_path):
    """A malicious title/search term must never be interpreted as SQL - the
    table must survive, and the payload must round-trip as ordinary text."""
    store = ConversationStore(tmp_path / "history.db")

    for payload in _SQLI_PAYLOADS:
        conversation = store.create(USER, title=payload)
        assert store.get(USER, conversation.id).title == payload

        found = store.list(USER, search=payload)
        assert any(c.id == conversation.id for c in found)

    # The table is intact: every conversation created above is still there,
    # and normal operations still work.
    assert len(store.list(USER)) == len(_SQLI_PAYLOADS)
    survivor = store.create(USER, title="ordinary title")
    assert store.get(USER, survivor.id) is not None


def test_message_content_with_sql_payloads_is_searchable_as_literal_text(tmp_path):
    store = ConversationStore(tmp_path / "history.db")
    conversation = store.create(USER)
    payload = "'; DROP TABLE messages; --"
    store.add_message(USER, conversation.id, "user", payload)

    found = store.list(USER, search=payload)
    assert any(c.id == conversation.id for c in found)
    assert len(store.messages(USER, conversation.id)) == 1


def test_session_cookie_is_httponly_and_samesite_lax(settings, ingestion, embeddings, store):
    """HttpOnly blocks JavaScript from reading the session token (XSS-driven
    theft); SameSite=Lax is the primary CSRF defense for every mutating
    route Phase 54 gated behind authentication."""
    from tests.test_api_security import _build_client

    client = _build_client(settings, ingestion, embeddings, store)

    response = client.post(
        "/auth/signup",
        json={"email": "cookie-check@example.test", "password": "correct horse battery"},
    )
    assert response.status_code == 201

    set_cookie_headers = response.headers.get_list("set-cookie")
    session_cookie = next(h for h in set_cookie_headers if h.startswith("apex_session="))
    assert re.search(r"(?i)\bhttponly\b", session_cookie)
    assert re.search(r"(?i)samesite=lax", session_cookie)


def test_garbage_session_cookie_is_rejected_not_crashed(settings, ingestion, embeddings, store):
    """An invalid/tampered cookie value must fail safely (fall back to
    auto-login or 401), never raise or leak an internal error."""
    from tests.test_api_security import _build_client

    client = _build_client(settings, ingestion, embeddings, store)
    client.cookies.set("apex_session", "'; DROP TABLE sessions; --")

    response = client.get("/auth/me")

    assert response.status_code in (200, 401)  # 200: auto-login fallback; never a 500


def test_escape_html_runs_before_markdown_transforms_in_the_chat_renderer():
    """Static regression guard for the client-side XSS defense: manually
    verified (see docs/PHASE60_SECURITY_TESTING.md) that renderMarkdown()
    escapes raw source text before any markdown-to-HTML transform runs, so
    literal `<script>`/`<img onerror>` text in an LLM answer or a citation
    can never reach innerHTML unescaped. This guards against a future edit
    silently removing that escaping call - it cannot verify runtime
    behavior, since this project has no JS execution harness (see the doc)."""
    app_js = PROJECT_ROOT / "apex_ai" / "web" / "static" / "app.js"
    source = app_js.read_text(encoding="utf-8")

    render_markdown = source[source.index("function renderMarkdown(") : source.index(
        "function toast("
    )]
    escape_call = render_markdown.index("text = escapeHTML(text);")
    first_html_transform = render_markdown.index('.replace(/\\[([^\\]]+)]')
    assert escape_call < first_html_transform, (
        "renderMarkdown must escape raw text before any markdown-to-HTML "
        "transform runs, or literal HTML in an LLM answer would be inserted "
        "unescaped via innerHTML"
    )
