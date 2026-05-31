"""CRUD helpers for Conversation and Message models.

Tenant isolation contract: every function accepts tenant_id as a mandatory
keyword-only argument and injects it into every query predicate/insert.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message


# ── Conversation ──────────────────────────────────────────────────────────────

async def create_conversation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str | None = None,
) -> Conversation:
    """Create a new Conversation row."""
    conv = Conversation(
        tenant_id=tenant_id,
        user_id=user_id,
        title=title,
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


async def get_conversation(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Conversation | None:
    """Fetch a conversation by ID, scoped to tenant."""
    stmt = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.tenant_id == tenant_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_conversations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> list[Conversation]:
    """List a user's conversations, newest first."""
    stmt = (
        select(Conversation)
        .where(Conversation.tenant_id == tenant_id)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── Message ───────────────────────────────────────────────────────────────────

async def create_message(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    citations_json: str | None = None,
) -> Message:
    """Persist a single message (user or assistant) to the conversation."""
    msg = Message(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role=role,
        content=content,
        citations_json=citations_json,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def list_messages(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> list[Message]:
    """Return all messages for a conversation, oldest first."""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .where(Message.tenant_id == tenant_id)
        .order_by(Message.created_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
