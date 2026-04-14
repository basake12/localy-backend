from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from decimal import Decimal
from uuid import UUID
from typing import Optional, List

from fastapi import HTTPException, status

from app.models.coupon_model import Coupon, CouponType, CouponStatus
from app.schemas.coupon_schema import (
    CouponCreate,
    CouponUpdate,
    CouponApplyRequest,
    CouponApplyResponse,
    CouponUsageCreate,
    CouponAnalyticsResponse,
)
from app.crud import coupon_crud


# ============================================
# CRUD WRAPPERS
# ============================================

async def list_available_coupons(
    db: AsyncSession,
    category: Optional[str] = None,
    business_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 20,
):
    return await coupon_crud.list_public_coupons(
        db, category=category, business_id=business_id, skip=skip, limit=limit
    )


async def create_coupon(db: AsyncSession, data: CouponCreate) -> Coupon:
    existing = await coupon_crud.get_coupon_by_code(db, data.code)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Coupon code '{data.code}' already exists",
        )
    return await coupon_crud.create_coupon(db, data)


async def update_coupon(db: AsyncSession, coupon_id: UUID, data: CouponUpdate) -> Coupon:
    coupon = await coupon_crud.get_coupon_by_id(db, coupon_id)
    if not coupon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    return await coupon_crud.update_coupon(db, coupon, data)


async def delete_coupon(db: AsyncSession, coupon_id: UUID) -> None:
    coupon = await coupon_crud.get_coupon_by_id(db, coupon_id)
    if not coupon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    await coupon_crud.delete_coupon(db, coupon)


async def record_coupon_usage(
    db: AsyncSession,
    user_id: UUID,
    data: CouponUsageCreate,
) -> None:
    coupon = await coupon_crud.get_coupon_by_id(db, data.coupon_id)
    if not coupon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    await coupon_crud.record_usage(db, user_id, data)


# ============================================
# APPLY ENGINE — All 10 coupon types
# ============================================

