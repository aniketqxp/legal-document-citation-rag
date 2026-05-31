"""Document API routes.

Endpoints
─────────
POST   /documents/upload         Upload a PDF → MinIO → DB record → Celery task
GET    /documents                List all documents for the current tenant
GET    /documents/{id}           Get metadata for a single document
GET    /documents/{id}/url       Get a pre-signed download URL (1-hour TTL)
DELETE /documents/{id}           Hard-delete document (MinIO + DB)

All endpoints require a valid JWT.  Tenant isolation is enforced by passing
current_user.tenant_id to every CRUD and storage call.

File validation rules:
  - Content-Type must be application/pdf
  - Max file size: 50 MB  (configurable via MAX_UPLOAD_BYTES constant below)
  - Minimum file size: 100 bytes (reject empty-ish uploads)
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi import File as FastAPIFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import CurrentUser, SessionDep
from app.crud import document as crud_document
from app.models.document import DocumentPublic
from app.services import storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024   # 50 MB
MIN_UPLOAD_BYTES: int = 100                 # reject suspiciously tiny files
ALLOWED_CONTENT_TYPES: set[str] = {"application/pdf"}


# ── Response schemas ──────────────────────────────────────────────────────────

class DocumentListResponse(BaseModel):
    documents: list[DocumentPublic]
    total: int


class PresignedUrlResponse(BaseModel):
    url: str
    expires_in_seconds: int


class StuckDocument(BaseModel):
    id: uuid.UUID
    original_filename: str
    created_at: datetime


class IntegrityReport(BaseModel):
    healthy: bool
    stuck_count: int
    stuck_documents: list[StuckDocument]


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post(
    "/upload",
    response_model=DocumentPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a PDF contract",
    description=(
        "Upload a PDF file (max 50 MB). The file is stored in MinIO, a "
        "Document record is created with status=`pending`, and a background "
        "ingestion task is dispatched to Celery (Phase 3 pipeline)."
    ),
)
async def upload_document(
    session: SessionDep,
    current_user: CurrentUser,
    file: UploadFile = FastAPIFile(
        ...,
        description="PDF file to upload (max 50 MB)",
    ),
) -> DocumentPublic:
    """Upload a PDF and kick off the ingestion pipeline."""

    # ── 1. Validate content type ──────────────────────────────────────────────
    content_type = file.content_type or ""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type '{content_type}'. "
                "Only application/pdf is accepted."
            ),
        )

    # ── 2. Read & validate file size ──────────────────────────────────────────
    file_data = await file.read()
    file_size = len(file_data)

    if file_size < MIN_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is too small or empty.",
        )

    if file_size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File size {file_size:,} bytes exceeds the 50 MB limit. "
                "Please split the document and upload each part separately."
            ),
        )

    # ── 3. Deduplication check (same hash already ingested for this tenant) ───
    import hashlib
    sha256_hash = hashlib.sha256(file_data).hexdigest()
    existing = await crud_document.get_document_by_hash(
        session,
        file_hash=sha256_hash,
        tenant_id=current_user.tenant_id,
    )
    if existing:
        logger.info(
            "Dedup hit: tenant=%s already has document hash %s (doc_id=%s)",
            current_user.tenant_id,
            sha256_hash[:16],
            existing.id,
        )
        return DocumentPublic.model_validate(existing)

    # ── 4. Allocate document ID (used in MinIO key before DB insert) ──────────
    document_id = uuid.uuid4()

    # ── 5. Upload to MinIO ────────────────────────────────────────────────────
    try:
        object_key, safe_filename, _ = await storage.upload_file(
            file_data=file_data,
            tenant_id=current_user.tenant_id,
            document_id=document_id,
            original_filename=file.filename or "document.pdf",
            content_type=content_type,
        )
    except Exception as exc:
        logger.exception("MinIO upload failed for tenant=%s", current_user.tenant_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="File storage is temporarily unavailable. Please try again.",
        ) from exc

    # ── 6. Insert Document record ─────────────────────────────────────────────
    try:
        doc = await crud_document.create_document(
            session,
            tenant_id=current_user.tenant_id,
            uploaded_by_id=current_user.id,
            original_filename=file.filename or "document.pdf",
            filename=safe_filename,
            file_hash=sha256_hash,
            minio_object_key=object_key,
            file_size_bytes=file_size,
        )
    except Exception as exc:
        # If DB insert fails, attempt cleanup of the orphaned MinIO object
        logger.exception(
            "DB insert failed for document_id=%s; cleaning up MinIO object",
            document_id,
        )
        try:
            await storage.delete_file(object_key)
        except Exception:
            logger.warning("Could not clean up MinIO object %s after DB failure", object_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save document record. The upload was rolled back.",
        ) from exc

    # 7. Dispatch tenant-scoped Celery ingestion task.
    try:
        from app.worker.tasks import ingest_document
        ingest_document.delay(str(doc.id), str(current_user.tenant_id))
        logger.info(
            "Dispatched ingest_document task for doc_id=%s tenant=%s",
            doc.id,
            current_user.tenant_id,
        )
    except Exception:
        # Celery is unavailable (e.g., Redis down); leave the document pending.
        # A retry mechanism or admin job can re-queue pending documents later.
        logger.exception(
            "Failed to enqueue ingest_document for doc_id=%s; document remains pending",
            doc.id,
        )

    return DocumentPublic.model_validate(doc)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List all documents for the current tenant",
)
async def list_documents(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 50,
) -> DocumentListResponse:
    """Return a paginated list of documents uploaded by the current tenant."""
    if limit > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be ≤ 200",
        )

    docs = await crud_document.list_documents(
        session,
        tenant_id=current_user.tenant_id,
        skip=skip,
        limit=limit,
    )
    return DocumentListResponse(
        documents=[DocumentPublic.model_validate(d) for d in docs],
        total=len(docs),
    )


# ── Integrity Check ───────────────────────────────────────────────────────────
# NOTE: This route MUST be defined before /{document_id} so FastAPI doesn't
# try to parse "integrity" as a UUID and return a 422.

@router.get(
    "/integrity",
    response_model=IntegrityReport,
    summary="Find ready documents with no indexed chunks",
    description=(
        "Returns any documents with status=ready that have zero chunks in "
        "the database, indicating a failed or incomplete ingestion pipeline."
    ),
)
async def check_integrity(
    session: SessionDep,
    current_user: CurrentUser,
) -> IntegrityReport:
    """Detect documents that finished ingestion but produced no searchable chunks."""
    sql = text("""
        SELECT d.id, d.original_filename, d.created_at
        FROM document d
        LEFT JOIN document_chunk dc ON dc.document_id = d.id
        WHERE d.tenant_id = :tenant_id
          AND d.status = 'ready'
        GROUP BY d.id, d.original_filename, d.created_at
        HAVING COUNT(dc.id) = 0
    """)
    result = await session.execute(sql, {"tenant_id": str(current_user.tenant_id)})
    rows = result.fetchall()

    stuck = [
        StuckDocument(
            id=row.id,
            original_filename=row.original_filename,
            created_at=row.created_at,
        )
        for row in rows
    ]

    return IntegrityReport(
        healthy=len(stuck) == 0,
        stuck_count=len(stuck),
        stuck_documents=stuck,
    )


# ── Get by ID ─────────────────────────────────────────────────────────────────

@router.get(
    "/{document_id}",
    response_model=DocumentPublic,
    summary="Get document metadata",
)
async def get_document(
    document_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> DocumentPublic:
    """Fetch metadata for a single document (tenant-scoped)."""
    doc = await crud_document.get_document(
        session,
        document_id=document_id,
        tenant_id=current_user.tenant_id,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentPublic.model_validate(doc)


# ── Pre-signed URL ────────────────────────────────────────────────────────────

@router.get(
    "/{document_id}/url",
    response_model=PresignedUrlResponse,
    summary="Get a pre-signed download URL for the PDF",
    description=(
        "Returns a time-limited URL (default 1 hour) that the frontend can use "
        "to stream the PDF directly from MinIO without routing through FastAPI. "
        "The document must belong to the requesting tenant."
    ),
)
async def get_document_url(
    document_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    expires_in: int = 3600,
) -> PresignedUrlResponse:
    """Generate a pre-signed MinIO URL for direct PDF streaming."""
    if not (60 <= expires_in <= 86400):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expires_in must be between 60 and 86400 seconds (1 min – 24 hrs).",
        )

    doc = await crud_document.get_document(
        session,
        document_id=document_id,
        tenant_id=current_user.tenant_id,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        url = await storage.generate_presigned_url(
            doc.minio_object_key,
            expires_in=expires_in,
        )
    except Exception as exc:
        logger.exception("Failed to generate pre-signed URL for doc_id=%s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not generate download URL. Please try again.",
        ) from exc

    return PresignedUrlResponse(url=url, expires_in_seconds=expires_in)


@router.get(
    "/{document_id}/proxy",
    summary="Proxy stream for document (CORS-safe)",
    description="Streams the PDF through the backend to avoid CORS issues in local dev.",
)
async def proxy_document_stream(
    document_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    """Bypass CORS by streaming the file through the API origin."""
    doc = await crud_document.get_document(
        session,
        document_id=document_id,
        tenant_id=current_user.tenant_id,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    body, content_type, content_length = await storage.stream_file(doc.minio_object_key)

    return StreamingResponse(
        body,
        media_type=content_type,
        headers={"Content-Length": str(content_length)}
    )


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hard-delete a document",
    description=(
        "Permanently deletes the PDF from MinIO and removes the Document record "
        "(and all associated chunks via CASCADE) from the database. "
        "This action is irreversible."
    ),
)
async def delete_document(
    document_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> None:
    """Delete a document and its MinIO object."""
    doc = await crud_document.get_document(
        session,
        document_id=document_id,
        tenant_id=current_user.tenant_id,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete from MinIO first — if this succeeds but DB delete fails, we have
    # an orphaned DB record (fixable by support), not an orphaned blob (data leak).
    try:
        await storage.delete_file(doc.minio_object_key)
    except Exception:
        logger.exception(
            "MinIO delete failed for object_key=%s; aborting DB delete",
            doc.minio_object_key,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Storage deletion failed. The document was not removed. Please try again.",
        )

    await crud_document.delete_document(session, document=doc)
    logger.info(
        "Deleted document doc_id=%s tenant=%s", document_id, current_user.tenant_id
    )
