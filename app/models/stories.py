from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, DateTime, CheckConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
import enum
from datetime import datetime, timedelta

from app.models.base import BaseModel


# ============================================
# ENUMS
# ============================================

class StoryTypeEnum(str, enum.Enum):
    IMAGE = "image"
    VIDEO = "video"
    TEXT = "text"


# ============================================
# STORY MODEL
# ============================================

class Story(BaseModel):
    """
    24-hour ephemeral content from businesses.
    Auto-expires after STORY_EXPIRE_HOURS (default 24h).
    """

    __tablename__ = "stories"

    business_id = Column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False,
                         index=True)

    story_type = Column(String(20), nullable=False)  # image | video | text
    media_url = Column(String(500), nullable=True)  # S3/MinIO URL for image/video
    thumbnail_url = Column(String(500), nullable=True)

    # Text story
    text_content = Column(String(500), nullable=True)
    background_color = Column(String(7), default="#000000")  # hex color for text stories

    # Duration in seconds (for video stories)
    duration_seconds = Column(Integer, default=5)

    # Engagement
    view_count = Column(Integer, default=0)

    # Expiry
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, default=True)

    # Link attachment (CTA)
    cta_text = Column(String(50), nullable=True)  # "Shop Now", "Book Table", etc.
    cta_url = Column(String(500), nullable=True)  # Deep link or web URL

    # Metadata
    meta_data = Column(JSONB, default=dict)

    # Relationships
    business = relationship("Business", back_populates="stories")
    views = relationship("StoryView", back_populates="story", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint('duration_seconds > 0 AND duration_seconds <= 60', name='valid_duration'),
        Index('idx_stories_active_expires', 'is_active', 'expires_at'),
        Index('idx_stories_business_active', 'business_id', 'is_active'),
    )


# ============================================
# STORY VIEW (user engagement tracking)
# ============================================

class StoryView(BaseModel):
    """One row per user per story view. Used to calculate view_count and 'viewed by you'."""

    __tablename__ = "story_views"

    story_id = Column(UUID(as_uuid=True), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False, index=True)
    viewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    viewed_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    story = relationship("Story", back_populates="views")
    viewer = relationship("User")

    __table_args__ = (
        Index('idx_story_views_unique', 'story_id', 'viewer_id', unique=True),
    )