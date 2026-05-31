"""Conversation and Message models.

A Conversation groups a series of Messages between a user and the AI.
Each Message can carry a citations_json payload for rendering clickable
citation chips in the frontend.
"""

import uuid

from sqlmodel import Field, SQLModel

from app.models.base import TenantBase


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ConversationPublic(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    title: str | None


class MessagePublic(SQLModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str           # "user" | "assistant"
    content: str
    citations_json: str | None  # JSON string of citation objects


# ── Database tables ───────────────────────────────────────────────────────────

class Conversation(TenantBase, table=True):
    __tablename__ = "conversation"

    user_id: uuid.UUID = Field(
        foreign_key="app_user.id",
        nullable=False,
        index=True,
    )
    title: str | None = Field(
        default=None,
        max_length=500,
        nullable=True,
        description="Auto-generated from first user message",
    )


class Message(TenantBase, table=True):
    __tablename__ = "message"

    conversation_id: uuid.UUID = Field(
        foreign_key="conversation.id",
        nullable=False,
        index=True,
    )
    role: str = Field(
        max_length=20,
        nullable=False,
        description="'user' or 'assistant'",
    )
    content: str = Field(nullable=False)
    # Serialised JSON: List[{sentence_idx, page, section, document_id, document_name}]
    citations_json: str | None = Field(default=None, nullable=True)
