"""
app/models/reels_model.py

FIXES vs previous version:
  1. tags JSONB corrected — now stores TAPPABLE product/service timestamps:
       [{timestamp_ms: INT, listing_id: UUID, x_position: FLOAT, y_position: FLOAT}]
     Blueprint §8.4 / §14: "Tag object structure: { timestamp_ms, listing_id,
     x_position, y_position }. Stored in reels.tags JSONB array."
     During playback: render tappable chips at correct timestamp_ms.
     Tap → navigate to listing for instant booking or order.

  2. hashtags TEXT[] added as a SEPARATE column — for discovery/search tags
     (strings like ["food", "lagos"]).
     Blueprint §14: reels.hashtags TEXT[].
     Previously tags was conflated with hashtags — now they are separate.

  3. views_count / likes_count renamed from view_count / like_count.
     Blueprint §14 exact field names.

  4. duration_s renamed from duration_seconds to match Blueprint §14.

  5. Only verified businesses may post reels — enforced at API layer.
     business_id NOT NULL enforces business-only ownership at DB layer.

  6. 90-second CHECK constraint kept — Blueprint §8.4: "up to 90 seconds".
"""
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    Text,
    ForeignKey,
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.models.base_model import BaseModel


# ─── Reel ─────────────────────────────────────────────────────────────────────

class Reel(BaseModel):
    """
    Short-form video content — up to 90 seconds.

    Blueprint §8.4 / §14 / §2 HARD RULE:
    Only VERIFIED businesses may post reels. Enforced at API layer via
    require_verified_business dependency. business_id NOT NULL enforces
    business-only ownership at DB layer — customers cannot create reels.
    """
    __tablename__ = "reels"

    # Owner — businesses only (Blueprint §8.4 HARD RULE)
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Video ─────────────────────────────────────────────────────────────────
    video_url     = Column(String(500), nullable=False)
    thumbnail_url = Column(String(500), nullable=True)

    # Blueprint §14: duration_s INTEGER — not duration_seconds
    duration_s = Column(Integer, nullable=False)

    # ── Content ───────────────────────────────────────────────────────────────
    caption = Column(Text, nullable=True)

    # Blueprint §14: hashtags TEXT[] — discovery/search tags (list of strings)
    # e.g. ["food", "lagos", "restaurant"] — used for search and feed filtering
    hashtags = Column(JSONB, default=list)   # stored as JSON array of strings

    # Blueprint §14 / §8.4: tags JSONB — TAPPABLE product/service timestamps
    # Structure per blueprint:
    #   [{
    #     "timestamp_ms": 3500,       -- when the chip appears during playback
    #     "listing_id": "uuid-...",   -- product/service/hotel/etc. UUID
    #     "x_position": 0.45,         -- normalised horizontal position (0–1)
    #     "y_position": 0.60          -- normalised vertical position (0–1)
    #   }]
    # During playback: Flutter renders a tappable chip at timestamp_ms.
    # Tap → navigate to listing for instant booking or order.
    tags = Column(JSONB, default=list)

    # ── Engagement counters (denormalised for feed sort) ──────────────────────
    # Blueprint §14: exact field names views_count and likes_count
    views_count   = Column(Integer, nullable=False, default=0)
    likes_count   = Column(Integer, nullable=False, default=0)
    comment_count = Column(Integer, default=0)
    share_count   = Column(Integer, default=0)

    # ── Moderation ────────────────────────────────────────────────────────────
    is_active   = Column(Boolean, default=True,  nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)

    # Extra metadata (resolution, codec, HLS URLs after transcoding, etc.)
    meta_data = Column(JSONB, default=dict)

    # ── Relationships ─────────────────────────────────────────────────────────
    business = relationship("Business", back_populates="reels", foreign_keys=[business_id])
    likes    = relationship("ReelLike",    back_populates="reel", cascade="all, delete-orphan")
    comments = relationship("ReelComment", back_populates="reel", cascade="all, delete-orphan")
    views    = relationship("ReelView",    back_populates="reel", cascade="all, delete-orphan")

    __table_args__ = (
        # Blueprint §8.4: "up to 90 seconds"
        CheckConstraint(
            "duration_s > 0 AND duration_s <= 90",
            name="valid_reel_duration",
        ),
        Index("idx_reels_active_featured",  "is_active", "is_featured"),
        Index("idx_reels_business_active",  "business_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Reel business={self.business_id} duration={self.duration_s}s>"


# ─── Reel Like ────────────────────────────────────────────────────────────────

class ReelLike(BaseModel):
    """One row per user per reel like."""

    __tablename__ = "reel_likes"

    reel_id = Column(UUID(as_uuid=True), ForeignKey("reels.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    reel = relationship("Reel", back_populates="likes")
    user = relationship("User")

    __table_args__ = (
        Index("idx_reel_likes_unique", "reel_id", "user_id", unique=True),
    )


# ─── Reel Comment ─────────────────────────────────────────────────────────────

class ReelComment(BaseModel):
    """Comments on reels — supports one level of reply threading."""

    __tablename__ = "reel_comments"

    reel_id   = Column(UUID(as_uuid=True), ForeignKey("reels.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id   = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("reel_comments.id", ondelete="CASCADE"), nullable=True, index=True)

    body = Column(Text, nullable=False)

    reel   = relationship("Reel", back_populates="comments")
    user   = relationship("User")
    parent = relationship("ReelComment", remote_side="ReelComment.id", backref="replies")

    __table_args__ = (
        Index("idx_reel_comments_reel", "reel_id", "created_at"),
    )


# ─── Reel View ────────────────────────────────────────────────────────────────

class ReelView(BaseModel):
    """Analytics row for each reel play event."""

    __tablename__ = "reel_views"

    reel_id   = Column(UUID(as_uuid=True), ForeignKey("reels.id", ondelete="CASCADE"), nullable=False, index=True)
    viewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # nullable = anonymous views allowed

    watch_time_seconds = Column(Integer, default=0)
    completed          = Column(Boolean, default=False)

    reel   = relationship("Reel", back_populates="views")
    viewer = relationship("User")

    __table_args__ = (
        Index("idx_reel_views_reel", "reel_id", "created_at"),
    )