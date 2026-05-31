"""Initial data seeding script.

Creates the first superuser if none exists.
Run automatically by prestart.sh after `alembic upgrade head`.

Usage:
    python -m app.initial_data
"""

import asyncio
import logging

from app.core.db import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Creating initial data...")
    from app.services.storage import ensure_bucket_cors, ensure_bucket_exists
    await init_db()
    await ensure_bucket_exists()
    await ensure_bucket_cors()
    logger.info("Initial data created successfully.")


if __name__ == "__main__":
    asyncio.run(main())
