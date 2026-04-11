"""Document model.

Represents a PDF uploaded by a user. The actual file bytes live in MinIO;
this table holds metadata + processing state.

Status lifecycle:
    pending → processing → ready
                        ↘ failed (with error_message set)
"""

import enum
import uuid

from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from app.models.base import TenantBase


class DocumentStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class DocumentPublic(SQLModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    original_filename: str
    status: DocumentStatus
    page_count: int | None
    file_size_bytes: int
    error_message: str | None
    uploaded_by_id: uuid.UUID


class DocumentStatusUpdate(SQLModel):
    status: DocumentStatus
    page_count: int | None = None
    error_message: str | None = None


# ── Database table ────────────────────────────────────────────────────────────

class Document(TenantBase, table=True):
    __tablename__ = "document"

    uploaded_by_id: uuid.UUID = Field(
        foreign_key="app_user.id",
        nullable=False,
        description="User who uploaded the document",
    )

    # Original filename as provided by the user
    original_filename: str = Field(max_length=500, nullable=False)

    # Sanitised filename used internally
    filename: str = Field(max_length=500, nullable=False)

    # SHA-256 hash of file contents for deduplication
    file_hash: str = Field(max_length=64, nullable=False, index=True)

    # MinIO object key: "{bucket}/{tenant_id}/{doc_id}/{filename}"
    minio_object_key: str = Field(max_length=1000, nullable=False)

    # Processing status
    status: DocumentStatus = Field(
        default=DocumentStatus.pending,
        sa_column=Column(
            SAEnum(DocumentStatus, name="document_status"),
            nullable=False,
            default=DocumentStatus.pending,
        ),
    )

    # Populated after successful ingestion
    page_count: int | None = Field(default=None, nullable=True)

    file_size_bytes: int = Field(nullable=False)

    # Set when status=failed to surface the error to the user
    error_message: str | None = Field(default=None, nullable=True)
