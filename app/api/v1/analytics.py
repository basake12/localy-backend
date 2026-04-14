from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.core.database import get_db
from app.dependencies import require_admin
from app.models.user_model import User
from app.schemas.common_schema import SuccessResponse
from app.services.analytics_service import analytics_service

router = APIRouter()


@router.get("/overview", response_model=SuccessResponse[dict])
def get_analytics_overview(
        db: Session = Depends(get_db),
        user: User = Depends(require_admin),
):
    """Get platform analytics overview (admin only)."""
    stats = analytics_service.get_dashboard_stats(db)
    return {"success": True, "data": stats}


@router.get("/revenue", response_model=SuccessResponse[dict])
def get_revenue_analytics(
        days: int = Query(30, ge=1, le=365),
        db: Session = Depends(get_db),
        user: User = Depends(require_admin),
):
    """Get revenue analytics for last N days (admin only)."""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    stats = analytics_service.get_revenue_stats(
        db,
        start_date=start_date,
        end_date=end_date,
    )
    return {"success": True, "data": stats}


@router.get("/trends", response_model=SuccessResponse[list])
def get_growth_trends(
        days: int = Query(30, ge=7, le=365),
        db: Session = Depends(get_db),
        user: User = Depends(require_admin),
):
    """Get growth trends (admin only)."""
    trends = analytics_service.get_growth_trends(db, days=days)
    return {"success": True, "data": trends}


@router.get("/categories", response_model=SuccessResponse[dict])
def get_category_stats(
        db: Session = Depends(get_db),
        user: User = Depends(require_admin),
):
    """Get statistics per business category (admin only)."""
    stats = analytics_service.get_category_stats(db)
    return {"success": True, "data": stats}