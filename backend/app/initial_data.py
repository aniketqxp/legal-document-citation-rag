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
    await init_db()
    logger.info("Initial data created successfully.")


if __name__ == "__main__":
    asyncio.run(main())
