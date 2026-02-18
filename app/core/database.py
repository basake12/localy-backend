from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
from geoalchemy2 import Geography
from typing import Generator
import logging

from app.config import settings

logger = logging.getLogger(__name__)

# Database engine configuration
engine_kwargs = {
    "pool_size": settings.DATABASE_POOL_SIZE,
    "max_overflow": settings.DATABASE_MAX_OVERFLOW,
    "pool_pre_ping": True,  # Enable connection health checks
    "pool_recycle": 3600,  # Recycle connections after 1 hour
    "echo": settings.DATABASE_ECHO,
}

# Use NullPool for testing to avoid connection issues
if settings.APP_ENV == "testing":
    engine_kwargs["poolclass"] = NullPool

# Create engine
engine = create_engine(
    str(settings.DATABASE_URL),
    **engine_kwargs
)

# SessionLocal class for database sessions
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base class for all models
Base = declarative_base()


# PostGIS extension check
@event.listens_for(engine, "connect")
def receive_connect(dbapi_conn, connection_record):
    """Enable PostGIS extension on connection"""
    with dbapi_conn.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";")
        dbapi_conn.commit()


def get_db() -> Generator[Session, None, None]:
    """
    Dependency to get database session.

    Usage:
        @app.get("/users")
        def get_users(db: Session = Depends(get_db)):
            return db.query(User).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all_tables():
    """Create all database tables"""
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully")


def drop_all_tables():
    """Drop all database tables (use with caution!)"""
    logger.warning("Dropping all database tables...")
    Base.metadata.drop_all(bind=engine)
    logger.info("Database tables dropped")


async def check_db_connection() -> bool:
    """Check if database connection is healthy"""
    try:
        db = SessionLocal()
        # Use text() wrapper for raw SQL in SQLAlchemy 2.0
        db.execute(text("SELECT 1"))
        db.close()
        logger.info("Database connection successful")
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False