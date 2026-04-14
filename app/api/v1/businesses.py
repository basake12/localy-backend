"""
app/api/v1/businesses.py

FIX (Blueprint v2.0):
  - list_businesses uses latitude/longitude/radius_meters — no LGA filtering.
    Discovery is purely GPS-radius based (PostGIS ST_DWithin).

  - Per-business stats and analytics endpoints for Flutter dashboard:
      GET /businesses/{id}/stats
      GET /businesses/{id}/analytics/revenue

FIX (full async conversion):
  CRUDBusiness extends AsyncCRUDBase — every method (including the inherited
  base .get() / .update()) is a coroutine. The entire router was using sync
  Session + db.query() ORM style which is incompatible with AsyncSession.

  All endpoints are now:
    - async def
    - AsyncSession = Depends(get_async_db)
    - await on all business_crud calls
    - select() + await db.execute() instead of db.query()

FIX (public access):
  - list_businesses: no auth required (Blueprint §12 open discovery)
  - get_business_by_id: auth optional (public profile pages)

FIX (Review comparison):
  - cast(business.id, String) instead of Python str() for DB-level safety.
"""

from datetime import datetime, timezone, timedelta
from uuid import UUID
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import func, and_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.constants import DEFAULT_RADIUS_METERS, MAX_RADIUS_METERS, MIN_RADIUS_METERS
from app.dependencies import (
    get_async_current_active_user,
    get_async_current_user_optional,
    require_async_business,
)
from app.models.user_model import User
from app.models.business_model import Business
from app.models.food_model import FoodOrder, Restaurant
from app.models.reviews_model import Review
from app.schemas.business_schema import BusinessOut, BusinessUpdate, BusinessListOut
from app.schemas.common_schema import SuccessResponse
from app.crud.business_crud import business_crud

router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _period_days(period: str) -> int:
    return {"7d": 7, "14d": 14, "30d": 30, "90d": 90, "1y": 365}.get(period, 30)


async def _get_business_or_404(db: AsyncSession, business_id: str) -> Business:
    """
    Resolve business_id string → Business or raise 404/422.

    Uses selectinload for business_hours and user so Pydantic can serialise
    BusinessOut without triggering lazy loads in an async context (greenlet crash).
    The base AsyncCRUDBase.get() does a plain select with no eager loading —
    that caused ResponseValidationError on every GET /{id} call.
    """
    try:
        uid = UUID(business_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid business ID",
        )
    result = await db.execute(
        select(Business)
        .options(
            selectinload(Business.business_hours),
            selectinload(Business.user),
        )
        .where(Business.id == uid, Business.is_active == True)
    )
    business = result.scalars().first()
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found",
        )
    return business


# ─── My Business ──────────────────────────────────────────────────────────────

@router.get("/my-business", response_model=SuccessResponse[BusinessOut])
async def get_my_business(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(require_async_business),
):
    """Get the authenticated business user's own profile."""
    business = await business_crud.get_by_user_id(db, user_id=user.id)
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No business profile found for this account",
        )
    return {"success": True, "data": business}


@router.put("/my-business", response_model=SuccessResponse[BusinessOut])
async def update_my_business(
    payload: BusinessUpdate,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(require_async_business),
):
    """Update the authenticated business user's own profile."""
    business = await business_crud.get_by_user_id(db, user_id=user.id)
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No business profile found for this account",
        )
    updated = await business_crud.update(db, db_obj=business, obj_in=payload)
    return {"success": True, "data": updated}


# ─── Business Stats ───────────────────────────────────────────────────────────

