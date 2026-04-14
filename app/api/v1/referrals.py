from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.referral_schema import (
    ApplyReferralCodeRequest,
    ApplyReferralCodeResponse,
    ReferralCodeResponse,
    ReferralDashboard,
)
from app.services import referral_service

router = APIRouter(tags=["Referrals"])


@router.get("/my-code", response_model=ReferralCodeResponse)
async def get_my_referral_code(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the current user's referral code (auto-created if missing)."""
    return await referral_service.get_or_create_my_code(db, current_user.id)


@router.get("/dashboard", response_model=ReferralDashboard)
async def get_referral_dashboard(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Full referral dashboard â€” code, stats, and list of referred users.
    Powers the ReferralsScreen in the app.
    """
    return await referral_service.get_dashboard(
        db, current_user.id, skip=skip, limit=limit
    )


@router.post(
    "/apply",
    response_model=ApplyReferralCodeResponse,
    status_code=status.HTTP_200_OK,
)
async def apply_referral_code(
    req: ApplyReferralCodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Apply a referral code during or just after registration.
    Each user can only apply one referral code; prevents self-referral.
    """
    return await referral_service.apply_referral_code_at_registration(
        db, new_user_id=current_user.id, req=req
    )