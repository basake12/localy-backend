from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Optional, List
from uuid import UUID
from decimal import Decimal

from app.models.coupon_model import Coupon, CouponRedemption, CouponStatus
from app.schemas.coupon_schema import CouponCreate, CouponUpdate, CouponUsageCreate


# ============================================
# COUPON CRUD
# ============================================

async def get_coupon_by_id(db: AsyncSession, coupon_id: UUID) -> Optional[Coupon]:
    result = await db.execute(select(Coupon).where(Coupon.id == coupon_id))
    return result.scalar_one_or_none()


async def get_coupon_by_code(db: AsyncSession, code: str) -> Optional[Coupon]:
    result = await db.execute(
        select(Coupon).where(Coupon.code == code.upper())
    )
    return result.scalar_one_or_none()


async def list_public_coupons(
    db: AsyncSession,
    category: Optional[str] = None,
    business_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 20,
) -> List[Coupon]:
    """
    List active, public coupons visible to customers.
    Optionally filter by category or business.
    Platform-wide coupons (business_id = None) are always included.
    """
    q = select(Coupon).where(
        and_(Coupon.status == CouponStatus.ACTIVE, Coupon.is_public == True)
    )

    if category:
        # JSONB contains operator: coupons where applicable_categories includes this category
        # OR applicable_categories is empty (applies to all)
        q = q.where(
            (Coupon.applicable_categories.contains([category])) |
            (Coupon.applicable_categories == [])
        )

    if business_id:
        # Return coupons for this business OR platform-wide coupons (business_id is NULL)
        q = q.where(
            (Coupon.business_id == business_id) |
            (Coupon.business_id == None)
        )

    q = q.order_by(Coupon.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())


async def list_business_coupons(
    db: AsyncSession,
    business_id: UUID,
    skip: int = 0,
    limit: int = 20,
) -> List[Coupon]:
    """
    List all coupons owned by a specific business — includes non-public coupons.
    Used by the business dashboard.
    """
    q = (
        select(Coupon)
        .where(Coupon.business_id == business_id)
        .order_by(Coupon.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


async def list_all_coupons(
    db: AsyncSession,
    status: Optional[CouponStatus] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[Coupon]:
    """Admin — list all coupons, optionally filtered by status."""
    q = select(Coupon)
    if status:
        q = q.where(Coupon.status == status)
    q = q.order_by(Coupon.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())


async def create_coupon(db: AsyncSession, data: CouponCreate) -> Coupon:
    coupon = Coupon(**data.model_dump())
    db.add(coupon)
    await db.commit()
    await db.refresh(coupon)
    return coupon


async def update_coupon(
    db: AsyncSession, coupon: Coupon, data: CouponUpdate
) -> Coupon:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(coupon, field, value)
    await db.commit()
    await db.refresh(coupon)
    return coupon


async def delete_coupon(db: AsyncSession, coupon: Coupon) -> None:
    await db.delete(coupon)
    await db.commit()


# ============================================
# USAGE CRUD
# ============================================

async def get_user_usage_count(
    db: AsyncSession, coupon_id: UUID, user_id: UUID
) -> int:
    result = await db.execute(
        select(func.count(CouponRedemption.id)).where(
            and_(
                CouponRedemption.coupon_id == coupon_id,
                CouponRedemption.user_id == user_id,
            )
        )
    )
    return result.scalar_one() or 0


async def record_usage(
    db: AsyncSession,
    user_id: UUID,
    data: CouponUsageCreate,
) -> CouponRedemption:
    usage = CouponRedemption(user_id=user_id, **data.model_dump())
    db.add(usage)

    # Atomically increment use counter on the coupon
    coupon = await get_coupon_by_id(db, data.coupon_id)
    if coupon:
        coupon.current_uses = (coupon.current_uses or 0) + 1

    await db.commit()
    await db.refresh(usage)
    return usage


async def list_user_usages(
    db: AsyncSession,
    user_id: UUID,
    skip: int = 0,
    limit: int = 20,
) -> List[CouponRedemption]:
    result = await db.execute(
        select(CouponRedemption)
        .where(CouponRedemption.user_id == user_id)
        .order_by(CouponRedemption.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_pending_cashback_usages(
    db: AsyncSession,
    limit: int = 100,
) -> List[CouponRedemption]:
    """
    Fetch usage records for CASHBACK coupons where cashback has not yet been credited.
    Called by Celery task to process pending cashback credits.
    """
    from app.models.coupon_model import CouponType

    result = await db.execute(
        select(CouponRedemption)
        .join(Coupon, CouponRedemption.coupon_id == Coupon.id)
        .where(
            and_(
                Coupon.coupon_type == CouponType.CASHBACK,
                CouponRedemption.cashback_credited == False,
                CouponRedemption.cashback_amount > 0,
            )
        )
        .limit(limit)
    )
    return list(result.scalars().all())