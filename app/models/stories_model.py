"""
app/models/stories_model.py

FIX:
  duration_seconds CHECK corrected to 30 seconds — Blueprint §5.3 states
  stories are "up to 30 seconds". The previous cap of 60 s was wrong.
"""
import enum

from sqlalchemy import (
    Column, String, Boolean, Integer, ForeignKey, DateTime,
    CheckConstraint, Index, func,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.models.base_model import BaseModel


# ============================================================
# ENUMS
# ============================================================

class StoryTypeEnum(str, enum.Enum):
    IMAGE = "image"
    VIDEO = "video"
    TEXT  = "text"


# ============================================================
# STORY MODEL
# ============================================================

class Story(BaseModel):
    """
    24-hour ephemeral content from businesses.
    Auto-expires after settings.STORY_EXPIRE_HOURS (default 24 h).
    Business-only — enforced at API layer via require_business dependency.
    """

    __tablename__ = "stories"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    story_type = Column(String(20), nullable=False)  # image | video | text

    media_url     = Column(String(500), nullable=True)  # S3/MinIO URL
    thumbnail_url = Column(String(500), nullable=True)

    # Text-only story
    text_content     = Column(String(500), nullable=True)
    background_color = Column(String(7), default="#000000")  # hex

    # Duration (seconds) — used by the Flutter progress timer
    # FIX: Blueprint §5.3 "up to 30 seconds" — was incorrectly 60
    duration_seconds = Column(Integer, default=5)

    # Engagement
    view_count = Column(Integer, default=0)

    # Expiry
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_active  = Column(Boolean, default=True)

    # Call-to-action deep link
    cta_text = Column(String(50),  nullable=True)  # "Shop Now", "Book Table"
    cta_url  = Column(String(500), nullable=True)  # deep link or web URL

    # Extra metadata
    meta_data = Column(JSONB, default=dict)

    # Relationships
    business = relationship("Business", back_populates="stories")
    views    = relationship(
        "StoryView", back_populates="story", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # FIX: 30 s cap — Blueprint §5.3 "up to 30 seconds"
        CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 30",
            name="valid_story_duration",
        ),
        Index("idx_stories_active_expires",  "is_active", "expires_at"),
        Index("idx_stories_business_active", "business_id", "is_active"),
    )


# ============================================================
# STORY VIEW
# ============================================================

class StoryView(BaseModel):
    """One row per viewer per story. Used to derive view_count and 'seen' state."""

    __tablename__ = "story_views"

    story_id  = Column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    viewer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # server_default supplies a timezone-aware timestamp from the DB.
    viewed_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    story  = relationship("Story", back_populates="views")
    viewer = relationship("User")

    __table_args__ = (
        Index("idx_story_views_unique", "story_id", "viewer_id", unique=True),
    )