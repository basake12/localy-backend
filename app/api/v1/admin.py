"""
admin.py — /admin/*

Every route is gated behind require_admin().
Response envelopes follow the project-wide {success, data} convention.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
from uuid import UUID

from app.core.database       import get_db
from app.dependencies        import require_admin, get_pagination_params
from app.models.user         import User

from app.schemas.admin import (
    DashboardOverview,
    TrendResponse,
    AdminUserListOut,
    UserStatusUpdate,
    AdminBusinessListOut,
    BusinessVerifyUpdate,
    ModerationQueueOut,
    RevenueReport,
)

from app.services.admin_service import (
    admin_dashboard_service,
    admin_user_service,
    admin_business_service,
    admin_moderation_service,
)


router = APIRouter()


# ===========================================================================
# DASHBOARD  —  live KPI cards
# ===========================================================================

@router.get(
    "/dashboard",
    response_model=DashboardOverview,
    summary="Live dashboard overview",
    description="Returns real-time KPI cards: users, orders, deliveries, platform rating, moderation flags.",
)
async def get_dashboard(
    db:   Session = Depends(get_db),
    user: User    = Depends(require_admin),
):
    return admin_dashboard_service.get_overview(db)


# ===========================================================================
# TRENDS  —  time-series for charts
# ===========================================================================

@router.get(
    "/trends",
    response_model=TrendResponse,
    summary="Metric trend over a date range",
    description=(
        "Returns daily data points from the analytics snapshot table. "
        "Valid metrics: new_users, total_orders, new_revenue, "
        "completed_deliveries, new_reviews, total_messages."
    ),
)
async def get_trend(
    metric:    str  = Query(..., description="KPI slug"),
    from_date: date = Query(..., description="Start date (inclusive)"),
    to_date:   date = Query(..., description="End date (inclusive)"),
    db:        Session = Depends(get_db),
    user:      User    = Depends(require_admin),
):
    return admin_dashboard_service.get_trend(
        db, metric=metric, from_date=from_date, to_date=to_date
    )


# ===========================================================================
# REVENUE REPORT
# ===========================================================================

@router.get(
    "/revenue",
    response_model=RevenueReport,
    summary="Revenue report with category breakdown",
)
async def get_revenue_report(
    from_date: date = Query(..., description="Start date (inclusive)"),
    to_date:   date = Query(..., description="End date (inclusive)"),
    db:        Session = Depends(get_db),
    user:      User    = Depends(require_admin),
):
    return admin_dashboard_service.get_revenue_report(
        db, from_date=from_date, to_date=to_date
    )


# ===========================================================================
# USER MANAGEMENT
# ===========================================================================

@router.get(
    "/users",
    response_model=AdminUserListOut,
    summary="List all users with filters",
)
async def list_users(
    user_type: Optional[str] = Query(None, description="customer | business | rider | admin"),
    status:    Optional[str] = Query(None, description="active | suspended | banned | pending_verification"),
    search:    Optional[str] = Query(None, description="Search by email, phone, or name"),
    pagination: dict         = Depends(get_pagination_params),
    db:        Session       = Depends(get_db),
    user:      User          = Depends(require_admin),
):
    return admin_user_service.list_users(
        db,
        user_type=user_type,
        status=status,
        search=search,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


@router.patch(
    "/users/{user_id}/status",
    summary="Update a user's status (suspend / ban / reactivate)",
)
async def update_user_status(
    user_id: UUID,
    body:    UserStatusUpdate,
    db:      Session = Depends(get_db),
    user:    User    = Depends(require_admin),
):
    updated = admin_user_service.update_status(db, user_id=user_id, status=body.status)
    return {
        "success": True,
        "data": {
            "id":     updated.id,
            "status": updated.status.value if hasattr(updated.status, "value") else updated.status,
            "message": f"User status updated to {body.status}",
        },
    }


# ===========================================================================
# BUSINESS MANAGEMENT
# ===========================================================================

@router.get(
    "/businesses",
    response_model=AdminBusinessListOut,
    summary="List all businesses with filters",
)
async def list_businesses(
    category:    Optional[str]  = Query(None, description="Business category slug"),
    is_verified: Optional[bool] = Query(None, description="Filter by verification status"),
    search:      Optional[str]  = Query(None, description="Search by business name"),
    pagination:  dict           = Depends(get_pagination_params),
    db:          Session        = Depends(get_db),
    user:        User           = Depends(require_admin),
):
    return admin_business_service.list_businesses(
        db,
        category=category,
        is_verified=is_verified,
        search=search,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


@router.patch(
    "/businesses/{business_id}/verify",
    summary="Verify or un-verify a business + set badge",
)
async def verify_business(
    business_id: UUID,
    body:        BusinessVerifyUpdate,
    db:          Session = Depends(get_db),
    user:        User    = Depends(require_admin),
):
    updated = admin_business_service.verify_business(
        db,
        business_id=business_id,
        is_verified=body.is_verified,
        verification_badge=body.verification_badge,
    )
    return {
        "success": True,
        "data": {
            "id":          updated.id,
            "is_verified": updated.is_verified,
            "badge":       updated.verification_badge,
            "message":     "Business verification updated",
        },
    }


# ===========================================================================
# MODERATION QUEUE  —  flagged / pending reviews
# ===========================================================================

@router.get(
    "/moderation/queue",
    response_model=ModerationQueueOut,
    summary="Flagged & pending reviews awaiting moderation",
)
async def get_moderation_queue(
    pagination: dict    = Depends(get_pagination_params),
    db:         Session = Depends(get_db),
    user:       User    = Depends(require_admin),
):
    return admin_moderation_service.get_queue(
        db,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )