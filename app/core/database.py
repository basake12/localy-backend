"""
app/core/database.py

FIXES:
  1. Async engine is now PRIMARY. All FastAPI route handlers use
     get_async_db(). Sync engine kept only for Alembic + startup utility.

  2. URL conversion uses SQLAlchemy make_url() instead of fragile
     string.replace() — handles postgresql://, postgres://, psycopg2://, etc.

  3. get_async_db() typed as AsyncGenerator[AsyncSession, None] with
     session.rollback() on exception — prevents connection pool poisoning.

  4. Blueprint §16.4 HARD RULE: all timestamps TIMESTAMPTZ — enforced in
     BaseModel; no action needed here.

  5. Blueprint §16.7: create_wallet_sync() callers use get_db() (sync).
     Async handlers always use get_async_db().
"""
import asyncio
import logging
from typing import AsyncGenerator, Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)

# ─── Declarative Base ─────────────────────────────────────────────────────────

Base = declarative_base()


# ─── Async Engine (PRIMARY — all FastAPI handlers use this) ───────────────────

def _build_async_url(raw_url: str) -> str:
    """
    Safely convert a sync PostgreSQL URL to the asyncpg driver variant.
    Works with postgresql://, postgres://, postgresql+psycopg2://, etc.
    """
    parsed = make_url(raw_url)
    if parsed.drivername in (
        "postgresql",
        "postgres",
        "postgresql+psycopg2",
        "postgresql+psycopg",
    ):
        parsed = parsed.set(drivername="postgresql+asyncpg")
    return parsed.render_as_string(hide_password=False)


_ASYNC_DATABASE_URL = _build_async_url(str(settings.DATABASE_URL))

async_engine = create_async_engine(
    _ASYNC_DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DATABASE_ECHO,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency — yields an async DB session.

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_async_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ─── Sync Engine (Alembic migrations + create_all_tables startup ONLY) ────────

_sync_kwargs: dict = {
    "pool_size":     settings.DATABASE_POOL_SIZE,
    "max_overflow":  settings.DATABASE_MAX_OVERFLOW,
    "pool_pre_ping": True,
    "pool_recycle":  3600,
    "echo":          settings.DATABASE_ECHO,
}

if getattr(settings, "APP_ENV", "") == "testing":
    _sync_kwargs["poolclass"] = NullPool

engine = create_engine(str(settings.DATABASE_URL), **_sync_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Sync FastAPI dependency — use ONLY where async is not possible.
    Prefer get_async_db() in all route handlers.

    Per Blueprint §16.7: use create_wallet_sync() with a sync Session
    if called from a synchronous handler context.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── Extension + table bootstrap (called once at app startup) ─────────────────

def _ensure_extensions(conn) -> None:
    """Create PostGIS and uuid-ossp if not present (requires superuser or rds_superuser)."""
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
    conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))


def create_all_tables() -> None:
    """
    Idempotent startup bootstrap:
      1. Ensure PostGIS + uuid-ossp extensions exist.
      2. Create all ORM-mapped tables (no-op if they already exist).
    """
    logger.info("Ensuring PostGIS / uuid-ossp extensions…")
    with engine.connect() as conn:
        _ensure_extensions(conn)
        conn.commit()

    logger.info("Creating database tables…")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully.")


def drop_all_tables() -> None:
    """Drop ALL tables — use with extreme caution (test environments only)."""
    logger.warning("Dropping ALL database tables…")
    Base.metadata.drop_all(bind=engine)
    logger.info("All tables dropped.")


# ─── Health check ─────────────────────────────────────────────────────────────

async def check_db_connection() -> bool:
    """Async-safe DB health check. Runs blocking call in a thread pool."""

    def _sync_ping() -> bool:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection healthy.")
            return True
        except Exception as exc:
            logger.error(f"Database connection failed: {exc}")
            return False

    return await asyncio.to_thread(_sync_ping)