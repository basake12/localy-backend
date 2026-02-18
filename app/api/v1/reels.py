"""
reels.py — /reels/*

Short-form video content.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_active_user, get_current_user_optional, require_business, get_pagination_params
from app.models.user import User
from app.schemas.reels import (
    ReelCreate, ReelUpdate, ReelOut, ReelListOut,
    ReelCommentCreate, ReelCommentListOut, ReelViewCreate,
)
from app.services.reel_service import reel_service


router = APIRouter()


# ===========================================================================
# REEL CRUD
# ===========================================================================

@router.post(
    "/businesses/{business_id}/reels",
    response_model=ReelOut,
    summary="Create a reel",
)
async def create_reel(
    business_id: UUID,
    body: ReelCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_business),
):
    """Business owner creates a reel."""
    reel = reel_service.create_reel(db, business_id=business_id, obj_in=body, user=user)
    return reel


@router.get(
    "/reels/{reel_id}",
    response_model=ReelOut,
    summary="Get single reel",
)
async def get_reel(
    reel_id: UUID,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Get a single reel with viewer context."""
    result = reel_service.get_reel(db, reel_id=reel_id, viewer_id=user.id if user else None)
    reel = result["reel"]
    reel.liked_by_me = result["liked_by_me"]
    return reel


@router.put(
    "/reels/{reel_id}",
    response_model=ReelOut,
    summary="Update reel",
)
async def update_reel(
    reel_id: UUID,
    body: ReelUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_business),
):
    """Update a reel (owner only)."""
    updated = reel_service.update_reel(db, reel_id=reel_id, obj_in=body, user=user)
    return updated


@router.delete(
    "/reels/{reel_id}",
    summary="Delete reel",
)
async def delete_reel(
    reel_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_business),
):
    """Delete a reel (owner only)."""
    reel_service.delete_reel(db, reel_id=reel_id, user=user)
    return {"success": True, "data": {"message": "Reel deleted"}}


# ===========================================================================
# FEED
# ===========================================================================

@router.get(
    "/reels/feed",
    response_model=ReelListOut,
    summary="Get reels feed",
)
async def get_reels_feed(
    tags: Optional[str] = Query(None, description="Comma-separated tags to filter by"),
    pagination: dict = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Returns paginated reels feed.
    Optional tag filtering.
    """
    tag_list = tags.split(',') if tags else None
    result = reel_service.get_feed(
        db,
        viewer_id=user.id if user else None,
        tags=tag_list,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return result


# ===========================================================================
# ENGAGEMENT
# ===========================================================================

@router.post(
    "/reels/{reel_id}/like",
    summary="Toggle like on reel",
)
async def toggle_reel_like(
    reel_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Toggle like on a reel."""
    result = reel_service.toggle_like(db, reel_id=reel_id, user_id=user.id)
    return {"success": True, "data": result}


@router.post(
    "/reels/{reel_id}/comments",
    summary="Comment on reel",
)
async def create_reel_comment(
    reel_id: UUID,
    body: ReelCommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Create a comment on a reel."""
    comment = reel_service.create_comment(db, reel_id=reel_id, user_id=user.id, obj_in=body)
    return {"success": True, "data": comment}


@router.get(
    "/reels/{reel_id}/comments",
    response_model=ReelCommentListOut,
    summary="Get reel comments",
)
async def get_reel_comments(
    reel_id: UUID,
    pagination: dict = Depends(get_pagination_params),
    db: Session = Depends(get_db),
):
    """Get comments for a reel."""
    result = reel_service.get_comments(
        db,
        reel_id=reel_id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return result


@router.post(
    "/reels/{reel_id}/view",
    summary="Record reel view",
)
async def record_reel_view(
    reel_id: UUID,
    body: ReelViewCreate,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Record a reel view (can be anonymous)."""
    result = reel_service.record_view(
        db,
        reel_id=reel_id,
        obj_in=body,
        viewer_id=user.id if user else None,
    )
    return {"success": True, "data": result}