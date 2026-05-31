"""Chat API routes — Phase 6: Multi-Document Context & Reasoning.

Endpoints
─────────
POST /chat/conversations                  Create a new conversation
GET  /chat/conversations                  List conversations for current user
GET  /chat/conversations/{id}/messages    Get full message history for a conversation
POST /chat/conversations/{id}/query       Ask a question → get a cited answer

Query Flow (POST /chat/conversations/{id}/query)
─────────────────────────────────────────────────
1. Fetch the last N messages from the conversation as windowed history.
2. Embed the user query via the same model used at ingest time.
3. Run hybrid retrieval: pgvector cosine search + Postgres FTS, fused with RRF.
   Optionally scoped to a specific list of document_ids (Phase 6 multi-doc).
4. Build alias-injected LLM prompt from top-K fused chunks.
5. Call Gemini 2.5 Flash with the conversation history injected as native
   multi-turn content (user/model alternating turns).
6. Parse [Doc N] aliases from the response → structured CitationResult JSON.
7. Persist: user Message, then assistant Message + citations_json.
8. Return: answer text + citation array + conversation metadata.

Tenant Isolation
────────────────
Every DB call injects current_user.tenant_id. The retrieval service also
accepts an optional document_ids filter, allowing users to scope a query
to specific uploaded documents rather than searching across all tenant docs.

All endpoints require a valid JWT (CurrentUser dependency).
"""

# from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
from app.core.limiter import limiter

logger = logging.getLogger(__name__)
from app.crud import conversation as crud_conversation
from app.models.conversation import ConversationPublic, MessagePublic
from app.services import llm as llm_service
from app.services.embeddings import EmbeddingError, generate_embeddings
from app.services.llm import HistoryItem, LLMError
from app.services.retrieval import hybrid_retrieve

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Number of prior messages to inject as conversation history.
# 6 messages = 3 full user/assistant exchanges.
HISTORY_WINDOW: int = 6


# ── Request / Response schemas ─────────────────────────────────────────────────


class ConversationCreate(BaseModel):
    title: str | None = None


class QueryRequest(BaseModel):
    question: str
    document_ids: list[uuid.UUID] | None = None  # None = search all tenant docs


class CitationOut(BaseModel):
    alias: str
    chunk_id: str
    document_id: str
    source_filename: str
    page_number: int
    section_title: str | None
    snippet: str

CitationOut.model_rebuild()
class QueryResponse(BaseModel):
    conversation_id: uuid.UUID
    answer: str
    citations: list[CitationOut]
    chunks_used: int
    model_used: str


QueryRequest.model_rebuild()
QueryResponse.model_rebuild()



class ConversationListResponse(BaseModel):
    conversations: list[ConversationPublic]
    total: int


class MessageListResponse(BaseModel):
    messages: list[MessagePublic]
    total: int


# ── Create Conversation ────────────────────────────────────────────────────────


@router.post(
    "/conversations",
    response_model=ConversationPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new conversation",
)
async def create_conversation(
    session: SessionDep,
    current_user: CurrentUser,
    body: ConversationCreate,
) -> ConversationPublic:
    """Create a blank conversation context for follow-up queries."""
    conv = await crud_conversation.create_conversation(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=body.title,
    )
    return ConversationPublic.model_validate(conv)


# ── List Conversations ─────────────────────────────────────────────────────────


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List all conversations for the current user",
)
async def list_conversations(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 50,
) -> ConversationListResponse:
    """Return a paginated list of the user's past conversations, newest first."""
    if limit > 100:
        raise HTTPException(status_code=400, detail="limit must be ≤ 100")

    convs = await crud_conversation.list_conversations(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        skip=skip,
        limit=limit,
    )
    return ConversationListResponse(
        conversations=[ConversationPublic.model_validate(c) for c in convs],
        total=len(convs),
    )


