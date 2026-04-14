# app/services/referral_service.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status

from app.models.referrals_model import ReferralStatus
from app.models.user_model import User, CustomerProfile
from app.schemas.referral_schema import (
    ApplyReferralCodeRequest,
    ApplyReferralCodeResponse,
    ReferralCodeResponse,
    ReferralDashboard,
    ReferralItem,
)
from app.crud import  referral_crud

# ── Blueprint §6.1: ₦1,000 to referrer · ₦1,000 off new user's first order ──
_REFERRER_REWARD = Decimal("1000.00")
_REFERRED_REWARD = Decimal("1000.00")   # discount applied at checkout, not a wallet credit


# ============================================================
# GET / CREATE MY CODE
# ============================================================

async def get_or_create_my_code(
    db: AsyncSession, user_id: UUID
) -> ReferralCodeResponse:
    code_obj = await referral_crud.get_or_create_referral_code(db, user_id)
    return ReferralCodeResponse.model_validate(code_obj)


# ============================================================
# DASHBOARD
# ============================================================

async def get_dashboard(
    db: AsyncSession,
    user_id: UUID,
    skip: int = 0,
    limit: int = 20,
) -> ReferralDashboard:
    code_obj = await referral_crud.get_referral_code_by_user(db, user_id)
    if not code_obj:
        fresh = await referral_crud.get_or_create_referral_code(db, user_id)
        return ReferralDashboard(
            referral_code=fresh.code,
            reward_amount=_REFERRER_REWARD,
            total_referrals=0,
            successful_referrals=0,
            total_earned=Decimal("0.00"),
            referrals=[],
        )

    referrals = await referral_crud.list_referrals_by_referrer(
        db, user_id, skip=skip, limit=limit
    )

    items: List[ReferralItem] = []
    for r in referrals:
        # Load user with customer_profile in one query
        result = await db.execute(
            select(User)
            .options(selectinload(User.customer_profile))
            .where(User.id == r.referred_id)
        )
        referred_user = result.scalar_one_or_none()

        if referred_user and referred_user.customer_profile:
            cp = referred_user.customer_profile
            referee_name = f"{cp.first_name} {cp.last_name}".strip()
        else:
            referee_name = "Unknown User"

        items.append(
            ReferralItem(
                referral_id=r.id,
                referee_name=referee_name,
                joined_at=r.created_at,
                status=r.status,
                reward=r.referrer_reward,
            )
        )

    return ReferralDashboard(
        referral_code=code_obj.code,
        reward_amount=_REFERRER_REWARD,
        total_referrals=code_obj.total_referrals,
        successful_referrals=code_obj.successful_referrals,
        total_earned=code_obj.total_earnings,
        referrals=items,
    )


# ============================================================
# APPLY CODE AT REGISTRATION
# ============================================================

async def apply_referral_code_at_registration(
    db: AsyncSession,
    new_user_id: UUID,
    req: ApplyReferralCodeRequest,
) -> ApplyReferralCodeResponse:
    """
    Records the referral when a new user supplies a referral code.
    The ₦1,000 discount is stored in referred_reward and applied at checkout
    by order/booking services on the user's first transaction above ₦2,000.
    """
    existing = await referral_crud.get_referral_by_referred(db, new_user_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already used a referral code.",
        )

    code_obj = await referral_crud.get_referral_code_by_code(db, req.code)
    if not code_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Referral code not found.",
        )

    if code_obj.user_id == new_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot use your own referral code.",
        )

    await referral_crud.create_referral(
        db,
        referral_code=code_obj,
        referred_id=new_user_id,
        referrer_reward=float(_REFERRER_REWARD),
        referred_reward=float(_REFERRED_REWARD),
    )

    return ApplyReferralCodeResponse(
        message=(
            f"Referral code applied! "
            f"You'll get ₦{int(_REFERRED_REWARD):,} off your first order above ₦2,000."
        ),
        referred_reward=_REFERRED_REWARD,
    )


# ============================================================
# COMPLETE REFERRAL ON FIRST TRANSACTION
# ============================================================

async def complete_referral_on_first_transaction(
    db: AsyncSession,
    referred_user_id: UUID,
    first_order_amount: Decimal,
) -> None:
    """
    Called by order/booking services after the referred user's first paid
    transaction is confirmed. Marks the referral REWARDED and credits
    ₦1,000 to the referrer's wallet via wallet_service.credit_referral_bonus().

    Blueprint §6.1: reward only triggers once per referred user; first order
    must exceed ₦2,000 (enforced inside credit_referral_bonus).
    """
    referral = await referral_crud.get_referral_by_referred(db, referred_user_id)
    if not referral or referral.status != ReferralStatus.PENDING:
        return

    # Mark completed first, then attempt wallet credit
    referral = await referral_crud.mark_referral_completed(db, referral)

    # Late import to avoid circular dependency
    from app.services.wallet_service import wallet_service

    credited = await wallet_service.credit_referral_bonus(
        db,
        referrer_user_id=referral.referrer_id,
        referred_user_id=referred_user_id,
        first_order_amount=first_order_amount,
    )

    if credited:
        code_obj = await referral_crud.get_referral_code_by_user(
            db, referral.referrer_id
        )
        if code_obj:
            await referral_crud.mark_referral_rewarded(db, referral, code_obj)


# ============================================================
# CHECKOUT HELPER — discount for referred user
# ============================================================

async def get_pending_referral_discount(
    db: AsyncSession,
    user_id: UUID,
) -> Optional[Decimal]:
    """
    Returns the ₦1,000 discount amount if the user was referred and
    has not yet completed their first order. Returns None otherwise.
    Called by order/booking checkout services to apply the discount.
    Blueprint §6.1: discount only on first order above ₦2,000.
    """
    referral = await referral_crud.get_referral_by_referred(db, user_id)
    if referral and referral.status == ReferralStatus.PENDING:
        return Decimal(str(referral.referred_reward))
    return None