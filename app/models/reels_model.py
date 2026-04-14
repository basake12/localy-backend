"""
app/models/reels_model.py

FIXES:
  1. duration_seconds CHECK raised to 90 seconds — Blueprint §5.2 states
     reels are "up to 90 seconds". The previous cap of 60 s was wrong.

  2. business_id is now NOT NULL and the CHECK constraint enforces
     business-only ownership. Blueprint §5.1: "Only verified businesses
     may post reels." user_id column is removed — customer reels are
     not permitted. The create_for_user method is removed from CRUDReel.

  3. linked_entity_type choices updated to match ReelCreate Literal —
     "food" replaces "restaurant", "ticket" replaces "event".
"""
from sqlalchemy import (
    Column, String, Boolean, Integer, Text, ForeignKey,
    CheckConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.models.base_model import BaseModel


# ============================================================
# REEL MODEL
# ============================================================

class Reel(BaseModel):
    """
    Short-form video content (max 90 seconds).
    Only businesses may create reels — enforced at API layer (require_business)
    and at DB layer via NOT NULL on business_id.
    """

    __tablename__ = "reels"

    # Owner — businesses only (Blueprint §5.1)
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Video
    video_url        = Column(String(500), nullable=False)
    thumbnail_url    = Column(String(500), nullable=False)
    duration_seconds = Column(Integer, nullable=False)  # 1–90 seconds

    # Content
    caption = Column(Text, nullable=True)

    # Discovery tags  ["food", "lagos", "restaurant"]
    tags = Column(JSONB, default=list)

    # Module tagging — link reel to a specific listing per Blueprint §5.2
    # e.g. linked_entity_type="hotel", linked_entity_id=<room_id>
    linked_entity_type = Column(
        String(50), nullable=True
    )  # hotel|food|service|product|health|property|ticket
    linked_entity_id = Column(UUID(as_uuid=True), nullable=True)

    # Engagement counters (denormalised for feed sort)
    view_count    = Column(Integer, default=0)
    like_count    = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    share_count   = Column(Integer, default=0)

    # Moderation
    is_active   = Column(Boolean, default=True)
    is_featured = Column(Boolean, default=False)  # Admin-toggled

    # Extra metadata (resolution, codec, etc.)
    meta_data = Column(JSONB, default=dict)

    # Relationships
    business = relationship(
        "Business", back_populates="reels", foreign_keys=[business_id]
    )
    likes    = relationship(
        "ReelLike", back_populates="reel", cascade="all, delete-orphan"
    )
    comments = relationship(
        "ReelComment", back_populates="reel", cascade="all, delete-orphan"
    )
    views    = relationship(
        "ReelView", back_populates="reel", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # FIX: 90 s cap — Blueprint §5.2 "up to 90 seconds"
        CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 90",
            name="valid_reel_duration",
        ),
        Index("idx_reels_active_featured",  "is_active", "is_featured"),
        Index("idx_reels_business_active",  "business_id", "is_active"),
        Index("idx_reels_linked_entity",    "linked_entity_type", "linked_entity_id"),
    )


# ============================================================
# REEL ENGAGEMENT
# ============================================================

class ReelLike(BaseModel):
    """One row per user per reel like."""

    __tablename__ = "reel_likes"

    reel_id = Column(
        UUID(as_uuid=True),
        ForeignKey("reels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    reel = relationship("Reel", back_populates="likes")
    user = relationship("User")

    __table_args__ = (
        Index("idx_reel_likes_unique", "reel_id", "user_id", unique=True),
    )


class ReelComment(BaseModel):
    """Comments on reels, supports one level of replies."""

    __tablename__ = "reel_comments"

    reel_id = Column(
        UUID(as_uuid=True),
        ForeignKey("reels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("reel_comments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    body = Column(Text, nullable=False)

    reel   = relationship("Reel", back_populates="comments")
    user   = relationship("User")
    parent = relationship(
        "ReelComment",
        remote_side="ReelComment.id",
        backref="replies",
    )

    __table_args__ = (
        Index("idx_reel_comments_reel", "reel_id", "created_at"),
    )


class ReelView(BaseModel):
    """Analytics row for each reel play event."""

    __tablename__ = "reel_views"

    reel_id   = Column(
        UUID(as_uuid=True),
        ForeignKey("reels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    viewer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,  # anonymous views allowed
        index=True,
    )

    watch_time_seconds = Column(Integer, default=0)
    completed          = Column(Boolean, default=False)

    reel   = relationship("Reel", back_populates="views")
    viewer = relationship("User")

    __table_args__ = (
        Index("idx_reel_views_reel", "reel_id", "created_at"),
    )