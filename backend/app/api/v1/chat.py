"""Conversation CRUD + SSE streaming chat endpoints (Phase 4)."""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DBSession
from app.schemas.chat import (
    ChatRequest,
    ConversationCreate,
    ConversationDetailRead,
    ConversationRead,
    ConversationRename,
)
from app.services.chat_service import ChatService, ChatStreamEvent

router = APIRouter(prefix="/conversations", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Disable nginx buffering so deltas reach the browser as they are produced.
    "X-Accel-Buffering": "no",
}


@router.get(
    "",
    response_model=list[ConversationRead],
    summary="List the caller's conversations (newest first)",
)
def list_conversations(
    db: DBSession, current_user: CurrentUser
) -> list[ConversationRead]:
    return ChatService(db).list_conversations(current_user.id)


@router.post(
    "",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation, optionally bound to a knowledge base",
)
def create_conversation(
    payload: ConversationCreate, db: DBSession, current_user: CurrentUser
) -> ConversationRead:
    return ChatService(db).create_conversation(
        current_user.id,
        title=payload.title,
        knowledge_base_id=payload.knowledge_base_id,
        agent_id=payload.agent_id,
    )


@router.get(
    "/{conv_id}",
    response_model=ConversationDetailRead,
    summary="Fetch one conversation with its full message history",
)
def get_conversation(
    conv_id: int, db: DBSession, current_user: CurrentUser
) -> ConversationDetailRead:
    return ChatService(db).get_detail(conv_id, current_user.id)


@router.patch(
    "/{conv_id}",
    response_model=ConversationRead,
    summary="Rename a conversation",
)
def rename_conversation(
    conv_id: int,
    payload: ConversationRename,
    db: DBSession,
    current_user: CurrentUser,
) -> ConversationRead:
    return ChatService(db).rename(conv_id, current_user.id, payload.title)


@router.delete(
    "/{conv_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and its messages",
)
def delete_conversation(
    conv_id: int, db: DBSession, current_user: CurrentUser
) -> None:
    ChatService(db).delete(conv_id, current_user.id)


@router.post(
    "/{conv_id}/chat",
    summary="Send a message and stream the answer as Server-Sent Events",
    responses={200: {"content": {"text/event-stream": {}}}},
    response_class=StreamingResponse,
)
async def chat(
    conv_id: int,
    payload: ChatRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    """SSE stream: meta -> [sources] -> delta* -> done (or error)."""
    service = ChatService(db)
    # Validate ownership BEFORE the streaming response starts: once the
    # stream is open, status codes can no longer change, so a 404 raised
    # inside the generator would become a broken stream instead of a 404.
    service.get_for_user(conv_id, current_user.id)

    async def event_stream() -> AsyncIterator[str]:
        async for event in service.stream_chat(
            conv_id, current_user.id, payload.content, payload.images
        ):
            yield _format_sse(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


def _format_sse(event: ChatStreamEvent) -> str:
    return f"event: {event.event}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"
