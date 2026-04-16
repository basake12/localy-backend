"""
app/api/v1/stories.py

FIXES vs previous version:
  1.  [HARD RULE §8.5] require_business → require_verified_business on all
      POST/PUT/DELETE endpoints.
      Blueprint §8.5: "Only VERIFIED businesses may post stories."
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    get_current_user_optional,
    require_verified_business,   # Blueprint §8.5 HARD RULE
    get_pagination_params,
)
from app.models.user_model import User
from app.schemas.stories_schema import (
    StoryCreate,
    StoryFeedOut,
    StoryOut,
    StoryUpdate,
)
from app.services.story_service import story_service
from app.core.constants import DEFAULT_RADIUS_METERS, MAX_RADIUS_METERS

router = APIRouter()


# ─── Feed ─────────────────────────────────────────────────────────────────────

@router.get("/feed", response_model=StoryFeedOut, summary="Get stories feed")
def get_stories_feed(
    lat:           Optional[float] = Query(None, description="Device latitude"),
    lng:           Optional[float] = Query(None, description="Device longitude"),
    radius_meters: int             = Query(DEFAULT_RADIUS_METERS, ge=1000, le=MAX_RADIUS_METERS),
    pagination:    dict            = Depends(get_pagination_params),
    db:            Session         = Depends(get_db),
    user:          Optional[User]  = Depends(get_current_user_optional),
):
    """
    Returns active stories grouped by business, filtered by GPS radius.
    Blueprint §7.3: businesses with unseen stories first, then subscription tier.
    Blueprint §4: GPS coordinates only — no LGA parameter.
    """
    return story_service.get_feed(
        db,
        viewer_id=user.id if user else None,
        lat=lat,
        lng=lng,
        radius_meters=radius_meters,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


# ─── CRUD ─────────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=StoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a story (verified business only)",
)
def create_story(
    body: StoryCreate,
    db:   Session = Depends(get_db),
    user: User    = Depends(require_verified_business),   # [HARD RULE §8.5]
):
    """
    Create a story.
    Blueprint §8.5 HARD RULE: Only VERIFIED businesses may post stories.
    Blueprint §8.5: expires_at = created_at + 24 hours (stored server-side).
    Blueprint §8.5: pinned_until — Enterprise only, set via update endpoint.
    """
    return story_service.create_story(
        db,
        business_id=user.business.id,
        obj_in=body,
        user=user,
    )


@router.get("/{story_id}", response_model=StoryOut, summary="Get single story")
def get_story(
    story_id: UUID,
    db:       Session        = Depends(get_db),
    user:     Optional[User] = Depends(get_current_user_optional),
):
    result = story_service.get_story(
        db, story_id=story_id, viewer_id=user.id if user else None
    )
    return StoryOut.model_validate(result["story"]).model_copy(
        update={"viewed_by_me": result["viewed_by_me"]}
    )


@router.get("/{story_id}/viewers", summary="Get story viewers (business owner only)")
def get_story_viewers(
    story_id:   UUID,
    pagination: dict    = Depends(get_pagination_params),
    db:         Session = Depends(get_db),
    user:       User    = Depends(require_verified_business),
):
    """
    Blueprint §8.5: "Viewer list visible to business: who viewed the story and when.
    (story_views table: story_id, viewer_user_id, viewed_at)"
    """
    return story_service.get_viewers(
        db,
        story_id=story_id,
        user=user,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


@router.put("/{story_id}", response_model=StoryOut, summary="Update story")
def update_story(
    story_id: UUID,
    body:     StoryUpdate,
    db:       Session = Depends(get_db),
    user:     User    = Depends(require_verified_business),   # [HARD RULE §8.5]
):
    return story_service.update_story(db, story_id=story_id, obj_in=body, user=user)


@router.delete(
    "/{story_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete story",
)
def delete_story(
    story_id: UUID,
    db:       Session = Depends(get_db),
    user:     User    = Depends(require_verified_business),   # [HARD RULE §8.5]
):
    story_service.delete_story(db, story_id=story_id, user=user)
    return {"success": True, "data": {"message": "Story deleted"}}


# ─── Engagement ───────────────────────────────────────────────────────────────

@router.post("/{story_id}/view", status_code=status.HTTP_200_OK)
def record_story_view(
    story_id: UUID,
    db:       Session = Depends(get_db),
    user:     User    = Depends(get_current_active_user),
):
    """
    Record that this user has viewed the story.
    Blueprint §8.5: idempotent — returns True on first view, False on repeat.
    """
    result = story_service.record_view(db, story_id=story_id, viewer_id=user.id)
    return {"success": True, "data": result}