"""DocumentChunk model.

Each chunk is a semantically coherent section of text extracted from a Document.
The embedding column (Vector(1536)) enables pgvector cosine similarity search.

Citation metadata (page_number, section_title) is stored AT CREATION TIME
so the frontend can render clickable citations without round-tripping to the PDF.

CRITICAL: tenant_id is on every chunk. All vector searches MUST include
          WHERE tenant_id = :tenant_id — cross-tenant vector leakage is
          an existential failure for this product.
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import TenantBase


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class DocumentChunkPublic(SQLModel):
    id: uuid.UUID
    document_id: uuid.UUID
    content: str
    page_number: int
    section_title: str | None
    chunk_index: int
    token_count: int


# ── Database table ────────────────────────────────────────────────────────────

class DocumentChunk(TenantBase, table=True):
    __tablename__ = "document_chunk"

    document_id: uuid.UUID = Field(
        foreign_key="document.id",
        nullable=False,
        index=True,
        description="Parent document",
    )

    # The raw text content of this chunk
    content: str = Field(sa_column=Column("content", type_=None, nullable=False))

    # Citation metadata — set during chunking, never changes
    page_number: int = Field(
        nullable=False,
        description="Page in the original PDF where this chunk starts",
    )
    section_title: str | None = Field(
        default=None,
        max_length=500,
        nullable=True,
        description="Nearest section heading above this chunk (for citations)",
    )
    chunk_index: int = Field(
        nullable=False,
        description="Zero-based position within the document",
    )
    token_count: int = Field(
        nullable=False,
        description="Number of tokens in content (tiktoken cl100k_base)",
    )

    # 1536-dim embedding from text-embedding-3-small via OpenRouter
    # MUST match EMBEDDING_DIMENSIONS in config.py and Vector(N) in the migration
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(1536), nullable=True),
    )
