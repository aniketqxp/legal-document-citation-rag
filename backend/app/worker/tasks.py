"""Celery task stubs — implemented in Phase 3 (Document Ingestion Pipeline)."""

from app.worker.celery_app import celery_app


@celery_app.task(name="ingest_document", bind=True, max_retries=3)
def ingest_document(self, doc_id: str) -> dict:
    """Ingest a PDF document through the full pipeline.

    Steps (implemented in Phase 3):
      1. Load Document record, set status=processing
      2. Download PDF from MinIO
      3. Parse pages → List[ParsedPage]
      4. Semantic chunk → List[ChunkData]
      5. Generate embeddings (batched, via OpenRouter)
      6. Bulk insert DocumentChunk records (with tenant_id!)
      7. Set Document status=ready (or status=failed on error)

    Phase 1 stub — raises NotImplementedError until Phase 3.
    """
    raise NotImplementedError("Document ingestion pipeline not yet implemented — Phase 3")
