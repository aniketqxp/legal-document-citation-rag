"""CRUD helpers for the Document model.

ALL queries filter by tenant_id.  Cross-tenant access is a hard failure.

Functions:
  create_document      — insert a new Document row (status=pending)
  get_document         — fetch one by (id, tenant_id)
  list_documents       — paginated list for a tenant
  update_status        — transition status (pending→processing→ready|failed)
  delete_document      — hard delete a document row (caller must delete MinIO obj)
  get_document_by_hash — deduplication helper: find existing doc with same hash
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus, DocumentStatusUpdate


# ── Create ────────────────────────────────────────────────────────────────────

async def create_document(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    uploaded_by_id: uuid.UUID,
    original_filename: str,
    filename: str,
    file_hash: str,
    minio_object_key: str,
    file_size_bytes: int,
) -> Document:
    """Insert a new Document with status=pending.

    The caller (upload route) should have already stored the file in MinIO
    before calling this so that a DB record always has a corresponding object.
    """
    doc = Document(
        tenant_id=tenant_id,
        uploaded_by_id=uploaded_by_id,
        original_filename=original_filename,
        filename=filename,
        file_hash=file_hash,
        minio_object_key=minio_object_key,
        file_size_bytes=file_size_bytes,
        status=DocumentStatus.pending,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc


# ── Read ──────────────────────────────────────────────────────────────────────

async def get_document(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Document | None:
    """Fetch a single Document, scoped to the requesting tenant.

    Returns None (not an exception) if the document does not exist or belongs
    to a different tenant — the route layer converts this to 404.
    """
    stmt = (
        select(Document)
        .where(Document.id == document_id)
        .where(Document.tenant_id == tenant_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_documents(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> list[Document]:
    """Return a paginated list of Documents for a tenant, newest first."""
    stmt = (
        select(Document)
        .where(Document.tenant_id == tenant_id)
        .order_by(Document.created_at.desc())  # type: ignore[union-attr]
        .offset(skip)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_document_by_hash(
    session: AsyncSession,
    *,
    file_hash: str,
    tenant_id: uuid.UUID,
) -> Document | None:
    """Find an existing document with the same SHA-256 hash for deduplication.

    Cross-tenant deduplication is intentionally NOT performed — two tenants
    may upload the same file and they must remain isolated.
    """
    stmt = (
        select(Document)
        .where(Document.file_hash == file_hash)
        .where(Document.tenant_id == tenant_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ── Update ────────────────────────────────────────────────────────────────────

async def update_status(
    session: AsyncSession,
    *,
    document: Document,
    update: DocumentStatusUpdate,
) -> Document:
    """Apply a status transition (and optional page_count / error_message).

    Called by the Celery worker (Phase 3) when ingestion completes or fails.
    The document object must have already been fetched with the correct tenant_id.
    """
    document.status = update.status
    if update.page_count is not None:
        document.page_count = update.page_count
    if update.error_message is not None:
        document.error_message = update.error_message

    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


# ── Delete ────────────────────────────────────────────────────────────────────

async def delete_document(
    session: AsyncSession,
    *,
    document: Document,
) -> None:
    """Hard-delete a Document row.

    The caller (route layer) is responsible for deleting the MinIO object
    BEFORE calling this so there are no orphaned blobs.  Cascading deletes
    on DocumentChunk rows are handled at the DB level (ON DELETE CASCADE in
    the migration).
    """
    await session.delete(document)
    await session.commit()
