from sqlalchemy import Column, String, Boolean, Integer, Text, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
import enum

from app.models.base import BaseModel


# ============================================
# REEL MODEL
# ============================================

class Reel(BaseModel):
    """
    Short-form video content (max 60s) from businesses.
    Similar to Instagram Reels / TikTok.
    """

    __tablename__ = "reels"

    business_id = Column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False,
                         index=True)

    # Video
    video_url = Column(String(500), nullable=False)
    thumbnail_url = Column(String(500), nullable=False)
    duration_seconds = Column(Integer, nullable=False)

    # Content
    caption = Column(Text, nullable=True)

    # Tags (for discovery)
    tags = Column(JSONB, default=list)  # ["food", "lagos", "restaurant"]

    # Engagement (denormalized for feed sorting)
    view_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)

    # Moderation
    is_active = Column(Boolean, default=True)
    is_featured = Column(Boolean, default=False)  # Admin can feature top content

    # Metadata
    meta_data = Column(JSONB, default=dict)  # resolution, codec, etc.

    # Relationships
    business = relationship("Business", back_populates="reels")
    likes = relationship("ReelLike", back_populates="reel", cascade="all, delete-orphan")
    comments = relationship("ReelComment", back_populates="reel", cascade="all, delete-orphan")
    views = relationship("ReelView", back_populates="reel", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint('duration_seconds > 0 AND duration_seconds <= 60', name='valid_reel_duration'),
        Index('idx_reels_active_featured', 'is_active', 'is_featured'),
        Index('idx_reels_business_active', 'business_id', 'is_active'),
    )


# ============================================
# REEL ENGAGEMENT
# ============================================

class ReelLike(BaseModel):
    """One row per user per reel like."""

    __tablename__ = "reel_likes"

    reel_id = Column(UUID(as_uuid=True), ForeignKey("reels.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    reel = relationship("Reel", back_populates="likes")
    user = relationship("User")

    __table_args__ = (
        Index('idx_reel_likes_unique', 'reel_id', 'user_id', unique=True),
    )


class ReelComment(BaseModel):
    """Comments on reels."""

    __tablename__ = "reel_comments"

    reel_id = Column(UUID(as_uuid=True), ForeignKey("reels.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    body = Column(Text, nullable=False)

    # Nested replies (optional)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("reel_comments.id", ondelete="CASCADE"), nullable=True,
                       index=True)

    reel = relationship("Reel", back_populates="comments")
    user = relationship("User")
    parent = relationship("ReelComment", remote_side="ReelComment.id", backref="replies")

    __table_args__ = (
        Index('idx_reel_comments_reel', 'reel_id', 'created_at'),
    )


class ReelView(BaseModel):
    """Track reel views (for analytics)."""

    __tablename__ = "reel_views"

    reel_id = Column(UUID(as_uuid=True), ForeignKey("reels.id", ondelete="CASCADE"), nullable=False, index=True)
    viewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True,
                       index=True)  # nullable for anonymous

    # Watch time (for engagement metrics)
    watch_time_seconds = Column(Integer, default=0)
    completed = Column(Boolean, default=False)  # watched to end

    reel = relationship("Reel", back_populates="views")
    viewer = relationship("User")

    __table_args__ = (
        Index('idx_reel_views_reel', 'reel_id', 'created_at'),
    )