"""
app/models/base_model.py

FIXES:
  1. id now uses server_default=sa_text("gen_random_uuid()") instead of
     Python-side default=uuid.uuid4. This ensures the DB column carries a
     genuine DEFAULT clause — raw SQL inserts and Alembic autogenerate both
     work correctly. Blueprint §14: "All UUIDs use gen_random_uuid()
     (PostgreSQL 13+)."

  2. created_at / updated_at use DateTime(timezone=True) — TIMESTAMPTZ in
     PostgreSQL. Blueprint §14 / §16.4 HARD RULE: "Always use
     datetime.now(timezone.utc). Never TIMESTAMP without zone."
"""
from sqlalchemy import Column, DateTime, func, text as sa_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declared_attr

from app.core.database import Base


class BaseModel(Base):
    """Abstract base — every ORM model inherits id, created_at, updated_at."""

    __abstract__ = True

    # Blueprint §14: DEFAULT gen_random_uuid() — server-side, not Python-side.
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
        index=True,
    )

    # Blueprint §14 / §16.4: ALL timestamps TIMESTAMPTZ (timezone=True).
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @declared_attr
    def __tablename__(cls) -> str:  # noqa: N805
        return cls.__name__.lower() + "s"

    def dict(self) -> dict:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}