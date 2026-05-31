"""CRUD helpers for DocumentChunk.

The single critical operation here is bulk insertion. Inserting chunks
individually (one ``session.add()`` + ``commit()`` per row) would be
catastrophically slow for large contracts that produce hundreds of chunks,
as each round-trip to PostgreSQL takes ~1–5 ms. A 300-chunk document would
incur ~1.5 seconds of pure DB latency in individual-insert mode.

Instead, ``session.add_all()`` submits a single large INSERT statement
(batched internally by SQLAlchemy), bringing 300-chunk insertion down to
a few milliseconds.

TENANT ISOLATION GUARANTEE
───────────────────────────
``tenant_id`` is set on EVERY DocumentChunk row from the parent Document
object — never from user-supplied input. The calling code (``tasks.py``)
passes ``document.tenant_id`` directly.

All future CRUD helpers added to this module MUST include a
``.where(DocumentChunk.tenant_id == tenant_id)`` predicate. Cross-tenant
vector search is an existential failure for this product.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import DocumentChunk
from app.services.chunker import ChunkData

logger = logging.getLogger(__name__)


async def bulk_insert_chunks(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    chunks: list[ChunkData],
    embeddings: list[list[float]],
) -> int:
    """Insert ``DocumentChunk`` rows with pre-computed embeddings.

    All rows are inserted in a single database transaction. If any row fails
    (e.g., a constraint violation), the entire batch is rolled back, leaving
    the ``Document`` in ``processing`` status. The Celery task will then mark
    it ``failed``.

    Args:
        session:     Async SQLAlchemy session (from ``async_session_maker``).
        document_id: UUID of the parent ``Document`` row.
        tenant_id:   Tenant isolation key — copied to EVERY chunk row.
        chunks:      ``ChunkData`` list from the chunker, in document order.
        embeddings:  Float vector list from the embedding service.
                     MUST be the same length and in the same order as ``chunks``.

    Returns:
        The number of rows successfully inserted.

    Raises:
        ValueError: If ``len(chunks) != len(embeddings)`` (programming error).
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"Chunk / embedding count mismatch: "
            f"{len(chunks)} chunks vs {len(embeddings)} embeddings. "
            "This is a pipeline bug — aborting insertion."
        )

    if not chunks:
        logger.warning(
            "bulk_insert_chunks called with empty chunk list for "
            "document_id=%s — nothing inserted.",
            document_id,
        )
        return 0

    # Build ORM objects up-front. We do NOT call session.add() in a loop here
    # because each individual add() can trigger a lazy flush. We collect all
    # objects first, then add_all() in one shot.
    chunk_objects: list[DocumentChunk] = [
        DocumentChunk(
            tenant_id=tenant_id,           # TENANT ISOLATION — never omit this
            document_id=document_id,
            content=chunk.raw_text,        # clean text, no section-title prefix
            page_number=chunk.page_number,
            section_title=chunk.section_title,
            chunk_index=chunk.chunk_index,
            token_count=chunk.token_count,
            embedding=embedding,           # pgvector Vector(1536)
        )
        for chunk, embedding in zip(chunks, embeddings)
    ]

    session.add_all(chunk_objects)
    await session.commit()

    logger.info(
        "Inserted %d chunks — document_id=%s  tenant_id=%s",
        len(chunk_objects),
        document_id,
        tenant_id,
    )
    return len(chunk_objects)
