"""
app/schemas/reels_schema.py

FIXES vs previous version:
  1.  duration_seconds → duration_s. Blueprint §14 model field name.

  2.  tags: List[str] DELETED. Replaced with correct Blueprint §8.4 structure:
      tags: List[ReelTag] = JSONB array of tappable product overlay objects.
      Blueprint §8.4:
        "Tag object structure: {timestamp_ms: INT, listing_id: UUID,
         x_position: FLOAT, y_position: FLOAT}"
        "During playback: render tappable chips at correct timestamp_ms."
        "Tap → navigate to listing for instant booking or order."
      These are NOT hashtag strings — they are video timestamp-keyed
      overlay chips that link to bookable listings.

  3.  hashtags: List[str] ADDED as a separate field.
      Blueprint §14: reels have BOTH hashtags TEXT[] AND tags JSONB.

  4.  view_count → views_count. Blueprint §14 model field name.
      like_count → likes_count. Blueprint §14 model field name.

  5.  views_count, likes_count set as int (not Optional) — defaults to 0
      if None (older rows before counter columns existed).
"""
from __future__ import annotations

from typing import List, Literal, Optional
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict, field_validator


# ─── Tappable reel tag (Blueprint §8.4) ──────────────────────────────────────

class ReelTag(BaseModel):
    """
    Tappable product/service overlay chip displayed during reel playback.

    Blueprint §8.4:
      "Tag object structure: {timestamp_ms: INT, listing_id: UUID,
       x_position: FLOAT, y_position: FLOAT}"
      "Stored in reels.tags JSONB array."
      "During playback: render tappable chips at correct timestamp_ms."
      "Tap → navigate to listing for instant booking or order."

    NOT a hashtag — tapping this navigates the user to the tagged listing.
    """
    timestamp_ms: int   = Field(..., description="Video timestamp when chip appears (milliseconds)")
    listing_id:   UUID  = Field(..., description="UUID of the product/service/hotel being tagged")
    x_position:   float = Field(..., ge=0.0, le=1.0, description="Horizontal position (0–1)")
    y_position:   float = Field(..., ge=0.0, le=1.0, description="Vertical position (0–1)")


# ─── Reel create / update ─────────────────────────────────────────────────────

class ReelCreate(BaseModel):
    """
    Create a reel.
    Blueprint §8.4:
      - Upload via pre-signed S3 URL (POST /reels/upload-url first).
      - After S3 upload completes: call POST /reels/ with video_url.
      - Celery transcode_reel task queued server-side on creation.
    """
    video_url:     str
    thumbnail_url: str
    # Blueprint §14: field name is duration_s (not duration_seconds)
    # Blueprint §8.4: "up to 90 seconds"
    duration_s: int = Field(..., ge=1, le=90, description="Duration in seconds (max 90)")

    caption: Optional[str] = None

    # Blueprint §14 + §8.4: SEPARATE fields for hashtags vs tappable tags.
    # hashtags: plain string tags for discovery/search
    hashtags: List[str] = Field(default_factory=list, description="Hashtag strings (e.g. ['food', 'lagos'])")
    # tags: tappable product overlay chips at specific video timestamps
    tags:     List[ReelTag] = Field(default_factory=list, description="Tappable listing overlays [{timestamp_ms, listing_id, x_position, y_position}]")

    # Optional link to a business module category
    linked_entity_type: Optional[Literal[
        "hotel", "food", "service", "product", "health", "property", "ticket"
    ]] = None
    linked_entity_id: Optional[UUID] = None


class ReelUpdate(BaseModel):
    caption:   Optional[str]       = None
    hashtags:  Optional[List[str]] = None
    # Cannot update tappable tags after upload — requires re-upload
    is_active: Optional[bool]      = None


# ─── Reel output ─────────────────────────────────────────────────────────────

class ReelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            UUID
    business_id:   UUID
    video_url:     str
    thumbnail_url: str
    # Blueprint §14: duration_s (not duration_seconds)
    duration_s:    int

    caption:  Optional[str] = None
    hashtags: List[str]     = Field(default_factory=list)
    # Blueprint §8.4: tags JSONB [{timestamp_ms, listing_id, x_position, y_position}]
    tags:     List[ReelTag] = Field(default_factory=list)

    linked_entity_type: Optional[str] = None
    linked_entity_id:   Optional[UUID] = None

    # Blueprint §14: views_count / likes_count (not view_count / like_count)
    views_count:   int = 0
    likes_count:   int = 0
    comment_count: int = 0
    share_count:   int = 0

    is_active:   bool
    is_featured: bool
    created_at:  datetime

    # Viewer context — injected by service layer
    liked_by_me: bool = False

    @field_validator("views_count", "likes_count", "comment_count", "share_count", mode="before")
    @classmethod
    def coerce_none_to_zero(cls, v):
        return v if v is not None else 0

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v):
        """Accept raw dict list from JSONB and coerce to ReelTag objects."""
        if not v:
            return []
        result = []
        for item in v:
            if isinstance(item, dict):
                result.append(ReelTag(**item))
            elif isinstance(item, ReelTag):
                result.append(item)
        return result

    @field_validator("hashtags", mode="before")
    @classmethod
    def coerce_hashtags(cls, v):
        return v if v is not None else []


class ReelListOut(BaseModel):
    reels: List[ReelOut]
    total: int
    skip:  int
    limit: int


# ─── S3 pre-signed upload response ───────────────────────────────────────────

class ReelUploadUrlResponse(BaseModel):
    """
    Response from POST /reels/upload-url.
    Blueprint §8.4:
      "Upload: POST /api/v1/reels/upload → pre-signed S3/R2 URL returned.
       Client: uploads directly to object storage (not through backend).
       On upload completion: Celery task transcode_reel queued."
    """
    upload_url:  str  = Field(..., description="Pre-signed S3/R2 URL for direct upload")
    s3_key:      str  = Field(..., description="S3 object key — pass back as video_url on creation")
    expires_in:  int  = Field(..., description="URL expiry in seconds")
    content_type: str = "video/mp4"


# ─── Engagement schemas ───────────────────────────────────────────────────────

class ReelCommentCreate(BaseModel):
    body:      str            = Field(..., min_length=1, max_length=1000)
    parent_id: Optional[UUID] = None


class ReelCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:              UUID
    reel_id:         UUID
    user_id:         UUID
    body:            str
    parent_id:       Optional[UUID]
    user_name:       Optional[str] = None
    user_avatar_url: Optional[str] = None
    created_at:      datetime


class ReelCommentListOut(BaseModel):
    comments: List[ReelCommentOut]
    total:    int


class ReelViewCreate(BaseModel):
    watch_time_seconds: int  = Field(0, ge=0)
    completed:          bool = False