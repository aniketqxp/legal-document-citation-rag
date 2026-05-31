"""Celery tasks for the document ingestion pipeline.

The ingest_document task runs:

    pending -> processing -> parse -> chunk -> embed -> bulk_insert -> ready
                                                        -> failed
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from celery import Task

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

RETRY_BACKOFF_BASE: int = 60


class HardIngestionFailure(Exception):
    """Raised for failures that should not be retried."""


@celery_app.task(
    name="ingest_document",
    bind=True,
    max_retries=3,
)
def ingest_document(self: Task, doc_id: str, tenant_id: str) -> dict:
    """Ingest a PDF document through the full pipeline."""
    try:
        return asyncio.run(_run_pipeline(doc_id, tenant_id))

    except HardIngestionFailure as exc:
        logger.error("Hard ingestion failure for doc_id=%s: %s", doc_id, exc)
        return {"doc_id": doc_id, "status": "failed", "error": str(exc)}

    except Exception as exc:
        retry_num = self.request.retries
        countdown = RETRY_BACKOFF_BASE * (2**retry_num)
        logger.warning(
            "Transient failure for doc_id=%s; retry %d/%d in %ds: %s",
            doc_id,
            retry_num + 1,
            self.max_retries,
            countdown,
            exc,
        )
        raise self.retry(exc=exc, countdown=countdown)


async def _run_pipeline(doc_id_str: str, tenant_id_str: str) -> dict:
    """Drive the full ingestion pipeline asynchronously."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings
    from app.crud import document as crud_document
    from app.crud.chunk import bulk_insert_chunks
    from app.models.document import Document, DocumentStatus, DocumentStatusUpdate
    from app.services import storage
    from app.services.chunker import chunk_blocks
    from app.services.embeddings import EmbeddingError, generate_embeddings
    from app.services.pdf_parser import get_ingestor

    doc_id = uuid.UUID(doc_id_str)
    tenant_id = uuid.UUID(tenant_id_str)

    engine = create_async_engine(settings.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        async with async_session_maker() as session:
            result = await session.execute(
                select(Document).where(
                    Document.id == doc_id,
                    Document.tenant_id == tenant_id,
                )
            )
            doc = result.scalar_one_or_none()
            if not doc:
                raise HardIngestionFailure(
                    f"Document {doc_id} not found for tenant {tenant_id}."
                )

            await crud_document.update_status(
                session,
                document=doc,
                update=DocumentStatusUpdate(status=DocumentStatus.processing),
            )

            try:
                pdf_bytes = await storage.download_file(doc.minio_object_key)

                ingestor = get_ingestor()
                blocks = ingestor.parse(pdf_bytes)
                if not blocks:
                    raise HardIngestionFailure("No text extracted from PDF.")

                chunks = chunk_blocks(blocks)
                if not chunks:
                    raise HardIngestionFailure("Chunking produced no chunks.")

                embeddings = await generate_embeddings([c.embed_content for c in chunks])

                chunks_created = await bulk_insert_chunks(
                    session,
                    document_id=doc.id,
                    tenant_id=doc.tenant_id,
                    chunks=chunks,
                    embeddings=embeddings,
                )

                page_count = max(b.page_number for b in blocks)
                await crud_document.update_status(
                    session,
                    document=doc,
                    update=DocumentStatusUpdate(
                        status=DocumentStatus.ready,
                        page_count=page_count,
                    ),
                )

                return {
                    "doc_id": doc_id_str,
                    "tenant_id": tenant_id_str,
                    "status": "ready",
                    "chunks": chunks_created,
                }

            except HardIngestionFailure as exc:
                await _mark_failed(session, crud_document, doc, str(exc))
                raise
            except EmbeddingError as exc:
                await _mark_failed(session, crud_document, doc, str(exc))
                raise HardIngestionFailure(str(exc)) from exc
            except Exception as exc:
                await _mark_failed(session, crud_document, doc, str(exc))
                raise
    finally:
        await engine.dispose()


async def _mark_failed(session, crud_document, doc, error_msg: str) -> None:
    from app.models.document import DocumentStatus, DocumentStatusUpdate

    try:
        await crud_document.update_status(
            session,
            document=doc,
            update=DocumentStatusUpdate(
                status=DocumentStatus.failed,
                error_message=error_msg[:500],
            ),
        )
    except Exception:
        logger.exception("Could not mark failed state for doc_id=%s", doc.id)
