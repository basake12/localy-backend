# app/crud/referral.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional, List
from uuid import UUID
import secrets
import string
from datetime import datetime, timezone, timedelta

from app.models.referrals_model import ReferralCode, Referral, ReferralStatus

_CODE_CHARS = string.ascii_uppercase + string.digits
_CODE_LENGTH = 8
_REFERRAL_EXPIRY_DAYS = 30


# ============================================================
# REFERRAL CODE
# ============================================================

def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_CHARS) for _ in range(_CODE_LENGTH))


async def get_or_create_referral_code(
    db: AsyncSession, user_id: UUID
) -> ReferralCode:
    result = await db.execute(
        select(ReferralCode).where(ReferralCode.user_id == user_id)
    )
    code_obj = result.scalar_one_or_none()
    if code_obj:
        return code_obj

    # Collision-free code generation
    while True:
        code = _generate_code()
        exists = await db.execute(
            select(ReferralCode).where(ReferralCode.code == code)
        )
        if not exists.scalar_one_or_none():
            break

    code_obj = ReferralCode(user_id=user_id, code=code)
    db.add(code_obj)
    await db.commit()
    await db.refresh(code_obj)
    return code_obj


async def get_referral_code_by_code(
    db: AsyncSession, code: str
) -> Optional[ReferralCode]:
    result = await db.execute(
        select(ReferralCode).where(ReferralCode.code == code.upper())
    )
    return result.scalar_one_or_none()


async def get_referral_code_by_user(
    db: AsyncSession, user_id: UUID
) -> Optional[ReferralCode]:
    result = await db.execute(
        select(ReferralCode).where(ReferralCode.user_id == user_id)
    )
    return result.scalar_one_or_none()


# ============================================================
# REFERRAL
# ============================================================

async def get_referral_by_referred(
    db: AsyncSession, referred_id: UUID
) -> Optional[Referral]:
    """Each user can only be referred once."""
    result = await db.execute(
        select(Referral).where(Referral.referred_id == referred_id)
    )
    return result.scalar_one_or_none()


async def list_referrals_by_referrer(
    db: AsyncSession,
    referrer_id: UUID,
    skip: int = 0,
    limit: int = 20,
) -> List[Referral]:
    result = await db.execute(
        select(Referral)
        .where(Referral.referrer_id == referrer_id)
        .order_by(Referral.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def create_referral(
    db: AsyncSession,
    referral_code: ReferralCode,
    referred_id: UUID,
    referrer_reward: float,
    referred_reward: float,
) -> Referral:
    expires_at = datetime.now(timezone.utc) + timedelta(days=_REFERRAL_EXPIRY_DAYS)
    referral = Referral(
        referral_code_id=referral_code.id,
        referrer_id=referral_code.user_id,
        referred_id=referred_id,
        referrer_reward=referrer_reward,
        referred_reward=referred_reward,
        expires_at=expires_at,
        status=ReferralStatus.PENDING,
    )
    db.add(referral)
    referral_code.total_referrals = (referral_code.total_referrals or 0) + 1
    await db.commit()
    await db.refresh(referral)
    return referral


async def mark_referral_completed(
    db: AsyncSession, referral: Referral
) -> Referral:
    referral.status = ReferralStatus.COMPLETED
    referral.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(referral)
    return referral


async def mark_referral_rewarded(
    db: AsyncSession, referral: Referral, referral_code: ReferralCode
) -> Referral:
    referral.status = ReferralStatus.REWARDED
    referral.rewarded_at = datetime.now(timezone.utc)
    referral_code.successful_referrals = (referral_code.successful_referrals or 0) + 1
    referral_code.total_earnings = (
        (referral_code.total_earnings or 0) + referral.referrer_reward
    )
    await db.commit()
    await db.refresh(referral)
    return referral


async def expire_stale_referrals(db: AsyncSession) -> int:
    """Called by a scheduled Celery task. Returns count of expired rows."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Referral).where(
            and_(
                Referral.status == ReferralStatus.PENDING,
                Referral.expires_at < now,
            )
        )
    )
    referrals = result.scalars().all()
    for r in referrals:
        r.status = ReferralStatus.EXPIRED
    await db.commit()
    return len(referrals)