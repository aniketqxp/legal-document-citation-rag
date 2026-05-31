"""Eval-only database access and an isolated eval tenant.

The harness reuses the application's real models, CRUD, and retrieval service,
but it owns its database *connection* and a dedicated *tenant* so that:

  • It can run from the host against the Docker-exposed Postgres on localhost,
    without depending on the in-container ``POSTGRES_SERVER=postgres`` value.
    Override the whole URL with ``EVAL_DATABASE_URL`` if your setup differs.

  • Everything it writes lives under one fixed, recognisable tenant id and a
    non-loginable eval user. Tenant isolation (the app's core invariant) means
    eval data is invisible to real tenants and trivially resettable, so the
    harness never pollutes or reads real customer documents.
"""

from __future__ import annotations

import os
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.tenant import Tenant
from app.models.user import User

# Fixed identifiers (valid hex; "e7a1" ~ "eval") so eval data is stable and
# easy to spot/clean in the database.
EVAL_TENANT_ID = uuid.UUID("e7a10000-0000-4000-8000-000000000001")
EVAL_USER_ID = uuid.UUID("e7a10000-0000-4000-8000-000000000002")
EVAL_USER_EMAIL = "eval-harness@local.invalid"


def eval_database_url() -> str:
    """Async SQLAlchemy URL for the eval database.

    Defaults to the app's credentials but forces ``localhost`` (Docker publishes
    Postgres on ``localhost:5432``). Set ``EVAL_DATABASE_URL`` to override.
    """
    override = os.getenv("EVAL_DATABASE_URL")
    if override:
        return override
    host = os.getenv("EVAL_POSTGRES_HOST", "localhost")
    return (
        f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{host}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )


def make_engine() -> AsyncEngine:
    """Create the eval async engine.

    ``NullPool`` matches the Celery worker's pattern: short-lived scripts should
    not hold a connection pool open. Callers should ``await engine.dispose()``
    when done so connections close cleanly (avoids noisy shutdown warnings).
    """
    return create_async_engine(eval_database_url(), poolclass=NullPool)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to ``engine``."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def ensure_eval_principals(session: AsyncSession) -> None:
    """Create the eval Tenant and eval User if they do not already exist.

    The eval user is inactive and has a non-verifiable password hash, so it can
    never be used to authenticate — it exists only to satisfy the
    ``document.uploaded_by_id`` foreign key.
    """
    tenant = await session.get(Tenant, EVAL_TENANT_ID)
    if tenant is None:
        session.add(
            Tenant(id=EVAL_TENANT_ID, name="Evaluation Harness", is_active=False)
        )

    existing_user = await session.scalar(
        select(User).where(User.id == EVAL_USER_ID)
    )
    if existing_user is None:
        session.add(
            User(
                id=EVAL_USER_ID,
                tenant_id=EVAL_TENANT_ID,
                email=EVAL_USER_EMAIL,
                full_name="Evaluation Harness",
                is_active=False,
                is_superuser=False,
                hashed_password="!eval-not-loginable",
            )
        )
    await session.commit()


async def reset_eval_corpus(session: AsyncSession) -> None:
    """Delete all documents and chunks owned by the eval tenant.

    Makes corpus building idempotent: re-running ``corpus.py`` always starts
    from a clean slate instead of stacking duplicate chunks. Chunks are deleted
    first to respect the ``document_chunk.document_id`` foreign key.
    """
    await session.execute(
        text("DELETE FROM document_chunk WHERE tenant_id = :tid"),
        {"tid": str(EVAL_TENANT_ID)},
    )
    await session.execute(
        text("DELETE FROM document WHERE tenant_id = :tid"),
        {"tid": str(EVAL_TENANT_ID)},
    )
    await session.commit()
