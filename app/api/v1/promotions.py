"""
app/api/v1/promotions.py

REST endpoints for the Promotions feature.

Public (any authenticated user):
  GET  /promotions/active            — list all currently active public promotions
  GET  /promotions/{id}              — get a single promotion detail
  GET  /promotions/my/streak         — user's active streak progresses
  GET  /promotions/my/redemptions    — user's promotion redemption history

Admin-only:
  POST   /promotions                 — create promotion
  PUT    /promotions/{id}            — update promotion
  DELETE /promotions/{id}            — delete (non-active only)
  GET    /promotions/admin/all       — list all promotions (any status)
  GET    /promotions/admin/{id}/analytics — redemption analytics
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from app.core.database import get_async_db
from app.dependencies import get_async_current_active_user, require_admin
from app.models.user_model import User
from app.models.promotions_model import PromotionType, PromotionStatus
from app.schemas.promotions_schema import (
    PromotionCreate,
    PromotionUpdate,
    PromotionOut,
    PromotionListOut,
    PromotionRedemptionListOut,
    StreakProgressListOut,
    ActiveReferralMultiplier,
    PromotionAnalytics,
)
from app.schemas.common_schema import SuccessResponse
from app.services.promotions_service import promotions_service
from app.crud.promotions_crud import promotion_redemption_crud

router = APIRouter(tags=["Promotions"])


# ─── Public ───────────────────────────────────────────────────────────────────

@router.get(
    "/active",
    response_model=SuccessResponse[PromotionListOut],
    summary="List all currently active public promotions",
)
async def get_active_promotions(
    db:   AsyncSession = Depends(get_async_db),
    user: User         = Depends(get_async_current_active_user),
):
    promotions = await promotions_service.get_active_promotions(db)
    # Mobile endpoint — admin never reaches here (Blueprint §2.2 HARD RULE).
    # Always filter to public-only promotions.
    promotions = [p for p in promotions if p.is_public]

    return {
        "success": True,
        "data": PromotionListOut(
            promotions=promotions,
            total=len(promotions),
            page=1,
            page_size=len(promotions),
        ),
    }


@router.get(
    "/referral-multiplier",
    response_model=SuccessResponse[ActiveReferralMultiplier],
    summary="Get current referral reward multiplier",
)
async def get_referral_multiplier(
    db:   AsyncSession = Depends(get_async_db),
    user: User         = Depends(get_async_current_active_user),
):
    """
    Returns the current referral multiplier.
    1.0 = standard ₦1,000. 2.0 = double-referral event active (₦2,000).
    """
    result = await promotions_service.get_referral_multiplier(db)
    return {"success": True, "data": result}


@router.get(
    "/my/streak",
    response_model=SuccessResponse[StreakProgressListOut],
    summary="Get my active streak progresses",
)
async def get_my_streak_progress(
    db:   AsyncSession = Depends(get_async_db),
    user: User         = Depends(get_async_current_active_user),
):
    progresses = await promotions_service.get_my_streak_progresses(
        db, user_id=user.id
    )
    return {
        "success": True,
        "data": StreakProgressListOut(progresses=progresses),
    }


@router.get(
    "/my/redemptions",
    response_model=SuccessResponse[PromotionRedemptionListOut],
    summary="Get my promotion redemption history",
)
async def get_my_redemptions(
    skip:  int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db:    AsyncSession = Depends(get_async_db),
    user:  User         = Depends(get_async_current_active_user),
):
    redemptions, total = await promotion_redemption_crud.list_for_user(
        db, user_id=user.id, skip=skip, limit=limit
    )
    return {
        "success": True,
        "data": PromotionRedemptionListOut(
            redemptions=redemptions,
            total=total,
            page=skip // limit + 1,
            page_size=limit,
        ),
    }


@router.get(
    "/{promotion_id}",
    response_model=SuccessResponse[PromotionOut],
    summary="Get a single promotion",
)
async def get_promotion(
    promotion_id: UUID,
    db:           AsyncSession = Depends(get_async_db),
    user:         User         = Depends(get_async_current_active_user),
):
    promotion = await promotions_service.get_promotion(db, promotion_id=promotion_id)
    return {"success": True, "data": promotion}


# ─── Admin ────────────────────────────────────────────────────────────────────

@router.get(
    "/admin/all",
    response_model=SuccessResponse[PromotionListOut],
    summary="[Admin] List all promotions (any status)",
)
async def admin_list_promotions(
    status:         Optional[PromotionStatus] = Query(None),
    promotion_type: Optional[PromotionType]   = Query(None),
    skip:  int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db:    AsyncSession = Depends(get_async_db),
    admin: User         = Depends(require_admin),
):
    promotions, total = await promotions_service.list_promotions(
        db,
        status=status,
        promotion_type=promotion_type,
        is_public_only=False,
        skip=skip,
        limit=limit,
    )
    return {
        "success": True,
        "data": PromotionListOut(
            promotions=promotions,
            total=total,
            page=skip // limit + 1,
            page_size=limit,
        ),
    }


@router.post(
    "",
    response_model=SuccessResponse[PromotionOut],
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Create a new promotion",
)
async def create_promotion(
    payload: PromotionCreate,
    db:      AsyncSession = Depends(get_async_db),
    admin:   User         = Depends(require_admin),
):
    promotion = await promotions_service.create_promotion(
        db, payload=payload, admin_id=admin.id
    )
    return {"success": True, "data": promotion}


@router.put(
    "/{promotion_id}",
    response_model=SuccessResponse[PromotionOut],
    summary="[Admin] Update a promotion",
)
async def update_promotion(
    promotion_id: UUID,
    payload:      PromotionUpdate,
    db:           AsyncSession = Depends(get_async_db),
    admin:        User         = Depends(require_admin),
):
    promotion = await promotions_service.update_promotion(
        db, promotion_id=promotion_id, payload=payload
    )
    return {"success": True, "data": promotion}


@router.delete(
    "/{promotion_id}",
    response_model=SuccessResponse[dict],
    summary="[Admin] Delete a non-active promotion",
)
async def delete_promotion(
    promotion_id: UUID,
    db:           AsyncSession = Depends(get_async_db),
    admin:        User         = Depends(require_admin),
):
    await promotions_service.delete_promotion(db, promotion_id=promotion_id)
    return {"success": True, "data": {"deleted": True}}


@router.get(
    "/admin/{promotion_id}/analytics",
    response_model=SuccessResponse[PromotionAnalytics],
    summary="[Admin] Promotion redemption analytics",
)
async def get_promotion_analytics(
    promotion_id: UUID,
    db:           AsyncSession = Depends(get_async_db),
    admin:        User         = Depends(require_admin),
):
    analytics = await promotions_service.get_analytics(
        db, promotion_id=promotion_id
    )
    return {"success": True, "data": analytics}