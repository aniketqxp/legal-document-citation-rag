"""MinIO object storage service (S3-compatible via boto3).

Responsibilities:
  - Upload a file-like object → returns the object key stored in MinIO
  - Download an object back as bytes (used by the ingestion worker)
  - Delete an object (called when a Document record is hard-deleted)
  - Generate a pre-signed GET URL so the frontend can stream the PDF directly
    from MinIO without proxying through FastAPI (reduces backend load).

Object-key convention:
    {bucket}/{tenant_id}/{document_id}/{sanitised_filename}

All calls are blocking boto3 calls executed in a thread-pool via
asyncio.to_thread so the FastAPI event loop is never blocked.

TENANT ISOLATION: The tenant_id is embedded in every object key, making
accidental cross-tenant reads require an explicit key construction bug — an
extra safety layer on top of the DB-level tenant_id filter.
"""

import asyncio
import hashlib
import io
import logging
import re
import uuid
from typing import BinaryIO

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitise_filename(name: str) -> str:
    """Strip unsafe characters; keep extension; truncate to 200 chars."""
    name = name.strip()
    # Replace whitespace with underscores
    name = re.sub(r"\s+", "_", name)
    # Remove anything that isn't alphanumeric, dash, underscore, or dot
    name = re.sub(r"[^\w.\-]", "", name)
    # Collapse multiple dots
    name = re.sub(r"\.{2,}", ".", name)
    return name[:200] or "document.pdf"


def _build_object_key(tenant_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    """Build a deterministic, tenant-scoped MinIO object key."""
    return f"{tenant_id}/{document_id}/{filename}"


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Client factory ────────────────────────────────────────────────────────────

def _get_s3_client():
    """Return a configured boto3 S3 client pointing at MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=f"{'https' if settings.MINIO_USE_SSL else 'http'}://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        region_name="us-east-1",  # MinIO ignores region but boto3 requires it
    )


# ── Core async wrappers ───────────────────────────────────────────────────────

async def ensure_bucket_exists() -> None:
    """Create the bucket if it does not already exist (idempotent)."""
    def _sync():
        client = _get_s3_client()
        try:
            client.head_bucket(Bucket=settings.MINIO_BUCKET)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("404", "NoSuchBucket"):
                client.create_bucket(Bucket=settings.MINIO_BUCKET)
                logger.info("Created MinIO bucket: %s", settings.MINIO_BUCKET)
            else:
                raise

    await asyncio.to_thread(_sync)


async def upload_file(
    file_data: bytes,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    original_filename: str,
    content_type: str = "application/pdf",
) -> tuple[str, str, str]:
    """Upload ``file_data`` to MinIO and return (object_key, sanitised_filename, sha256_hash).

    Args:
        file_data: Raw bytes of the PDF.
        tenant_id: Owning tenant (embedded in object key for isolation).
        document_id: The pre-allocated Document UUID.
        original_filename: The name as provided by the user's browser.
        content_type: MIME type (default ``application/pdf``).

    Returns:
        A 3-tuple of:
          - ``object_key``   — the full MinIO key (relative to bucket)
          - ``safe_filename`` — the sanitised filename stored in the DB
          - ``sha256_hash``  — hex digest of file contents for deduplication
    """
    safe_filename = _sanitise_filename(original_filename)
    object_key = _build_object_key(tenant_id, document_id, safe_filename)
    sha256_hash = _sha256_of_bytes(file_data)

    def _sync():
        client = _get_s3_client()
        client.put_object(
            Bucket=settings.MINIO_BUCKET,
            Key=object_key,
            Body=io.BytesIO(file_data),
            ContentType=content_type,
            ContentLength=len(file_data),
            Metadata={
                "tenant-id": str(tenant_id),
                "document-id": str(document_id),
                "original-filename": original_filename[:512],
            },
        )
        logger.info(
            "Uploaded %s bytes to s3://%s/%s",
            len(file_data),
            settings.MINIO_BUCKET,
            object_key,
        )

    await asyncio.to_thread(_sync)
    return object_key, safe_filename, sha256_hash


async def download_file(object_key: str) -> bytes:
    """Download an object from MinIO and return its raw bytes.

    Called by the Celery ingestion worker (Phase 3) so it can parse the PDF
    without needing a pre-signed URL.
    """
    def _sync() -> bytes:
        client = _get_s3_client()
        response = client.get_object(Bucket=settings.MINIO_BUCKET, Key=object_key)
        return response["Body"].read()

    return await asyncio.to_thread(_sync)


async def stream_file(object_key: str):
    """Return a generator and metadata for streaming a file from MinIO."""
    def _sync():
        client = _get_s3_client()
        response = client.get_object(Bucket=settings.MINIO_BUCKET, Key=object_key)
        return response["Body"], response["ContentType"], response["ContentLength"]

    return await asyncio.to_thread(_sync)


async def delete_file(object_key: str) -> None:
    """Permanently delete an object from MinIO.

    Called when a Document record is hard-deleted by an authorised user.
    Raises StorageError if MinIO returns an unexpected error.
    """
    def _sync():
        client = _get_s3_client()
        client.delete_object(Bucket=settings.MINIO_BUCKET, Key=object_key)
        logger.info("Deleted s3://%s/%s", settings.MINIO_BUCKET, object_key)

    await asyncio.to_thread(_sync)


async def generate_presigned_url(object_key: str, expires_in: int = 3600) -> str:
    """Return a pre-signed GET URL valid for ``expires_in`` seconds.

    The frontend uses this URL to stream the PDF directly from MinIO so
    FastAPI does not have to buffer large binary responses.

    Args:
        object_key: The MinIO object key (as stored in Document.minio_object_key).
        expires_in: Seconds until the URL expires (default 1 hour).

    Returns:
        A pre-signed HTTPS (or HTTP in local dev) URL string.
    """
    def _sync() -> str:
        client = _get_s3_client()
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.MINIO_BUCKET, "Key": object_key},
            ExpiresIn=expires_in,
        )
        # Fix for local development: The backend uses the internal Docker hostname "minio",
        # but the frontend browser needs "localhost" to resolve the IP address.
        if settings.ENVIRONMENT == "local":
            url = url.replace("minio:9000", "localhost:9000")
        return url

    return await asyncio.to_thread(_sync)


async def ensure_bucket_cors() -> None:
    """Configures the CORS policy on the MinIO bucket for frontend access.

    This allows our React app on localhost:5173/3000 to fetch PDFs directly
    via pre-signed URLs.
    """
    def _sync():
        try:
            client = _get_s3_client()
            cors_configuration = {
                'CORSRules': [
                    {
                        'AllowedHeaders': ['*'],
                        'AllowedMethods': ['GET', 'HEAD'],
                        'AllowedOrigins': ['*']
                    }
                ]
            }
            client.put_bucket_cors(
                Bucket=settings.MINIO_BUCKET,
                CORSConfiguration=cors_configuration
            )
            logger.info("MinIO bucket '%s' CORS policy updated.", settings.MINIO_BUCKET)
        except (BotoCoreError, ClientError) as e:
            logger.error("Failed to set MinIO CORS: %s", e)

    await asyncio.to_thread(_sync)


# ── Custom exception ──────────────────────────────────────────────────────────

class StorageError(Exception):
    """Base exception for all MinIO-related failures."""
