"""
scripts/init_db.py

Creates the PostGIS / uuid-ossp extensions and all ORM-mapped tables.
Run once after deploying a fresh database:

    python scripts/init_db.py

For production, prefer Alembic migrations over this script.
"""
import sys
import os

# Allow running from the project root: python scripts/init_db.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import create_all_tables, check_db_connection
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Checking database connection…")
    if not await check_db_connection():
        logger.error("Cannot connect to the database. Check DATABASE_URL in .env")
        sys.exit(1)

    logger.info("Initialising database schema…")
    create_all_tables()
    logger.info("✅ Database initialised successfully.")


if __name__ == "__main__":
    asyncio.run(main())