async def apply_coupon(
    db: AsyncSession,
    user_id: UUID,
    req: CouponApplyRequest,
    is_new_user: bool = False,
) -> CouponApplyResponse:
    """
    Validate a coupon code against the order context and compute the discount.

    Returns a CouponApplyResponse with discount_amount and final_amount.
    For CASHBACK coupons, discount_amount is 0 and cashback_amount holds
    the wallet credit that will be issued post-payment.
    For FREE_DELIVERY, discount_amount equals the delivery_fee passed in.
    """

    # ── 1. Fetch coupon ───────────────────────────────────────────────────────
    coupon = await coupon_crud.get_coupon_by_code(db, req.code)
    if not coupon:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid coupon code",
        )

    # ── 2. Basic validity (status, time window, global use count) ─────────────
    if not coupon.is_valid():
        _raise_invalid(coupon)

    # ── 3. Per-user use count ─────────────────────────────────────────────────
    user_usage_count = await coupon_crud.get_user_usage_count(db, coupon.id, user_id)
    if not coupon.can_user_use(user_usage_count):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already used this coupon the maximum number of times",
        )

    # ── 4. Minimum order value ────────────────────────────────────────────────
    if coupon.min_order_value and req.order_total < coupon.min_order_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Minimum order value for this coupon is ₦{coupon.min_order_value:,.2f}",
        )

    # ── 5. Category restriction ───────────────────────────────────────────────
    if coupon.applicable_categories:
        if not req.category or req.category not in coupon.applicable_categories:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This coupon is not applicable for this category",
            )

    # ── 6. Business restriction ───────────────────────────────────────────────
    if coupon.business_id:
        if not req.business_id or str(coupon.business_id) != str(req.business_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This coupon is only valid at a specific business",
            )

    # ── 7. First-order / new-user gate ────────────────────────────────────────
    if coupon.coupon_type == CouponType.FIRST_ORDER or coupon.new_users_only:
        if not is_new_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This coupon is only valid on your first order",
            )

    # ── 8. Type-specific discount calculation ─────────────────────────────────
    discount_amount = Decimal("0")
    cashback_amount = Decimal("0")
    delivery_fee_waived = False
    message = ""

    ct = coupon.coupon_type

    if ct == CouponType.PERCENTAGE:
        discount_amount = _apply_percentage(
            req.order_total, coupon.discount_value, coupon.max_discount
        )
        message = f"{coupon.discount_value:.0f}% discount applied"

    elif ct == CouponType.FIXED:
        discount_amount = min(Decimal(str(coupon.discount_value)), req.order_total)
        message = f"₦{discount_amount:,.2f} discount applied"

    elif ct == CouponType.FREE_DELIVERY:
        discount_amount = req.delivery_fee
        delivery_fee_waived = True
        message = "Free delivery applied — delivery fee waived"

    elif ct == CouponType.BUY_X_GET_Y:
        discount_amount = _apply_buy_x_get_y(
            req.order_total,
            req.item_count,
            coupon.buy_quantity or 1,
            coupon.get_quantity or 1,
        )
        message = (
            f"Buy {coupon.buy_quantity} Get {coupon.get_quantity} Free applied"
        )

    elif ct == CouponType.CASHBACK:
        # No upfront discount — cashback credited to wallet post-payment
        cashback_amount = _apply_percentage(
            req.order_total, coupon.discount_value, coupon.max_discount
        )
        discount_amount = Decimal("0")
        message = (
            f"₦{cashback_amount:,.2f} cashback will be added to your wallet after payment"
        )

    elif ct == CouponType.FIRST_ORDER:
        # Treated like a fixed or percentage discount — check discount_value
        if coupon.discount_value < 100:
            # Treat as percentage if <= 100, otherwise fixed
            # Convention: FIRST_ORDER discount_value is always fixed amount for clarity
            discount_amount = min(Decimal(str(coupon.discount_value)), req.order_total)
        else:
            discount_amount = min(Decimal(str(coupon.discount_value)), req.order_total)
        message = f"First-order discount of ₦{discount_amount:,.2f} applied"

    elif ct == CouponType.CATEGORY:
        # Category-scoped percentage discount (already validated category above)
        discount_amount = _apply_percentage(
            req.order_total, coupon.discount_value, coupon.max_discount
        )
        message = f"{coupon.discount_value:.0f}% category discount applied"

    elif ct == CouponType.BUSINESS_SPECIFIC:
        # Business-specific — treat as fixed or percentage
        if coupon.discount_value <= 100:
            # Treat as percentage
            discount_amount = _apply_percentage(
                req.order_total, coupon.discount_value, coupon.max_discount
            )
            message = f"{coupon.discount_value:.0f}% discount applied"
        else:
            discount_amount = min(Decimal(str(coupon.discount_value)), req.order_total)
            message = f"₦{discount_amount:,.2f} discount applied"

    elif ct == CouponType.FLASH:
        # Flash coupon — time validation already covered by is_valid()
        # Compute discount same as percentage or fixed
        if coupon.discount_value <= 100:
            discount_amount = _apply_percentage(
                req.order_total, coupon.discount_value, coupon.max_discount
            )
            message = f"Flash deal: {coupon.discount_value:.0f}% discount applied"
        else:
            discount_amount = min(Decimal(str(coupon.discount_value)), req.order_total)
            message = f"Flash deal: ₦{discount_amount:,.2f} discount applied"

    elif ct == CouponType.BUNDLE:
        # Validate that all required bundle items are present in cart
        missing = _check_bundle_items(req.item_ids, coupon.bundle_item_ids or [])
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Your cart is missing required items for this bundle coupon",
            )
        discount_amount = min(Decimal(str(coupon.discount_value)), req.order_total)
        message = f"Bundle discount of ₦{discount_amount:,.2f} applied"

    # ── 9. Final amount ────────────────────────────────────────────────────────
    final_amount = max(req.order_total - discount_amount, Decimal("0"))

    return CouponApplyResponse(
        coupon_id=coupon.id,
        code=coupon.code,
        coupon_type=coupon.coupon_type,
        discount_amount=discount_amount,
        cashback_amount=cashback_amount,
        final_amount=final_amount,
        message=message,
        delivery_fee_waived=delivery_fee_waived,
    )


# ============================================
# ANALYTICS
# ============================================

