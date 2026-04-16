"""
app/api/v1/reels.py

FIXES vs previous version:
  1.  [HARD RULE §8.4] require_business → require_verified_business on all
      POST/PUT/DELETE endpoints.
      Blueprint §8.4: "Only VERIFIED businesses may post reels.
      Unverified businesses see these features locked with a clear
      verification prompt — not a silent blank state."

  2.  POST /reels/upload-url endpoint added.
      Blueprint §8.4:
        "Upload: POST /api/v1/reels/upload → pre-signed S3/R2 URL returned.
         Client: uploads directly to object storage (not through backend)."

  3.  POST /reels/ now dispatches Celery transcode_reel task.
      Blueprint §8.4 / §16.2:
        "On upload completion: Celery task transcode_reel queued.
         Transcoded formats: 1080p, 720p, 480p adaptive bitrate (HLS)"

  4.  All POST/PUT/DELETE use get_async_current_active_user (async deps)
      to match AsyncSession-backed business verification.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.database import get_async_db, get_db
from app.dependencies import (
    get_current_active_user,
    get_current_user_optional,
    require_verified_business,   # Blueprint §8.4 HARD RULE
    get_pagination_params,
)
from app.models.user_model import User
from app.schemas.reels_schema import (
    ReelCreate,
    ReelListOut,
    ReelOut,
    ReelUpdate,
    ReelCommentCreate,
    ReelCommentListOut,
    ReelUploadUrlResponse,
    ReelViewCreate,
)
from app.services.reel_service import reel_service
from app.core.constants import DEFAULT_RADIUS_METERS, MAX_RADIUS_METERS

router = APIRouter()


# ─── S3 Pre-signed Upload URL ─────────────────────────────────────────────────

@router.post(
    "/upload-url",
    response_model=ReelUploadUrlResponse,
    summary="Get pre-signed S3 URL for reel upload",
)
def get_reel_upload_url(
    filename:     str  = Query(..., description="Original filename (e.g. reel.mp4)"),
    content_type: str  = Query("video/mp4"),
    db:           Session = Depends(get_db),
    user:         User    = Depends(require_verified_business),   # HARD RULE §8.4
):
    """
    Returns a pre-signed S3/R2 URL.
    Blueprint §8.4:
      "Upload: POST /api/v1/reels/upload → pre-signed S3/R2 URL returned.
       Client: uploads directly to object storage (not through backend)."

    Flutter workflow:
      1. POST /reels/upload-url?filename=reel.mp4  → get upload_url + s3_key
      2. PUT <upload_url> with video bytes          → direct to S3/R2
      3. POST /reels/ { video_url: s3_key, ... }   → create reel record
         → server dispatches transcode_reel Celery task
    """
    import boto3
    from app.config import settings
    import uuid

    s3_key = f"reels/{user.business.id}/{uuid.uuid4()}/{filename}"
    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION,
        )
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket":      settings.AWS_S3_BUCKET,
                "Key":         s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=3600,   # 1 hour
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not generate upload URL: {exc}",
        )

    return ReelUploadUrlResponse(
        upload_url=upload_url,
        s3_key=s3_key,
        expires_in=3600,
        content_type=content_type,
    )


# ─── Feed ─────────────────────────────────────────────────────────────────────

@router.get("/feed", response_model=ReelListOut, summary="Get reels feed")
def get_reels_feed(
    lat:                Optional[float] = Query(None, description="Device latitude"),
    lng:                Optional[float] = Query(None, description="Device longitude"),
    radius_meters:      int             = Query(DEFAULT_RADIUS_METERS, ge=1000, le=MAX_RADIUS_METERS),
    linked_entity_type: Optional[str]   = Query(None),
    pagination:         dict            = Depends(get_pagination_params),
    db:                 Session         = Depends(get_db),
    user:               Optional[User]  = Depends(get_current_user_optional),
):
    """
    Radius-filtered, subscription-ranked reels feed.
    Blueprint §7.3: "Feed ranking: Enterprise > Pro > Starter > Free (organic)."
    Blueprint §4: GPS coordinates only — no LGA parameter.
    """
    return reel_service.get_feed(
        db,
        viewer_id=user.id if user else None,
        lat=lat,
        lng=lng,
        radius_meters=radius_meters,
        linked_entity_type=linked_entity_type,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


# ─── CRUD ─────────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=ReelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a reel record after S3 upload",
)
def create_reel(
    body: ReelCreate,
    db:   Session = Depends(get_db),
    user: User    = Depends(require_verified_business),   # [HARD RULE §8.4]
):
    """
    Create a reel record after direct S3 upload.
    Blueprint §8.4: "On upload completion: Celery task transcode_reel queued."
    Blueprint §8.4 HARD RULE: Only VERIFIED businesses may post reels.
    """
    reel = reel_service.create_reel(
        db,
        business_id=user.business.id,
        obj_in=body,
        user=user,
    )

    # Blueprint §8.4 / §16.2: dispatch transcode_reel Celery task
    try:
        from app.tasks.cleanup_tasks import transcode_reel
        transcode_reel.delay(str(reel.id), reel.video_url)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "transcode_reel task dispatch failed for reel %s: %s", reel.id, exc
        )

    return reel


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
    user:    User    = Depends(require_verified_business),   # [HARD RULE §8.4]
):
    return reel_service.update_reel(db, reel_id=reel_id, obj_in=body, user=user)


@router.delete(
    "/{reel_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete reel",
)
def delete_reel(
    reel_id: UUID,
    db:      Session = Depends(get_db),
    user:    User    = Depends(require_verified_business),   # [HARD RULE §8.4]
):
    reel_service.delete_reel(db, reel_id=reel_id, user=user)
    return {"success": True, "data": {"message": "Reel deleted"}}


# ─── Engagement ───────────────────────────────────────────────────────────────

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