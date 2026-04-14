"""
app/api/v1/reels.py

FIX:
  get_reels_feed() now accepts `lat`, `lng`, `radius_meters` query params
  instead of `lga_id`. Blueprint §3: "Location model — Radius-based
  (default 5 km) — no LGA dependency." The Flutter client sends the
  device GPS coordinates; the backend filters via PostGIS ST_DWithin.

  Flutter sends:
    GET /api/v1/reels/feed?lat=6.5244&lng=3.3792&radius_meters=5000&skip=0&limit=10
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    get_current_user_optional,
    require_business,
    get_pagination_params,
)
from app.models.user_model import User
from app.schemas.reels_schema import (
    ReelCreate, ReelUpdate, ReelOut, ReelListOut,
    ReelCommentCreate, ReelCommentListOut, ReelViewCreate,
)
from app.services.reel_service import reel_service
from app.core.constants import DEFAULT_RADIUS_METERS, MAX_RADIUS_METERS

router = APIRouter()


# ── Feed ──────────────────────────────────────────────────────────────────────

@router.get("/feed", response_model=ReelListOut, summary="Get reels feed")
def get_reels_feed(
    # FIX: GPS coordinates replace lga_id — Blueprint: radius-only, no LGA
    lat:                Optional[float] = Query(None, description="Device latitude"),
    lng:                Optional[float] = Query(None, description="Device longitude"),
    radius_meters:      int             = Query(DEFAULT_RADIUS_METERS, ge=1000, le=MAX_RADIUS_METERS),
    tags:               Optional[str]   = Query(None, description="Comma-separated tags"),
    linked_entity_type: Optional[str]   = Query(None),
    pagination:         dict            = Depends(get_pagination_params),
    db:                 Session         = Depends(get_db),
    user:               Optional[User]  = Depends(get_current_user_optional),
):
    """
    Paginated reels feed — radius-filtered, subscription-ranked.
    Blueprint §5.4: Enterprise > Pro > Starter > Free (organic).
    Flutter sends device GPS coordinates; ST_DWithin filters by radius.
    """
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    return reel_service.get_feed(
        db,
        viewer_id=user.id if user else None,
        lat=lat,
        lng=lng,
        radius_meters=radius_meters,
        tags=tag_list,
        linked_entity_type=linked_entity_type,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post(
    "/businesses/{business_id}",
    response_model=ReelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a reel (business only)",
)
def create_reel(
    business_id: UUID,
    body:        ReelCreate,
    db:          Session = Depends(get_db),
    user:        User    = Depends(require_business),
):
    return reel_service.create_reel(
        db, business_id=business_id, obj_in=body, user=user
    )


@router.get("/{reel_id}", response_model=ReelOut, summary="Get single reel")
def get_reel(
    reel_id: UUID,
    db:      Session        = Depends(get_db),
    user:    Optional[User] = Depends(get_current_user_optional),
):
    result = reel_service.get_reel(
        db, reel_id=reel_id, viewer_id=user.id if user else None
    )
    return ReelOut.model_validate(result["reel"]).model_copy(
        update={"liked_by_me": result["liked_by_me"]}
    )


@router.put("/{reel_id}", response_model=ReelOut, summary="Update reel")
def update_reel(
    reel_id: UUID,
    body:    ReelUpdate,
    db:      Session = Depends(get_db),
    user:    User    = Depends(require_business),
):
    return reel_service.update_reel(
        db, reel_id=reel_id, obj_in=body, user=user
    )


@router.delete("/{reel_id}", status_code=status.HTTP_200_OK, summary="Delete reel")
def delete_reel(
    reel_id: UUID,
    db:      Session = Depends(get_db),
    user:    User    = Depends(require_business),
):
    reel_service.delete_reel(db, reel_id=reel_id, user=user)
    return {"success": True, "data": {"message": "Reel deleted"}}


# ── Engagement ────────────────────────────────────────────────────────────────

@router.post("/{reel_id}/like", status_code=status.HTTP_200_OK)
def toggle_reel_like(
    reel_id: UUID,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_active_user),
):
    result = reel_service.toggle_like(db, reel_id=reel_id, user_id=user.id)
    return {"success": True, "data": result}


@router.post("/{reel_id}/comments", status_code=status.HTTP_201_CREATED)
def create_reel_comment(
    reel_id: UUID,
    body:    ReelCommentCreate,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_active_user),
):
    comment = reel_service.create_comment(
        db, reel_id=reel_id, user_id=user.id, obj_in=body
    )
    return {"success": True, "data": comment}


@router.get("/{reel_id}/comments", response_model=ReelCommentListOut)
def get_reel_comments(
    reel_id:    UUID,
    pagination: dict    = Depends(get_pagination_params),
    db:         Session = Depends(get_db),
):
    return reel_service.get_comments(
        db,
        reel_id=reel_id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


@router.post("/{reel_id}/view", status_code=status.HTTP_200_OK)
def record_reel_view(
    reel_id: UUID,
    body:    ReelViewCreate,
    db:      Session        = Depends(get_db),
    user:    Optional[User] = Depends(get_current_user_optional),
):
    result = reel_service.record_view(
        db,
        reel_id=reel_id,
        obj_in=body,
        viewer_id=user.id if user else None,
    )
    return {"success": True, "data": result}