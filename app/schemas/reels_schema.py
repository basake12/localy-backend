"""
app/schemas/reels_schema.py

FIX:
  duration_seconds Field raised to le=90 — Blueprint §5.2 "up to 90 seconds".
  The previous le=60 was incorrect.
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Literal
from datetime import datetime
from uuid import UUID


# ============================================================
# REEL SCHEMAS
# ============================================================

class ReelCreate(BaseModel):
    video_url:        str
    thumbnail_url:    str
    # FIX: Blueprint §5.2 — reels up to 90 seconds (was le=60)
    duration_seconds: int = Field(..., ge=1, le=90)
    caption:          Optional[str] = None
    tags:             List[str] = Field(default_factory=list)
    # Blueprint §5.2 — link reel to a specific listing for contextual visibility
    linked_entity_type: Optional[Literal[
        "hotel", "food", "service", "product", "health", "property", "ticket"
    ]] = None
    linked_entity_id: Optional[UUID] = None


class ReelUpdate(BaseModel):
    caption:   Optional[str]       = None
    tags:      Optional[List[str]] = None
    is_active: Optional[bool]      = None


class ReelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               UUID
    business_id:      UUID
    video_url:        str
    thumbnail_url:    str
    duration_seconds: int
    caption:          Optional[str]
    tags:             List[str]
    linked_entity_type: Optional[str]
    linked_entity_id:   Optional[UUID]
    view_count:       int
    like_count:       int
    comment_count:    int
    share_count:      int
    is_active:        bool
    is_featured:      bool
    created_at:       datetime

    # Viewer context — injected by service layer, not on the ORM object
    liked_by_me: bool = False


class ReelListOut(BaseModel):
    reels: List[ReelOut]
    total: int
    skip:  int
    limit: int


# ============================================================
# ENGAGEMENT SCHEMAS
# ============================================================

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