async def get_coupon_analytics(
    db: AsyncSession,
    coupon_id: UUID,
    business_id: Optional[UUID] = None,
) -> CouponAnalyticsResponse:
    """
    Returns aggregate statistics for a single coupon.
    If business_id is provided, verifies the coupon belongs to that business.
    """
    from app.models.coupon_model import CouponUsage

    coupon = await coupon_crud.get_coupon_by_id(db, coupon_id)
    if not coupon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")

    # Businesses may only view their own coupon analytics
    if business_id and str(coupon.business_id) != str(business_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Aggregate usage data
    result = await db.execute(
        select(
            func.count(CouponUsage.id).label("total_redemptions"),
            func.coalesce(func.sum(CouponUsage.discount_amount), 0).label("total_discount_given"),
            func.coalesce(
                func.sum(CouponUsage.cashback_amount), 0
            ).label("total_cashback_credited"),
        ).where(CouponUsage.coupon_id == coupon_id)
    )
    row = result.one()

    return CouponAnalyticsResponse(
        coupon_id=coupon.id,
        code=coupon.code,
        name=coupon.name,
        coupon_type=coupon.coupon_type,
        total_redemptions=int(row.total_redemptions),
        total_discount_given=Decimal(str(row.total_discount_given)),
        total_cashback_credited=Decimal(str(row.total_cashback_credited)),
        current_uses=coupon.current_uses or 0,
        max_uses=coupon.max_uses,
        status=coupon.status,
    )


async def get_business_coupons(
    db: AsyncSession,
    business_id: UUID,
    skip: int = 0,
    limit: int = 20,
) -> list:
    """List all coupons owned by a specific business (including non-public ones)."""
    return await coupon_crud.list_business_coupons(db, business_id, skip=skip, limit=limit)


async def credit_cashback(
    db: AsyncSession,
    usage_id: UUID,
) -> None:
    """
    Called by Celery task after payment confirmation to credit cashback wallet.
    Marks cashback_credited = True on the usage record.
    Wallet credit itself is handled by wallet_service.
    """
    from app.models.coupon_model import CouponUsage

    result = await db.execute(
        select(CouponUsage).where(CouponUsage.id == usage_id)
    )
    usage = result.scalar_one_or_none()
    if not usage:
        return
    if usage.cashback_credited:
        return  # idempotent

    usage.cashback_credited = True
    await db.commit()


# ============================================
# PRIVATE HELPERS
# ============================================

def _apply_percentage(
    order_total: Decimal,
    rate: Decimal,
    max_discount: Optional[Decimal],
) -> Decimal:
    """Compute percentage discount, optionally capped by max_discount."""
    raw = order_total * (Decimal(str(rate)) / Decimal("100"))
    if max_discount is not None:
        raw = min(raw, Decimal(str(max_discount)))
    return raw.quantize(Decimal("0.01"))


def _apply_buy_x_get_y(
    order_total: Decimal,
    item_count: int,
    buy_qty: int,
    get_qty: int,
) -> Decimal:
    """
    Computes the value of free items.
    Assumes items are equal-valued; discount = (free_items / total_items) * order_total.
    """
    if item_count < buy_qty:
        return Decimal("0")

    sets = item_count // buy_qty
    free_items = sets * get_qty
    total_items = item_count + free_items  # notional total
    # Discount = proportion of free items from order total
    if total_items == 0:
        return Decimal("0")
    discount = order_total * (Decimal(str(free_items)) / Decimal(str(total_items)))
    return discount.quantize(Decimal("0.01"))


def _check_bundle_items(cart_item_ids: List[str], required_item_ids: List[str]) -> List[str]:
    """Returns list of required item IDs missing from the cart."""
    cart_set = set(cart_item_ids)
    return [item_id for item_id in required_item_ids if item_id not in cart_set]


def _raise_invalid(coupon: Coupon) -> None:
    """Raise a descriptive 400 for an invalid coupon."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    if coupon.status == CouponStatus.DISABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This coupon has been disabled",
        )
    if coupon.status == CouponStatus.EXPIRED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This coupon has expired",
        )
    if coupon.end_date and now > coupon.end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This coupon has expired",
        )
    if coupon.start_date and now < coupon.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This coupon is not yet active",
        )
    if coupon.max_uses is not None and coupon.current_uses >= coupon.max_uses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This coupon has reached its maximum usage limit",
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="This coupon is no longer valid",
    )