"""
stories_crud.py — /stories/*

Business stories with 24h expiry.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_active_user, get_current_user_optional, require_business, get_pagination_params
from app.models.user_model import User
from app.schemas.stories_schema import StoryCreate, StoryUpdate, StoryOut, StoryFeedOut
from app.services.story_service import story_service


router = APIRouter()


# ===========================================================================
# STORY CRUD
# ===========================================================================

@router.post(
    "/businesses/{business_id}/stories",
    response_model=StoryOut,
    summary="Create a story",
)
async def create_story(
    business_id: UUID,
    body: StoryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_business),
):
    """Business owner creates a 24-hour story."""
    story = story_service.create_story(db, business_id=business_id, obj_in=body, user=user)
    return story


@router.get(
    "/stories/{story_id}",
    response_model=StoryOut,
    summary="Get single story",
)
async def get_story(
    story_id: UUID,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Get a single story with viewer context."""
    result = story_service.get_story(db, story_id=story_id, viewer_id=user.id if user else None)
    story = result["story"]
    story.viewed_by_me = result["viewed_by_me"]
    return story


@router.put(
    "/stories/{story_id}",
    response_model=StoryOut,
    summary="Update story",
)
async def update_story(
    story_id: UUID,
    body: StoryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_business),
):
    """Update a story (owner only)."""
    updated = story_service.update_story(db, story_id=story_id, obj_in=body, user=user)
    return updated


@router.delete(
    "/stories/{story_id}",
    summary="Delete story",
)
async def delete_story(
    story_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_business),
):
    """Delete a story (owner only)."""
    story_service.delete_story(db, story_id=story_id, user=user)
    return {"success": True, "data": {"message": "Story deleted"}}


# ===========================================================================
# FEED
# ===========================================================================

@router.get(
    "/stories/feed",
    response_model=StoryFeedOut,
    summary="Get stories feed",
)
async def get_stories_feed(
    pagination: dict = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Returns stories grouped by business.
    Businesses with unseen stories appear first.
    """
    result = story_service.get_feed(
        db,
        viewer_id=user.id if user else None,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return result


# ===========================================================================
# ENGAGEMENT
# ===========================================================================

@router.post(
    "/stories/{story_id}/view",
    summary="Record story view",
)
async def record_story_view(
    story_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Record that the user viewed this story."""
    result = story_service.record_view(db, story_id=story_id, viewer_id=user.id)
    return {"success": True, "data": result}