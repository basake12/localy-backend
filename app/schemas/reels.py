from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID


# ============================================
# REEL SCHEMAS
# ============================================

class ReelCreate(BaseModel):
    video_url: str
    thumbnail_url: str
    duration_seconds: int = Field(..., ge=1, le=60)
    caption: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class ReelUpdate(BaseModel):
    caption: Optional[str] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None


class ReelOut(BaseModel):
    id: UUID
    business_id: UUID
    video_url: str
    thumbnail_url: str
    duration_seconds: int
    caption: Optional[str]
    tags: List[str]
    view_count: int
    like_count: int
    comment_count: int
    share_count: int
    is_active: bool
    is_featured: bool
    created_at: datetime

    # Viewer context
    liked_by_me: bool = False

    class Config:
        from_attributes = True


class ReelListOut(BaseModel):
    reels: List[ReelOut]
    total: int
    skip: int
    limit: int


# ============================================
# ENGAGEMENT SCHEMAS
# ============================================

class ReelCommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=1000)
    parent_id: Optional[UUID] = None


class ReelCommentOut(BaseModel):
    id: UUID
    reel_id: UUID
    user_id: UUID
    body: str
    parent_id: Optional[UUID]
    created_at: datetime

    class Config:
        from_attributes = True


class ReelCommentListOut(BaseModel):
    comments: List[ReelCommentOut]
    total: int


class ReelViewCreate(BaseModel):
    watch_time_seconds: int = Field(0, ge=0)
    completed: bool = False