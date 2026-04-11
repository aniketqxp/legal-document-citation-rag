"""Alembic environment configuration with async support.

Uses asyncpg driver to run migrations asynchronously, consistent with the
main app's database engine.

Key design choices:
  - DB URL loaded from app.core.config.settings (not hardcoded in alembic.ini)
  - All models imported via `app.models` to register with SQLModel.metadata
  - run_sync() bridges the async connection to Alembic's sync context manager
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

# ── Import all models to register them with SQLModel.metadata ─────────────────
# This is what makes `alembic revision --autogenerate` work.
from app.models import *  # noqa: F401, F403
from app.core.config import settings

# Alembic Config object (gives access to values in alembic.ini)
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# All table metadata — Alembic diffs this against the live DB
target_metadata = SQLModel.metadata


# ── Offline migrations (generate SQL without a live DB) ───────────────────────

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection needed)."""
    context.configure(
        url=settings.SQLALCHEMY_DATABASE_URI,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online migrations (async, connects to live DB) ────────────────────────────

def _do_run_migrations(connection) -> None:
    """Synchronous callback executed inside async connection.run_sync()."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Create async engine, connect, and run migrations via run_sync."""
    connectable = create_async_engine(settings.SQLALCHEMY_DATABASE_URI)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migration mode."""
    asyncio.run(_run_async_migrations())


# ── Dispatch ──────────────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