@router.get("/{business_id}/stats")
async def get_business_stats(
    business_id: str,
    period: str = Query("30d", description="Time window: 7d|14d|30d|90d|1y"),
    db: AsyncSession = Depends(get_async_db),
    _: User = Depends(get_async_current_active_user),
) -> dict:
    """Dashboard KPI stats for a business."""
    business = await _get_business_or_404(db, business_id)
    days  = _period_days(period)
    now   = _now()
    since = now - timedelta(days=days)
    prev  = since - timedelta(days=days)

    total_revenue  = 0.0
    prev_revenue   = 0.0
    total_orders   = 0
    prev_orders    = 0
    pending_orders = 0
    today_revenue  = 0.0

    category_val = (
        business.category.value
        if hasattr(business.category, "value")
        else str(business.category)
    ).upper()

    if category_val == "FOOD":
        restaurant_result = await db.execute(
            select(Restaurant).where(Restaurant.business_id == business.id)
        )
        restaurant = restaurant_result.scalars().first()

        if restaurant:
            rid = restaurant.id

            cur = (await db.execute(
                select(func.count(FoodOrder.id), func.sum(FoodOrder.total_amount))
                .where(
                    FoodOrder.restaurant_id == rid,
                    FoodOrder.payment_status == "paid",
                    FoodOrder.created_at >= since,
                )
            )).first()
            total_orders  = cur[0] or 0
            total_revenue = float(cur[1] or 0)

            prv = (await db.execute(
                select(func.count(FoodOrder.id), func.sum(FoodOrder.total_amount))
                .where(
                    FoodOrder.restaurant_id == rid,
                    FoodOrder.payment_status == "paid",
                    FoodOrder.created_at >= prev,
                    FoodOrder.created_at < since,
                )
            )).first()
            prev_orders  = prv[0] or 0
            prev_revenue = float(prv[1] or 0)

            pending_result = await db.execute(
                select(func.count(FoodOrder.id))
                .where(
                    FoodOrder.restaurant_id == rid,
                    FoodOrder.order_status.in_(["pending", "confirmed", "preparing"]),
                )
            )
            pending_orders = pending_result.scalar() or 0

            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_result = await db.execute(
                select(func.sum(FoodOrder.total_amount))
                .where(
                    FoodOrder.restaurant_id == rid,
                    FoodOrder.payment_status == "paid",
                    FoodOrder.created_at >= today_start,
                )
            )
            today_revenue = float(today_result.scalar() or 0)

    # reviewable_id is UUID(as_uuid=True) — compare UUID to UUID directly.
    # Previously used cast(business.id, String) which compared a UUID column
    # against varchar → "operator does not exist: uuid = character varying" error.
    review_result = await db.execute(
        select(func.count(Review.id), func.avg(Review.rating))
        .where(Review.reviewable_id == business.id)
    )
    review_row     = review_result.first()
    review_count   = review_row[0] or 0
    average_rating = float(review_row[1] or business.average_rating or 0)

    def _pct(cur: float, prv: float) -> float:
        if prv == 0:
            return 100.0 if cur > 0 else 0.0
        return round((cur - prv) / prv * 100, 1)

    return {
        "success": True,
        "data": {
            "total_revenue":         total_revenue,
            "revenue_change":        _pct(total_revenue, prev_revenue),
            "total_orders":          total_orders,
            "orders_change":         _pct(total_orders, prev_orders),
            "total_customers":       total_orders,
            "customers_change":      _pct(total_orders, prev_orders),
            "average_rating":        average_rating,
            "review_count":          review_count,
            "pending_orders":        pending_orders,
            "today_revenue":         today_revenue,
            "occupancy_rate":        0,
            "occupancy_rate_change": 0,
            "adr":                   0,
            "adr_change":            0,
            "rev_par":               0,
            "rev_par_change":        0,
        },
    }


# ─── Business Analytics — Revenue Chart ───────────────────────────────────────

@router.get("/{business_id}/analytics/revenue")
async def get_business_revenue(
    business_id: str,
    period: str = Query("30d", description="7d|14d|30d|90d"),
    db: AsyncSession = Depends(get_async_db),
    _: User = Depends(get_async_current_active_user),
) -> dict:
    """Daily revenue chart data for the dashboard."""
    business = await _get_business_or_404(db, business_id)
    days = _period_days(period)
    now  = _now()

    category_val = (
        business.category.value
        if hasattr(business.category, "value")
        else str(business.category)
    ).upper()

    points: List[dict] = []

    if category_val == "FOOD":
        restaurant_result = await db.execute(
            select(Restaurant).where(Restaurant.business_id == business.id)
        )
        restaurant = restaurant_result.scalars().first()

        if restaurant:
            rows_result = await db.execute(
                select(
                    func.date(FoodOrder.created_at).label("day"),
                    func.sum(FoodOrder.total_amount).label("revenue"),
                )
                .where(
                    FoodOrder.restaurant_id == restaurant.id,
                    FoodOrder.payment_status == "paid",
                    FoodOrder.created_at >= now - timedelta(days=days),
                )
                .group_by(func.date(FoodOrder.created_at))
                .order_by(func.date(FoodOrder.created_at))
            )
            points = [
                {"label": str(row.day), "amount": float(row.revenue or 0)}
                for row in rows_result.all()
            ]

    if not points:
        points = [
            {
                "label":  (now - timedelta(days=i)).strftime("%m/%d"),
                "amount": 0.0,
            }
            for i in range(min(days, 7), 0, -1)
        ]

    return {"success": True, "data": {"points": points}}


