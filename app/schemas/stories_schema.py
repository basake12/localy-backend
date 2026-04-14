"""
app/schemas/stories_schema.py

FIX:
  StoryOut now includes business_name and business_avatar_url.
  StoriesViewerScreen renders these in the story header (business name +
  avatar). Without them the header was always blank — the Story ORM model
  has no business_name column; the value must be injected by the CRUD
  feed query (which joins Business) and carried through to the schema.
  Both fields are Optional so single-story GET endpoints still validate.
"""
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List, Literal
from datetime import datetime
from uuid import UUID


# ============================================================
# STORY SCHEMAS
# ============================================================

class StoryCreate(BaseModel):
    story_type:       Literal["image", "video", "text"]
    media_url:        Optional[str] = None
    thumbnail_url:    Optional[str] = None
    text_content:     Optional[str] = Field(None, max_length=500)
    background_color: str           = "#000000"
    duration_seconds: int           = Field(5, ge=1, le=30)  # Blueprint §5.3: 30 s max
    cta_text:         Optional[str] = Field(None, max_length=50)
    cta_url:          Optional[str] = None


class StoryUpdate(BaseModel):
    is_active: Optional[bool] = None
    cta_text:  Optional[str]  = Field(None, max_length=50)
    cta_url:   Optional[str]  = None


class StoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               UUID
    business_id:      UUID
    story_type:       str
    media_url:        Optional[str] = None
    thumbnail_url:    Optional[str] = None
    text_content:     Optional[str] = None
    background_color: Optional[str] = None
    duration_seconds: int
    view_count:       int
    expires_at:       datetime
    is_active:        bool
    cta_text:         Optional[str] = None
    cta_url:          Optional[str] = None
    created_at:       datetime

    # FIX: business info injected by feed CRUD — needed by StoriesViewerScreen
    # header. Not a real DB column; set as a dynamic attribute on the ORM
    # object by stories_crud.get_feed() after the Business join.
    business_name:       Optional[str] = None
    business_avatar_url: Optional[str] = None

    # Viewer context — injected by service, not persisted on the ORM object
    viewed_by_me: bool = False

    @field_validator("background_color", mode="before")
    @classmethod
    def coerce_bg_color(cls, v: Optional[str]) -> str:
        """Coerce NULL from legacy DB rows to a safe default."""
        return v if v is not None else "#000000"


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