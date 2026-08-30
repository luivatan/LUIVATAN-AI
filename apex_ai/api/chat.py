"""Conversation and streaming endpoints for the ChatGPT-style web client.

This is a thin controller around the existing RagEngine. It does not generate answers,
retrieve documents, or alter LLM behavior itself; it only selects conversation memory,
streams genuine engine events, and persists completed messages.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apex_ai.api.auth import make_require_user_dependency
from apex_ai.api.errors import (
    internal_error_problem,
    problem_from_apex,
    service_not_ready_error,
)
from apex_ai.api.schemas import (
    ConversationDetailOut,
    ConversationOut,
    DeletedOut,
    MessageOut,
    StopOut,
)
from apex_ai.core.errors import ApexError
from apex_ai.core.logging import get_logger
from apex_ai.memory.conversations import ConversationMemoryAdapter, ConversationStore
from apex_ai.memory.summarization import build_summary_messages, turns_needing_summary
from apex_ai.rag.engine import RagEngine

log = get_logger("api.chat")


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=100)
    collection_id: str | None = None
    project_id: str | None = None


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ConversationCollectionUpdate(BaseModel):
    collection_id: str | None = None


class ConversationProjectUpdate(BaseModel):
    project_id: str | None = None


class ChatStreamRequest(BaseModel):
    question: str = Field(default="", max_length=20000)
    conversation_id: str | None = None
    request_id: str | None = Field(default=None, max_length=100)
    regenerate: bool = False
    use_memory: bool = True
    # Only used when this request lazily creates a brand-new conversation
    # (no conversation_id given); an existing conversation's own stored
    # collection_id/project_id is always authoritative (see PATCH
    # .../collection and PATCH .../project).
    collection_id: str | None = None
    project_id: str | None = None


class StopRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)


class MessageFeedback(BaseModel):
    feedback: str | None = Field(default=None, pattern=r"^(up|down)$")


@dataclass
class ActiveGeneration:
    conversation_id: str
    stop: threading.Event


class GenerationManager:
    """Coordinates one active generation per conversation and supports stop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, ActiveGeneration] = {}

    def register(self, request_id: str, conversation_id: str) -> threading.Event:
        with self._lock:
            if request_id in self._active:
                raise ValueError("That generation request is already active.")
            if any(item.conversation_id == conversation_id for item in self._active.values()):
                raise ValueError("This conversation is already generating a response.")
            event = threading.Event()
            self._active[request_id] = ActiveGeneration(conversation_id, event)
            return event

    def request_stop(self, request_id: str) -> bool:
        with self._lock:
            active = self._active.get(request_id)
            if active is None:
                return False
            active.stop.set()
            return True

    def unregister(self, request_id: str) -> None:
        with self._lock:
            self._active.pop(request_id, None)