# ─── Business Subscription ────────────────────────────────────────────────────

@router.get("/{business_id}/subscription")
async def get_business_subscription(
    business_id: str,
    db: AsyncSession = Depends(get_async_db),
    _: User = Depends(get_async_current_active_user),
) -> dict:
    """Return the business's active subscription plan."""
    business = await _get_business_or_404(db, business_id)

    from app.models.subscription_model import Subscription
    sub_result = await db.execute(
        select(Subscription)
        .join(User, User.id == Subscription.user_id)
        .join(Business, Business.user_id == User.id)
        .where(
            Business.id == business.id,
            Subscription.status == "ACTIVE",
        )
        .order_by(Subscription.expires_at.desc())
    )
    sub = sub_result.scalars().first()

    plan_name  = "Free"
    is_active  = False
    expires_at = None

    if sub:
        plan_name  = sub.plan.name if sub.plan else "Starter"
        is_active  = True
        expires_at = sub.expires_at.isoformat() if sub.expires_at else None

    return {
        "success": True,
        "data": {
            "plan_name":  plan_name,
            "is_active":  is_active,
            "expires_at": expires_at,
        },
    }


# ─── Business Inbox ───────────────────────────────────────────────────────────

@router.get("/{business_id}/inbox")
async def get_business_inbox(
    business_id: str,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_async_db),
    _: User = Depends(get_async_current_active_user),
) -> dict:
    """Return recent conversations for the business inbox widget."""
    business = await _get_business_or_404(db, business_id)

    from app.models.chat_model import Conversation
    convo_result = await db.execute(
        select(Conversation)
        .where(Conversation.user_two_id == business.user_id)
        .order_by(Conversation.last_message_at.desc())
        .offset(skip)
        .limit(limit)
    )
    convos = convo_result.scalars().all()

    messages = [
        {
            "id":          str(c.id),
            "sender_name": "Customer",
            "preview":     c.last_message_preview or "",
            "time":        c.last_message_at.isoformat() if c.last_message_at else _now().isoformat(),
            "unread":      c.unread_count_user_two or 0,
            "avatar_url":  None,
        }
        for c in convos
    ]

    return {"success": True, "data": {"messages": messages}}


# ─── List / Search Businesses ─────────────────────────────────────────────────

@router.get("", response_model=SuccessResponse[BusinessListOut])
async def list_businesses(
    latitude:      float = Query(..., ge=-90,  le=90,  description="User's current latitude"),
    longitude:     float = Query(..., ge=-180, le=180, description="User's current longitude"),
    radius_meters: float = Query(
        DEFAULT_RADIUS_METERS,
        ge=MIN_RADIUS_METERS,
        le=MAX_RADIUS_METERS,
        description="Search radius in metres (default 5 000 m = 5 km)",
    ),
    category: Optional[str] = Query(None),
    search:   Optional[str] = Query(None),
    skip:  int = Query(0,  ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Discover businesses near the user.

    Public — no auth required (Blueprint §12 open discovery).
    Sorted by: subscription tier → featured → rating → distance.
    """
    businesses, total = await business_crud.search_businesses(
        db,
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
        category=category,
        search_query=search,
        skip=skip,
        limit=limit,
    )
    return {
        "success": True,
        "data": {
            "businesses": businesses,
            "total":      total,
            "page":       skip // limit + 1,
            "page_size":  limit,
        },
    }


# ─── Get Business By ID ───────────────────────────────────────────────────────

@router.get("/{business_id}", response_model=SuccessResponse[BusinessOut])
async def get_business_by_id(
    business_id: str,
    db: AsyncSession = Depends(get_async_db),
    # Public profile — auth optional per Blueprint §12
    _: Optional[User] = Depends(get_async_current_user_optional),
):
    """Get any public business profile by ID. No auth required."""
    business = await _get_business_or_404(db, business_id)
    return {"success": True, "data": business}