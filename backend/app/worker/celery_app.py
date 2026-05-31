"""Celery application factory.

Phase 1 stub — Celery is configured but tasks are defined in Phase 3
(Document Ingestion Pipeline).
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "legal-document-citation-rag",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,      # only ack after task completes (safer for ingestion)
    worker_prefetch_multiplier=1,  # one task at a time per worker process
)