def _event(event_type: str, **payload) -> bytes:
    return (json.dumps({"type": event_type, **payload}, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _citation_payload(citation) -> dict:
    payload = citation.to_dict()
    # Citation.to_dict intentionally stays compact for the old API. The web source
    # viewer needs the exact retrieved text, so the controller adds it explicitly.
    payload["label"] = citation.label()
    payload["text"] = citation.text
    return payload


def _maybe_update_conversation_summary(
    services, conversations, user_id: str, conversation_id: str, llm_provider
) -> None:
    """Phase 50: opportunistically fold turns that just fell out of the live
    short-term window into the conversation's rolling summary.

    Off unless ``APEX_CONVERSATION_SUMMARY=1`` (extra LLM call — same latency
    tradeoff Phase 30's optional query rewriting already accepts by defaulting
    off). Runs after the assistant message is persisted, on the small fraction
    of turns where enough messages have accumulated since the last summary;
    most turns are a cheap no-op check. A failure here must never break the
    chat turn that triggered it — same optional-component boundary as memory
    candidate proposal just above.

    ``llm_provider`` is the exact provider this turn's answer was generated
    with (``engine.llm`` from ``_engine_for_conversation``) — not an
    independently resolved ``services.active_llm()``, which can differ (and
    can fail outright, e.g. in tests that wire a fake provider directly into
    the engine without a real model configured in settings).
    """
    if not services.settings.conversation_summary:
        return
    try:
        messages = conversations.messages(user_id, conversation_id)
        existing_summary, summarized_count = conversations.summary_state(user_id, conversation_id)
        # memory_turns is counted in turns; approximate the message-count
        # equivalent of "still live" as two messages (user + assistant) per turn.
        keep_live_messages = max(0, int(services.settings.memory_turns)) * 2
        pending = turns_needing_summary(
            messages,
            already_summarized_count=summarized_count,
            keep_live_messages=keep_live_messages,
        )
        if pending is None:
            return
        if not pending.turns_text:
            conversations.update_summary(
                user_id, conversation_id, existing_summary, pending.through_message_count
            )
            return
        summary_messages = build_summary_messages(existing_summary, pending.turns_text)
        new_summary = llm_provider.generate(
            messages=summary_messages, max_tokens=300, temperature=0.2
        )
        new_summary = (new_summary or "").strip()
        if new_summary:
            conversations.update_summary(
                user_id, conversation_id, new_summary, pending.through_message_count
            )
    except Exception as error:  # noqa: BLE001 - optional continuity boundary
        log.warning(
            "Conversation summarization failed; continuing without it (error_type=%s)",
            type(error).__name__,
        )


def _candidate_payload(candidate, confirmation_service, user_id: str) -> dict:
    """Phase 49: attach any detected conflict so the confirmation card can warn
    before the user approves, without changing what approval itself does."""
    conflict = confirmation_service.find_conflict(user_id, candidate)
    return {
        **candidate.to_dict(),
        "conflicts_with": conflict.to_dict() if conflict else None,
    }


def _resolve_scoping(
    services, user_id: str, conversation
) -> tuple[list[str] | None, str | None]:
    """Phase 71/72: a conversation in a project scopes retrieval to that
    project's collection and threads its instructions into the prompt,
    taking precedence over the conversation's own standalone collection_id
    (Phase 67) - a conversation not in a project keeps that pre-Phase-71
    behavior unchanged."""
    collection_id = ""
    project_instructions = None
    if conversation.project_id and services.projects is not None:
        project = services.projects.get(user_id, conversation.project_id)
        if project is not None:
            collection_id = project.collection_id
            project_instructions = project.instructions or None
    elif conversation.collection_id:
        collection_id = conversation.collection_id
    document_ids = (
        services.ingestion.document_ids_for_collection(user_id, collection_id)
        if collection_id and services.ingestion is not None
        else None
    )
    return document_ids, project_instructions


def _engine_for_conversation(services, memory, user_id: str) -> RagEngine:
    """Reuse every existing backend component, changing only the memory view."""
    base = services.engine
    return RagEngine(
        settings=services.settings,
        store=services.store,
        retriever=services.retriever,
        reranker=services.reranker,
        memory=memory,
        llm_provider=base.llm,
        query_processor=services.query_processor,
        medical_mode=base.medical_mode,
        long_term_memory=services.long_term_memory,
        user_id=user_id,
    )


def create_chat_router(
    services,
    conversations: ConversationStore,
    generations: GenerationManager | None = None,
) -> APIRouter:
    router = APIRouter(tags=["chat"])
    generations = generations or GenerationManager()
    require_user = make_require_user_dependency(services)

    @router.get("/conversations", response_model=list[ConversationOut])
    def list_conversations(
        search: str = "", project_id: str | None = None, user=Depends(require_user)
    ):
        return [
            item.to_dict()
            for item in conversations.list(user.id, search=search, project_id=project_id)
        ]

    @router.post("/conversations", status_code=201, response_model=ConversationOut)
    def create_conversation(payload: ConversationCreate, user=Depends(require_user)):
        collection_id = payload.collection_id or ""
        if collection_id and (
            services.collections is None or services.collections.get(user.id, collection_id) is None
        ):
            raise HTTPException(status_code=404, detail="Collection not found.")
        project_id = payload.project_id or ""
        if project_id and (
            services.projects is None or services.projects.get(user.id, project_id) is None
        ):
            raise HTTPException(status_code=404, detail="Project not found.")
        return conversations.create(user.id, payload.title, collection_id, project_id).to_dict()

    @router.patch("/conversations/{conversation_id}/collection", response_model=ConversationOut)
    def set_conversation_collection(
        conversation_id: str, payload: ConversationCollectionUpdate, user=Depends(require_user)
    ):
        """Scope (or unscope) this conversation's retrieval to one knowledge-
        base collection (Phase 67)."""
        collection_id = payload.collection_id or ""
        if collection_id and (
            services.collections is None or services.collections.get(user.id, collection_id) is None
        ):
            raise HTTPException(status_code=404, detail="Collection not found.")
        try:
            return conversations.set_collection(user.id, conversation_id, collection_id).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Conversation not found.") from error

    @router.patch("/conversations/{conversation_id}/project", response_model=ConversationOut)
    def set_conversation_project(
        conversation_id: str, payload: ConversationProjectUpdate, user=Depends(require_user)
    ):
        """Move this conversation into (or out of) a project (Phase 71)."""
        project_id = payload.project_id or ""
        if project_id and (
            services.projects is None or services.projects.get(user.id, project_id) is None
        ):
            raise HTTPException(status_code=404, detail="Project not found.")
        try:
            return conversations.set_project(user.id, conversation_id, project_id).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Conversation not found.") from error

    @router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
    def get_conversation(conversation_id: str, user=Depends(require_user)):
        conversation = conversations.get(user.id, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {
            **conversation.to_dict(),
            "messages": [
                message.to_dict() for message in conversations.messages(user.id, conversation_id)
            ],
        }

    @router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
    def rename_conversation(
        conversation_id: str, payload: ConversationRename, user=Depends(require_user)
    ):
        try:
            return conversations.rename(user.id, conversation_id, payload.title).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Conversation not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.delete("/conversations/{conversation_id}", response_model=DeletedOut)
    def delete_conversation(conversation_id: str, user=Depends(require_user)):
        if not conversations.delete(user.id, conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {"deleted": True}

    @router.post(
        "/conversations/{conversation_id}/messages/{message_id}/feedback",
        response_model=MessageOut,
    )
    def set_message_feedback(
        conversation_id: str,
        message_id: str,
        payload: MessageFeedback,
        user=Depends(require_user),
    ):
        """Local up/down reaction to one assistant message (Phase 17 response
        action). Not aggregated, not sent anywhere, not used by generation."""
        try:
            return conversations.set_feedback(
                user.id, conversation_id, message_id, payload.feedback
            ).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Message not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/chat/stop", response_model=StopOut)
    def stop_generation(payload: StopRequest, user=Depends(require_user)):
        return {"stopping": generations.request_stop(payload.request_id)}

    @router.post("/chat/stream")
    def stream_chat(payload: ChatStreamRequest, user=Depends(require_user)):
        if not services.ready:
            raise service_not_ready_error()

        request_id = payload.request_id or str(uuid.uuid4())
        conversation = (
            conversations.get(user.id, payload.conversation_id)
            if payload.conversation_id
            else None
        )
        if payload.conversation_id and conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        if conversation is None:
            new_collection_id = payload.collection_id or ""
            if new_collection_id and (
                services.collections is None
                or services.collections.get(user.id, new_collection_id) is None
            ):
                raise HTTPException(status_code=404, detail="Collection not found.")
            new_project_id = payload.project_id or ""
            if new_project_id and (
                services.projects is None or services.projects.get(user.id, new_project_id) is None
            ):
                raise HTTPException(status_code=404, detail="Project not found.")
            conversation = conversations.create(
                user.id, collection_id=new_collection_id, project_id=new_project_id
            )

        # Phase 67/71: a conversation scoped to a knowledge-base collection
        # (directly, or via a project) restricts retrieval to that
        # collection's documents only; a project's instructions (Phase 72)
        # get woven into the prompt.
        document_ids, project_instructions = _resolve_scoping(services, user.id, conversation)

        pending_user = None
        if payload.regenerate:
            pending_user = conversations.last_user_message(user.id, conversation.id)
            if pending_user is None:
                raise HTTPException(
                    status_code=400, detail="There is no user message to regenerate."
                )
            question = pending_user.content
        else:
            question = payload.question.strip()
            if not question:
                raise HTTPException(status_code=422, detail="Type a message first.")

        # Reserve the conversation before persisting a new user message. A rejected
        # concurrent request must not leave an orphan question in history.
        try:
            stop_event = generations.register(request_id, conversation.id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if pending_user is None:
            try:
                pending_user = conversations.add_message(user.id, conversation.id, "user", question)
            except Exception:
                generations.unregister(request_id)
                raise

        memory_candidates: list[dict] = []
        if not payload.regenerate and services.memory_confirmation is not None:
            try:
                memory_candidates = [
                    _candidate_payload(item, services.memory_confirmation, user.id)
                    for item in services.memory_confirmation.propose_from_user_message(
                        user.id, question
                    )
                ]
            except Exception as error:  # noqa: BLE001 - optional memory boundary
                log.warning(
                    "Memory candidate preparation failed; chat will continue (%s)",
                    type(error).__name__,
                )

        def generate() -> Iterator[bytes]:
            parts: list[str] = []
            persisted = False
            iterator = None
            try:
                # Keep the first yield inside the try/finally so an immediate browser
                # disconnect cannot leak the active-generation reservation.
                yield _event(
                    "meta",
                    request_id=request_id,
                    conversation=conversations.get(user.id, conversation.id).to_dict(),
                    user_message=pending_user.to_dict(),
                    regenerate=payload.regenerate,
                    memory_candidates=memory_candidates,
                )
                memory = ConversationMemoryAdapter(
                    conversations,
                    user.id,
                    conversation.id,
                    services.settings.memory_turns,
                    exclude_user_message_id=pending_user.id,
                )
                engine = _engine_for_conversation(services, memory, user.id)
                iterator = engine.ask_stream(
                    question,
                    use_memory=payload.use_memory,
                    document_ids=document_ids,
                    project_instructions=project_instructions,
                )
                for engine_event in iterator:
                    if stop_event.is_set():
                        if hasattr(iterator, "close"):
                            iterator.close()
                        break
                    if engine_event["type"] == "token":
                        token = engine_event["text"]
                        parts.append(token)
                        yield _event("token", text=token)
                        continue

                    result = engine_event["result"]
                    answer = result.answer
                    citations = [_citation_payload(item) for item in result.citations]
                    if payload.regenerate:
                        conversations.remove_answers_after(user.id, pending_user)
                    message = conversations.add_message(
                        user.id, conversation.id, "assistant", answer, citations=citations
                    )
                    persisted = True
                    _maybe_update_conversation_summary(
                        services, conversations, user.id, conversation.id, engine.llm
                    )
                    yield _event(
                        "final",
                        message=message.to_dict(),
                        citations=citations,
                        confidence=result.confidence,
                        insufficient_evidence=result.insufficient_evidence,
                        queries_used=result.queries_used,
                        timings=result.timings,
                        conversation=conversations.get(user.id, conversation.id).to_dict(),
                    )

                if stop_event.is_set():
                    partial = "".join(parts).strip()
                    message_payload = None
                    if partial:
                        if payload.regenerate:
                            conversations.remove_answers_after(user.id, pending_user)
                        message = conversations.add_message(
                            user.id, conversation.id, "assistant", partial, status="stopped"
                        )
                        message_payload = message.to_dict()
                        persisted = True
                    yield _event("stopped", message=message_payload)
            except GeneratorExit:
                raise
            except ApexError as error:
                log.warning("Chat request failed: %s", error.title)
                problem = problem_from_apex(error)
                yield _event("error", message=problem["message"], error=problem)
            except Exception:
                log.exception("Unexpected streaming chat failure")
                problem = internal_error_problem()
                yield _event("error", message=problem["message"], error=problem)
            finally:
                # A disconnected browser should not make partial generated text look
                # complete in history. Explicit Stop uses the branch above.
                if not persisted and parts and not payload.regenerate:
                    try:
                        conversations.add_message(
                            user.id,
                            conversation.id,
                            "assistant",
                            "".join(parts).strip(),
                            status="stopped",
                        )
                    except Exception:
                        log.exception("Could not persist interrupted partial response")
                generations.unregister(request_id)

        return StreamingResponse(
            generate(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # Exposed for deterministic controller tests, not as a global singleton.
    router.generation_manager = generations  # type: ignore[attr-defined]
    return router


__all__ = ["GenerationManager", "create_chat_router"]
