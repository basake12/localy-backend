"""
app/api/v1/stories.py

FIX:
  get_stories_feed() now accepts `lat`, `lng`, `radius_meters` instead of
  `lga`. Blueprint §3: radius-only, no LGA dependency.
  Flutter sends device GPS coordinates.

  Flutter sends:
    GET /api/v1/stories/feed?lat=6.5244&lng=3.3792&radius_meters=5000&skip=0&limit=20
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    get_current_user_optional,
    require_business,
    get_pagination_params,
)
from app.models.user_model import User
from app.schemas.stories_schema import (
    StoryCreate, StoryUpdate, StoryOut, StoryFeedOut,
)
from app.services.story_service import story_service
from app.core.constants import DEFAULT_RADIUS_METERS, MAX_RADIUS_METERS

router = APIRouter()


# ── Feed ──────────────────────────────────────────────────────────────────────

@router.get("/feed", response_model=StoryFeedOut, summary="Get stories feed")
def get_stories_feed(
    # FIX: GPS coordinates replace `lga` — Blueprint: radius-only, no LGA
    lat:           Optional[float] = Query(None, description="Device latitude"),
    lng:           Optional[float] = Query(None, description="Device longitude"),
    radius_meters: int             = Query(DEFAULT_RADIUS_METERS, ge=1000, le=MAX_RADIUS_METERS),
    pagination:    dict            = Depends(get_pagination_params),
    db:            Session         = Depends(get_db),
    user:          Optional[User]  = Depends(get_current_user_optional),
):
    """
    Returns active stories grouped by business, filtered by GPS radius.
    Businesses with unseen stories appear first, then by subscription tier.
    Blueprint §5.4: Enterprise > Pro > Starter > Free.
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


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post(
    "/businesses/{business_id}",
    response_model=StoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a story (business only)",
)
def create_story(
    business_id: UUID,
    body:        StoryCreate,
    db:          Session = Depends(get_db),
    user:        User    = Depends(require_business),
):
    return story_service.create_story(
        db, business_id=business_id, obj_in=body, user=user
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


@router.put("/{story_id}", response_model=StoryOut, summary="Update story")
def update_story(
    story_id: UUID,
    body:     StoryUpdate,
    db:       Session = Depends(get_db),
    user:     User    = Depends(require_business),
):
    return story_service.update_story(
        db, story_id=story_id, obj_in=body, user=user
    )


@router.delete("/{story_id}", status_code=status.HTTP_200_OK, summary="Delete story")
def delete_story(
    story_id: UUID,
    db:       Session = Depends(get_db),
    user:     User    = Depends(require_business),
):
    story_service.delete_story(db, story_id=story_id, user=user)
    return {"success": True, "data": {"message": "Story deleted"}}


# ── Engagement ────────────────────────────────────────────────────────────────

@router.post("/{story_id}/view", status_code=status.HTTP_200_OK)
def record_story_view(
    story_id: UUID,
    db:       Session = Depends(get_db),
    user:     User    = Depends(get_current_active_user),
):
    result = story_service.record_view(
        db, story_id=story_id, viewer_id=user.id
    )
    return {"success": True, "data": result}