# ── Get Message History ────────────────────────────────────────────────────────


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
    summary="Get full message history for a conversation",
)
async def get_messages(
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> MessageListResponse:
    """Return all messages in a conversation, oldest-first."""
    conv = await crud_conversation.get_conversation(
        session,
        conversation_id=conversation_id,
        tenant_id=current_user.tenant_id,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await crud_conversation.list_messages(
        session,
        conversation_id=conversation_id,
        tenant_id=current_user.tenant_id,
    )
    return MessageListResponse(
        messages=[MessagePublic.model_validate(m) for m in messages],
        total=len(messages),
    )


# ── Query Endpoint ─────────────────────────────────────────────────────────────


@router.post(
    "/conversations/{conversation_id}/query",
    response_model=QueryResponse,
    summary="Ask a question and receive a cited answer",
    description=(
        "Runs the full RAG pipeline with multi-turn memory and optional document "
        "scoping: embed query → fetch history → hybrid retrieve (optionally scoped "
        "to document_ids) → RRF fuse → LLM generate with history → parse citations "
        "→ persist messages."
    ),
)
@limiter.limit("20/minute")
async def query_conversation(
    request: Request,  # noqa: ARG001 — required by slowapi decorator
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    body: QueryRequest,
) -> QueryResponse:
    """Core RAG query endpoint with Phase 6 multi-turn history and doc scoping."""

    # ── 1. Verify conversation ownership ─────────────────────────────────────
    conv = await crud_conversation.get_conversation(
        session,
        conversation_id=conversation_id,
        tenant_id=current_user.tenant_id,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    # ── 2. Fetch windowed conversation history (Phase 6) ─────────────────────
    # We fetch ALL messages then take the tail so we always get the most recent
    # exchanges, not an arbitrary offset window.
    all_messages = await crud_conversation.list_messages(
        session,
        conversation_id=conversation_id,
        tenant_id=current_user.tenant_id,
    )
    history: list[HistoryItem] = [
        HistoryItem(role=m.role, content=m.content)
        for m in all_messages[-HISTORY_WINDOW:]
    ]
    logger.debug(
        "Injecting %d history messages for conv=%s", len(history), conversation_id
    )

    # ── 3. Embed the question ─────────────────────────────────────────────────
    try:
        query_embeddings = await generate_embeddings([question])
        query_embedding = query_embeddings[0]
    except EmbeddingError as exc:
        logger.error("Embedding failed for conv=%s: %s", conversation_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Embedding service is temporarily unavailable. Please try again.",
        ) from exc

    # ── 4. Hybrid retrieve with optional document scoping (Phase 6) ──────────
    raw_chunks = await hybrid_retrieve(
        session,
        query=question,
        query_embedding=query_embedding,
        tenant_id=current_user.tenant_id,
        document_ids=body.document_ids,  # None = search all tenant docs
    )

    if not raw_chunks:
        no_context_answer = "I cannot determine this from the provided documents."
        await _persist_exchange(
            session,
            tenant_id=current_user.tenant_id,
            conversation_id=conversation_id,
            question=question,
            answer=no_context_answer,
            citations_json=None,
        )
        return QueryResponse(
            conversation_id=conversation_id,
            answer=no_context_answer,
            citations=[],
            chunks_used=0,
            model_used=llm_service.QUERY_MODEL,
        )

    # ── 5. Build ChunkContext objects for the LLM service ────────────────────
    from app.services.llm import ChunkContext

    chunk_contexts = [
        ChunkContext(
            alias_index=idx,
            chunk_id=raw.chunk_id,
            document_id=raw.document_id,
            original_filename=raw.original_filename,
            page_number=raw.page_number,
            section_title=raw.section_title,
            content=raw.content,
            rrf_score=1.0 / (60 + raw.rank),
        )
        for idx, raw in enumerate(raw_chunks, start=1)
    ]

    # ── 6. Call LLM — inject history for multi-turn reasoning (Phase 6) ──────
    try:
        llm_result = await llm_service.answer_query(
            query=question,
            chunks=chunk_contexts,
            history=history if history else None,
        )
    except LLMError as exc:
        logger.error("LLM failure for conv=%s: %s", conversation_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    # ── 7. Serialize citations for storage ────────────────────────────────────
    citations_for_storage = [
        {
            "alias": c.alias,
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "source_filename": c.source_filename,
            "page_number": c.page_number,
            "section_title": c.section_title,
            "snippet": c.snippet,
        }
        for c in llm_result.citations
    ]
    citations_json_str = (
        json.dumps(citations_for_storage) if citations_for_storage else None
    )

    # ── 8. Persist the exchange ───────────────────────────────────────────────
    await _persist_exchange(
        session,
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        question=question,
        answer=llm_result.answer,
        citations_json=citations_json_str,
    )

    logger.info(
        "Query completed — conv=%s chunks=%d citations=%d history=%d",
        conversation_id,
        llm_result.chunks_used,
        len(llm_result.citations),
        len(history),
    )

    return QueryResponse(
        conversation_id=conversation_id,
        answer=llm_result.answer,
        citations=[
            CitationOut(
                alias=c.alias,
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                source_filename=c.source_filename,
                page_number=c.page_number,
                section_title=c.section_title,
                snippet=c.snippet,
            )
            for c in llm_result.citations
        ],
        chunks_used=llm_result.chunks_used,
        model_used=llm_result.model_used,
    )


# ── Private helpers ────────────────────────────────────────────────────────────


async def _persist_exchange(
    session: SessionDep,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    question: str,
    answer: str,
    citations_json: str | None,
) -> None:
    """Persist the user question and assistant answer as two Message rows."""
    await crud_conversation.create_message(
        session,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="user",
        content=question,
        citations_json=None,  # user messages never carry citations
    )
    await crud_conversation.create_message(
        session,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="assistant",
        content=answer,
        citations_json=citations_json,
    )
