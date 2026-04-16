"""
app/schemas/stories_schema.py

FIXES vs previous version:
  1.  story_type → media_type.
      Values: 'photo' | 'video' ONLY.
      Blueprint §14: "media_type VARCHAR(10) NOT NULL CHECK (media_type IN ('photo','video'))"
      'image' and 'text' are wrong values — removed.

  2.  tags JSONB added for product/service tagging.
      Blueprint §8.5: "Product and service tagging (same tap-to-tag mechanic
      as reels)" — stories support tappable listing overlays.

  3.  pinned_until: Optional[datetime] added.
      Blueprint §14 / §8.5: "Enterprise: can pin a story to profile for
      up to 7 days (stories.pinned_until TIMESTAMPTZ, NULL for unpinned)."
      Only Enterprise accounts can set this. Enforced at service layer.

  4.  view_count → views_count. Blueprint §14 field name.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.reels_schema import ReelTag   # reuse same tappable tag structure


# ─── Story create / update ────────────────────────────────────────────────────

class StoryCreate(BaseModel):
    """
    Create a story.
    Blueprint §8.5: "Photo or video — up to 30 seconds per story."
    Blueprint §14: media_type IN ('photo','video').
    """
    # Blueprint §14 HARD RULE: media_type IN ('photo','video') only.
    # 'image' is wrong — blueprint uses 'photo'. 'text' is not supported.
    media_type: Literal["photo", "video"] = Field(
        ..., description="'photo' or 'video' (Blueprint §14)"
    )
    media_url:  str = Field(..., description="S3/R2 URL of the photo or video")
    thumbnail_url: Optional[str] = None

    # Blueprint §8.5: "up to 30 seconds per story"
    duration_s: int = Field(5, ge=1, le=30, description="Display duration in seconds")

    # Blueprint §8.5: "Product and service tagging (same tap-to-tag mechanic as reels)"
    tags: List[ReelTag] = Field(
        default_factory=list,
        description="Tappable listing overlays [{timestamp_ms, listing_id, x_position, y_position}]",
    )

    # Optional call-to-action
    cta_text: Optional[str] = Field(None, max_length=50)
    cta_url:  Optional[str] = None


class StoryUpdate(BaseModel):
    is_active: Optional[bool] = None
    cta_text:  Optional[str]  = Field(None, max_length=50)
    cta_url:   Optional[str]  = None


# ─── Story output ─────────────────────────────────────────────────────────────

class StoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:          UUID
    business_id: UUID

    # Blueprint §14: media_type IN ('photo','video')
    media_type:    str
    media_url:     str
    thumbnail_url: Optional[str] = None

    duration_s: int

    # Blueprint §8.5: tappable product tags
    tags: List[ReelTag] = Field(default_factory=list)

    # Blueprint §14 / §8.5: views_count (not view_count)
    views_count: int = 0

    expires_at: datetime
    is_active:  bool

    # Blueprint §14 / §8.5: Enterprise only — pin story for up to 7 days
    pinned_until: Optional[datetime] = None

    cta_text: Optional[str] = None
    cta_url:  Optional[str] = None
    created_at: datetime

    # Business display info — injected by CRUD feed query after Business join
    business_name:       Optional[str] = None
    business_avatar_url: Optional[str] = None

    # Viewer context — injected by service layer
    viewed_by_me: bool = False

    @field_validator("views_count", mode="before")
    @classmethod
    def coerce_none_to_zero(cls, v):
        return v if v is not None else 0

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v):
        if not v:
            return []
        result = []
        for item in v:
            if isinstance(item, dict):
                result.append(ReelTag(**item))
            elif isinstance(item, ReelTag):
                result.append(item)
        return result


# ─── Feed ─────────────────────────────────────────────────────────────────────

class StoryFeedItem(BaseModel):
    """Stories grouped by business for the horizontal story-ring row."""
    business_id:     UUID
    business_name:   str
    business_logo:   Optional[str] = None
    story_count:     int
    latest_story_at: datetime
    has_unseen:      bool
    stories:         List[StoryOut]


class StoryFeedOut(BaseModel):
    items: List[StoryFeedItem]
    total: int


class StoryViewCreate(BaseModel):
    story_id: UUID