from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
from uuid import UUID


# ============================================
# STORY SCHEMAS
# ============================================

class StoryCreate(BaseModel):
    story_type: str = Field(..., description="image | video | text")
    media_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    text_content: Optional[str] = Field(None, max_length=500)
    background_color: str = "#000000"
    duration_seconds: int = Field(5, ge=1, le=60)
    cta_text: Optional[str] = Field(None, max_length=50)
    cta_url: Optional[str] = None


class StoryUpdate(BaseModel):
    is_active: Optional[bool] = None
    cta_text: Optional[str] = Field(None, max_length=50)
    cta_url: Optional[str] = None


class StoryOut(BaseModel):
    id: UUID
    business_id: UUID
    story_type: str
    media_url: Optional[str]
    thumbnail_url: Optional[str]
    text_content: Optional[str]
    background_color: str
    duration_seconds: int
    view_count: int
    expires_at: datetime
    is_active: bool
    cta_text: Optional[str]
    cta_url: Optional[str]
    created_at: datetime

    # Viewer context
    viewed_by_me: bool = False

    class Config:
        from_attributes = True


class StoryFeedItem(BaseModel):
    """Groups stories by business for feed display."""
    business_id: UUID
    business_name: str
    business_logo: Optional[str]
    story_count: int
    latest_story_at: datetime
    has_unseen: bool  # Has stories not viewed by current user
    stories: List[StoryOut]


class StoryFeedOut(BaseModel):
    items: List[StoryFeedItem]
    total: int


class StoryViewCreate(BaseModel):
    story_id: UUID