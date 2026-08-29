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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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
from apex_ai.rag.engine import RagEngine

log = get_logger("api.chat")


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=100)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ChatStreamRequest(BaseModel):
    question: str = Field(default="", max_length=20000)
    conversation_id: str | None = None
    request_id: str | None = Field(default=None, max_length=100)
    regenerate: bool = False
    use_memory: bool = True


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


def _engine_for_conversation(services, memory) -> RagEngine:
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
    )


def create_chat_router(
    services,
    conversations: ConversationStore,
    generations: GenerationManager | None = None,
) -> APIRouter:
    router = APIRouter(tags=["chat"])
    generations = generations or GenerationManager()

    @router.get("/conversations", response_model=list[ConversationOut])
    def list_conversations(search: str = ""):
        return [item.to_dict() for item in conversations.list(search=search)]

    @router.post("/conversations", status_code=201, response_model=ConversationOut)
    def create_conversation(payload: ConversationCreate):
        return conversations.create(payload.title).to_dict()

    @router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
    def get_conversation(conversation_id: str):
        conversation = conversations.get(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {
            **conversation.to_dict(),
            "messages": [message.to_dict() for message in conversations.messages(conversation_id)],
        }

    @router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
    def rename_conversation(conversation_id: str, payload: ConversationRename):
        try:
            return conversations.rename(conversation_id, payload.title).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Conversation not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.delete("/conversations/{conversation_id}", response_model=DeletedOut)
    def delete_conversation(conversation_id: str):
        if not conversations.delete(conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {"deleted": True}

    @router.post(
        "/conversations/{conversation_id}/messages/{message_id}/feedback",
        response_model=MessageOut,
    )
    def set_message_feedback(conversation_id: str, message_id: str, payload: MessageFeedback):
        """Local up/down reaction to one assistant message (Phase 17 response
        action). Not aggregated, not sent anywhere, not used by generation."""
        try:
            return conversations.set_feedback(
                conversation_id, message_id, payload.feedback
            ).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Message not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/chat/stop", response_model=StopOut)
    def stop_generation(payload: StopRequest):
        return {"stopping": generations.request_stop(payload.request_id)}

    @router.post("/chat/stream")
    def stream_chat(payload: ChatStreamRequest):
        if not services.ready:
            raise service_not_ready_error()

        request_id = payload.request_id or str(uuid.uuid4())
        conversation = (
            conversations.get(payload.conversation_id) if payload.conversation_id else None
        )
        if payload.conversation_id and conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        if conversation is None:
            conversation = conversations.create()

        pending_user = None
        if payload.regenerate:
            pending_user = conversations.last_user_message(conversation.id)
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
                pending_user = conversations.add_message(conversation.id, "user", question)
            except Exception:
                generations.unregister(request_id)
                raise

        memory_candidates: list[dict] = []
        if not payload.regenerate and services.memory_confirmation is not None:
            try:
                memory_candidates = [
                    item.to_dict()
                    for item in services.memory_confirmation.propose_from_user_message(
                        question
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
                    conversation=conversations.get(conversation.id).to_dict(),
                    user_message=pending_user.to_dict(),
                    regenerate=payload.regenerate,
                    memory_candidates=memory_candidates,
                )
                memory = ConversationMemoryAdapter(
                    conversations,
                    conversation.id,
                    services.settings.memory_turns,
                    exclude_user_message_id=pending_user.id,
                )
                engine = _engine_for_conversation(services, memory)
                iterator = engine.ask_stream(question, use_memory=payload.use_memory)
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
                        conversations.remove_answers_after(pending_user)
                    message = conversations.add_message(
                        conversation.id, "assistant", answer, citations=citations
                    )
                    persisted = True
                    yield _event(
                        "final",
                        message=message.to_dict(),
                        citations=citations,
                        confidence=result.confidence,
                        insufficient_evidence=result.insufficient_evidence,
                        queries_used=result.queries_used,
                        timings=result.timings,
                        conversation=conversations.get(conversation.id).to_dict(),
                    )

                if stop_event.is_set():
                    partial = "".join(parts).strip()
                    message_payload = None
                    if partial:
                        if payload.regenerate:
                            conversations.remove_answers_after(pending_user)
                        message = conversations.add_message(
                            conversation.id, "assistant", partial, status="stopped"
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
