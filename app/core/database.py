"""
app/core/database.py

Synchronous SQLAlchemy engine + session factory.

All CRUD layers in the project use Session (sync), so we keep the engine
sync for consistency.  The two places in main.py that need to run DB work
inside an async lifespan do so via asyncio.to_thread() — see main.py.
"""
import asyncio
import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import NullPool
from typing import Generator

from app.config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# ENGINE
# ─────────────────────────────────────────────

engine_kwargs: dict = {
    "pool_size":     settings.DATABASE_POOL_SIZE,
    "max_overflow":  settings.DATABASE_MAX_OVERFLOW,
    "pool_pre_ping": True,   # validate connections before use
    "pool_recycle":  3600,   # recycle after 1 hour
    "echo":          settings.DATABASE_ECHO,
}

if settings.APP_ENV == "testing":
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(str(settings.DATABASE_URL), **engine_kwargs)

# ─────────────────────────────────────────────
# SESSION
# ─────────────────────────────────────────────

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# ─────────────────────────────────────────────
# BASE
# ─────────────────────────────────────────────

Base = declarative_base()

# ─────────────────────────────────────────────
# POSTGIS / UUID EXTENSION
# FIX: run once at startup (create_all) rather than on every connection.
# The @event listener approach executes on every new connection from the
# pool, which is slow and can fail if the role lacks SUPERUSER.
# Extensions are now created in create_all_tables() below.
# ─────────────────────────────────────────────


def _ensure_extensions(conn) -> None:
    """Create PostGIS and uuid-ossp extensions if they don't already exist."""
    conn.execute(text('CREATE EXTENSION IF NOT EXISTS postgis;'))
    conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))


# ─────────────────────────────────────────────
# DEPENDENCY
# ─────────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency — yields a database session.

    Usage:
        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────

def create_all_tables() -> None:
    """Create extensions then all ORM-mapped tables."""
    logger.info("Ensuring PostGIS / uuid-ossp extensions exist…")
    with engine.connect() as conn:
        _ensure_extensions(conn)
        conn.commit()

    logger.info("Creating database tables…")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully.")


def drop_all_tables() -> None:
    """Drop all database tables — use with extreme caution!"""
    logger.warning("Dropping ALL database tables…")
    Base.metadata.drop_all(bind=engine)
    logger.info("Database tables dropped.")


def _sync_check_db_connection() -> bool:
    """Synchronous health check — used inside asyncio.to_thread()."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection successful.")
        return True
    except Exception as exc:
        logger.error(f"Database connection failed: {exc}")
        return False


async def check_db_connection() -> bool:
    """
    Async-safe health check.

    Runs the blocking SQLAlchemy call in a thread pool so the event
    loop is never blocked.
    """
    return await asyncio.to_thread(_sync_check_db_connection)

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Async engine � replace postgresql:// with postgresql+asyncpg://
_async_url = str(settings.DATABASE_URL).replace("postgresql://", "postgresql+asyncpg://").replace("postgresql+psycopg2://", "postgresql+asyncpg://")
async_engine = create_async_engine(_async_url, echo=False)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


async def get_async_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
