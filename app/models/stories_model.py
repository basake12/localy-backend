"""
app/models/stories_model.py

FIXES vs previous version:
  1. pinned_until TIMESTAMPTZ added — Blueprint §8.5:
     "Enterprise: can pin a story to profile for up to 7 days.
     (stories.pinned_until TIMESTAMPTZ, NULL for unpinned)"

  2. media_type CHECK corrected: ('photo','video') only.
     Blueprint §14: "media_type VARCHAR(10) NOT NULL CHECK (media_type IN ('photo','video'))."
     Previous enum had 'image' (wrong) and 'text' (not in blueprint — stories
     are photo or video only). StoryTypeEnum updated to match.

  3. tags JSONB added — Blueprint §8.5:
     "Product and service tagging (same tap-to-tag mechanic as reels)."
     Same structure as reels.tags:
       [{timestamp_ms, listing_id, x_position, y_position}]

  4. media_url is now NOT NULL — Blueprint §14: "media_url TEXT NOT NULL".
     Stories are photo or video — they always have a media URL.

  5. expires_at TIMESTAMPTZ NOT NULL — Blueprint §14 / §8.5:
     "Disappears after 24 hours. DB: stories.expires_at = created_at + 24 hours,
     using timezone-aware datetime."
     Set by the service layer: created_at + timedelta(hours=24) using
     datetime.now(timezone.utc) — NEVER datetime.utcnow().

  6. 30-second CHECK kept — Blueprint §8.5: "up to 30 seconds per story."

  7. story_views table: viewer_user_id column named per Blueprint §14 pattern
     "who viewed the story and when (story_views table: story_id,
     viewer_user_id, viewed_at)".
"""
import enum

from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    Text,
    ForeignKey,
    DateTime,
    CheckConstraint,
    Index,
    func,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class StoryTypeEnum(str, enum.Enum):
    """
    Blueprint §14: media_type IN ('photo','video').
    'photo' replaces the previous 'image'. 'text' removed — not in blueprint.
    """
    PHOTO = "photo"
    VIDEO = "video"


# ─── Story ────────────────────────────────────────────────────────────────────

class Story(BaseModel):
    """
    24-hour ephemeral business content. Blueprint §8.5 / §14.

    Blueprint §2 HARD RULE: Only VERIFIED businesses may post stories.
    Enforced at API layer via require_verified_business dependency.
    business_id NOT NULL enforces business-only ownership at DB layer.

    Expiry: expires_at = created_at + 24h (timezone-aware).
    Celery task prune_expired_stories (hourly) sets is_active=False when
    expires_at has passed.

    Enterprise feature: pinned_until — pin a story for up to 7 days.
    """
    __tablename__ = "stories"

    # Owner — businesses only (Blueprint §8.5 HARD RULE)
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Blueprint §14: media_type VARCHAR(10) NOT NULL CHECK (media_type IN ('photo','video'))
    media_type = Column(String(10), nullable=False)

    # Blueprint §14: media_url TEXT NOT NULL — stories always have a media URL
    media_url     = Column(Text, nullable=False)
    thumbnail_url = Column(String(500), nullable=True)

    # Blueprint §14 / §8.5: tags JSONB — TAPPABLE product/service timestamps
    # Same structure as reels.tags:
    #   [{
    #     "timestamp_ms": 2000,
    #     "listing_id": "uuid-...",
    #     "x_position": 0.3,
    #     "y_position": 0.7
    #   }]
    tags = Column(JSONB, default=list)

    # Duration for Flutter's progress bar timer (seconds)
    duration_seconds = Column(Integer, default=5, nullable=False)

    # Blueprint §14 / §8.5: expires_at TIMESTAMPTZ NOT NULL
    # Set by service layer: datetime.now(timezone.utc) + timedelta(hours=24)
    # NEVER datetime.utcnow() — produces naive datetimes (Blueprint §16.4 HARD RULE)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # Blueprint §8.5: pinned_until TIMESTAMPTZ — Enterprise plan only
    # NULL = not pinned. Set to created_at + timedelta(days=7) by service
    # when business is Enterprise tier. Prune task respects this.
    pinned_until = Column(DateTime(timezone=True), nullable=True)

    is_active  = Column(Boolean, default=True, nullable=False)
    view_count = Column(Integer, default=0, nullable=False)

    # Call-to-action deep link — e.g. "Shop Now" → product listing
    cta_text = Column(String(50),  nullable=True)
    cta_url  = Column(String(500), nullable=True)

    meta_data = Column(JSONB, default=dict)

    # ── Relationships ─────────────────────────────────────────────────────────
    business = relationship("Business", back_populates="stories")
    views    = relationship(
        "StoryView", back_populates="story", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Blueprint §14: media_type CHECK
        CheckConstraint(
            "media_type IN ('photo','video')",
            name="valid_story_media_type",
        ),
        # Blueprint §8.5: up to 30 seconds per story
        CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 30",
            name="valid_story_duration",
        ),
        Index("idx_stories_active_expires",  "is_active", "expires_at"),
        Index("idx_stories_business_active", "business_id", "is_active"),
        # Index to efficiently find pinned stories for the prune task
        Index("idx_stories_pinned_until",    "pinned_until"),
    )

    def __repr__(self) -> str:
        return f"<Story business={self.business_id} type={self.media_type} expires={self.expires_at}>"


# ─── Story View ───────────────────────────────────────────────────────────────

class StoryView(BaseModel):
    """
    One row per viewer per story.

    Blueprint §8.5: "Viewer list visible to business: who viewed the story
    and when (story_views table: story_id, viewer_user_id, viewed_at)."

    viewed_at uses server_default for timezone-aware insertion without
    requiring Python-side datetime generation on every row.
    """
    __tablename__ = "story_views"

    story_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Blueprint §14: viewer_user_id (exact name from blueprint spec)
    viewer_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Blueprint §8.5: "who viewed the story and when"
    viewed_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    story  = relationship("Story", back_populates="views")
    viewer = relationship("User", foreign_keys=[viewer_user_id])

    __table_args__ = (
        # One view row per viewer per story (deduplication)
        Index("idx_story_views_unique", "story_id", "viewer_user_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<StoryView story={self.story_id} viewer={self.viewer_user_id}>"