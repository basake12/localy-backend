from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_user, get_current_admin, get_current_business
from app.models.user_model import User
from app.schemas.coupon_schema import (
    CouponApplyRequest,
    CouponApplyResponse,
    CouponAnalyticsResponse,
    CouponCreate,
    CouponResponse,
    CouponSummary,
    CouponUpdate,
    CouponUsageCreate,
    CouponUsageResponse,
)
from app.services import coupon_service
from app.crud import coupon_crud

router = APIRouter(tags=["Coupons"])


# ============================================
# PUBLIC / CUSTOMER ENDPOINTS
# ============================================

@router.get("", response_model=List[CouponSummary])
async def list_coupons(
    category: Optional[str] = Query(None, description="Filter by applicable category"),
    business_id: Optional[UUID] = Query(None, description="Filter by business"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """List all active, public coupons. Optionally filter by category or business."""
    coupons = await coupon_service.list_available_coupons(
        db, category=category, business_id=business_id, skip=skip, limit=limit
    )
    return [
        CouponSummary(
            id=c.id,
            code=c.code,
            name=c.name,
            description=c.description,
            coupon_type=c.coupon_type,
            discount_value=c.discount_value,
            max_discount=c.max_discount,
            min_order_value=c.min_order_value,
            end_date=c.end_date,
            is_valid=c.is_valid(),
            business_id=c.business_id,
        )
        for c in coupons
    ]


@router.post("/apply", response_model=CouponApplyResponse)
async def apply_coupon(
    req: CouponApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Validate a coupon code and calculate the discount for an order.
    Call this before order confirmation.
    Call POST /coupons/usage after payment succeeds to lock in the usage.

    For CASHBACK coupons, discount_amount will be 0 and cashback_amount
    indicates what will be credited to the customer's wallet post-payment.
    """
    # Determine if this user is placing their first order
    # Check via coupon usage history as a proxy — zero prior usages = likely new user
    # A more precise check would query all order tables; this is the lightweight approach.
    from app.models.coupon_model import CouponRedemption
    from sqlalchemy import select, func

    usage_result = await db.execute(
        select(func.count(CouponRedemption.id)).where(CouponRedemption.user_id == current_user.id)
    )
    total_usages = usage_result.scalar_one() or 0
    is_new_user = total_usages == 0

    return await coupon_service.apply_coupon(
        db,
        user_id=current_user.id,
        req=req,
        is_new_user=is_new_user,
    )


@router.post("/usage", status_code=status.HTTP_201_CREATED)
async def record_coupon_usage(
    data: CouponUsageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Persist coupon usage after a successful payment.
    Must be called by the payment confirmation flow immediately after
    the transaction completes. Increments the coupon use counter atomically.
    """
    await coupon_service.record_coupon_usage(db, user_id=current_user.id, data=data)
    return {"message": "Coupon usage recorded"}


@router.get("/my-usages", response_model=List[CouponUsageResponse])
async def my_coupon_usages(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current customer's coupon usage history."""
    usages = await coupon_crud.list_user_usages(db, current_user.id, skip=skip, limit=limit)
    return [CouponUsageResponse.model_validate(u) for u in usages]


# ============================================
# BUSINESS ENDPOINTS
# ============================================

@router.get("/business/my-coupons", response_model=List[CouponSummary])
async def list_my_business_coupons(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_business),
):
    """
    Business: list all coupons owned by the authenticated business,
    including private/non-public ones.
    """
    business_id = current_user.business_id
    if not business_id:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No business profile associated with this account",
        )
    coupons = await coupon_service.get_business_coupons(
        db, business_id=business_id, skip=skip, limit=limit
    )
    return [
        CouponSummary(
            id=c.id,
            code=c.code,
            name=c.name,
            description=c.description,
            coupon_type=c.coupon_type,
            discount_value=c.discount_value,
            max_discount=c.max_discount,
            min_order_value=c.min_order_value,
            end_date=c.end_date,
            is_valid=c.is_valid(),
            business_id=c.business_id,
        )
        for c in coupons
    ]


@router.post("/business", response_model=CouponResponse, status_code=status.HTTP_201_CREATED)
async def create_business_coupon(
    data: CouponCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_business),
):
    """
    Business: create a business-funded coupon.
    The business_id is automatically set from the authenticated business —
    businesses cannot create coupons for other businesses.
    """
    business_id = current_user.business_id
    if not business_id:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No business profile associated with this account",
        )
    # Force the business_id to the authenticated business regardless of request
    data_with_business = data.model_copy(update={"business_id": business_id})
    coupon = await coupon_service.create_coupon(db, data_with_business)
    return CouponResponse.model_validate(coupon)


@router.patch("/business/{coupon_id}", response_model=CouponResponse)
async def update_business_coupon(
    coupon_id: UUID,
    data: CouponUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_business),
):
    """Business: update one of the business's own coupons."""
    from fastapi import HTTPException

    coupon = await coupon_crud.get_coupon_by_id(db, coupon_id)
    if not coupon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    if str(coupon.business_id) != str(current_user.business_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    updated = await coupon_crud.update_coupon(db, coupon, data)
    return CouponResponse.model_validate(updated)


@router.get("/business/{coupon_id}/analytics", response_model=CouponAnalyticsResponse)
async def business_coupon_analytics(
    coupon_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_business),
):
    """Business: get redemption analytics for one of the business's coupons."""
    return await coupon_service.get_coupon_analytics(
        db,
        coupon_id=coupon_id,
        business_id=current_user.business_id,
    )


# ============================================
# ADMIN ENDPOINTS
# ============================================

@router.get("/admin/all", response_model=List[CouponSummary])
async def admin_list_all_coupons(
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Admin: list all coupons with optional status filter."""
    from app.models.coupon_model import CouponStatus as CS
    parsed_status = None
    if status_filter:
        try:
            parsed_status = CS(status_filter)
        except ValueError:
            pass

    coupons = await coupon_crud.list_all_coupons(db, status=parsed_status, skip=skip, limit=limit)
    return [
        CouponSummary(
            id=c.id,
            code=c.code,
            name=c.name,
            description=c.description,
            coupon_type=c.coupon_type,
            discount_value=c.discount_value,
            max_discount=c.max_discount,
            min_order_value=c.min_order_value,
            end_date=c.end_date,
            is_valid=c.is_valid(),
            business_id=c.business_id,
        )
        for c in coupons
    ]


@router.post("/admin", response_model=CouponResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_coupon(
    data: CouponCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Admin: create a platform-funded coupon (business_id = None) or assign to a business."""
    coupon = await coupon_service.create_coupon(db, data)
    return CouponResponse.model_validate(coupon)


@router.patch("/admin/{coupon_id}", response_model=CouponResponse)
async def admin_update_coupon(
    coupon_id: UUID,
    data: CouponUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Admin: update any coupon."""
    coupon = await coupon_service.update_coupon(db, coupon_id, data)
    return CouponResponse.model_validate(coupon)


@router.delete("/admin/{coupon_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_coupon(
    coupon_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Admin: permanently delete a coupon."""
    await coupon_service.delete_coupon(db, coupon_id)


@router.get("/admin/{coupon_id}/analytics", response_model=CouponAnalyticsResponse)
async def admin_coupon_analytics(
    coupon_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Admin: get full analytics for any coupon."""
    return await coupon_service.get_coupon_analytics(db, coupon_id=coupon_id)