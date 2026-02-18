"""
admin_service.py

Thin orchestration layer between admin API routes and CRUD.
All date validation, permission checks, and response shaping live here.
"""

from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from datetime import date, timedelta
from uuid import UUID

from app.crud.admin import (
    admin_dashboard_crud,
    admin_user_crud,
    admin_business_crud,
    moderation_queue_crud,
)
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    PermissionDeniedException,
)
from app.core.constants import UserStatus


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

class AdminDashboardService:

    def get_overview(self, db: Session) -> dict:
        """
        Assemble the full dashboard card in one shot.
        Each sub-query is independently cached at the CRUD layer in prod;
        here we just fan out and merge.
        """
        users      = admin_dashboard_crud.user_summary(db)
        orders     = admin_dashboard_crud.order_summary(db)
        deliveries = admin_dashboard_crud.delivery_summary(db)
        pending    = admin_dashboard_crud.pending_moderation_count(db)
        avg_rating = admin_dashboard_crud.platform_avg_rating(db)

        return {
            "users":                 users,
            "orders":                orders,
            "deliveries":            deliveries,
            "avg_platform_rating":   avg_rating,
            "pending_reviews":       pending,
            "unread_flags":          pending,   # same pool; separate counter if needed later
        }

    # ---------- trends ----------

    _VALID_METRICS = {
        "new_users", "total_orders", "new_revenue",
        "completed_deliveries", "new_reviews", "total_messages",
    }

    def get_trend(
        self, db: Session, *,
        metric: str,
        from_date: date,
        to_date: date,
    ) -> dict:
        if metric not in self._VALID_METRICS:
            raise ValidationException(
                f"Invalid metric. Choose from: {', '.join(sorted(self._VALID_METRICS))}"
            )

        if from_date > to_date:
            raise ValidationException("from_date must be <= to_date")

        # Cap range at 365 days to protect the DB
        if (to_date - from_date).days > 365:
            raise ValidationException("Date range cannot exceed 365 days")

        points = admin_dashboard_crud.get_trend(
            db, metric=metric, from_date=from_date, to_date=to_date
        )
        return {"metric": metric, "points": points}

    # ---------- revenue ----------

    def get_revenue_report(
        self, db: Session, *,
        from_date: date,
        to_date: date,
    ) -> dict:
        if from_date > to_date:
            raise ValidationException("from_date must be <= to_date")

        if (to_date - from_date).days > 365:
            raise ValidationException("Date range cannot exceed 365 days")

        return admin_dashboard_crud.revenue_report(
            db, from_date=from_date, to_date=to_date
        )


# ---------------------------------------------------------------------------
# USER MANAGEMENT
# ---------------------------------------------------------------------------

class AdminUserService:

    def list_users(
        self, db: Session, *,
        user_type:  Optional[str]  = None,
        status:     Optional[str]  = None,
        search:     Optional[str]  = None,
        skip:       int            = 0,
        limit:      int            = 50,
    ) -> dict:
        users, total = admin_user_crud.list_users(
            db,
            user_type=user_type,
            status=status,
            search=search,
            skip=skip,
            limit=limit,
        )
        return {"users": users, "total": total, "skip": skip, "limit": limit}

    def update_status(self, db: Session, *, user_id: UUID, status: str) -> dict:
        valid_statuses = {s.value for s in UserStatus}
        if status not in valid_statuses:
            raise ValidationException(
                f"Invalid status. Choose from: {', '.join(sorted(valid_statuses))}"
            )

        user = admin_user_crud.update_status(db, user_id=user_id, status=status)
        if not user:
            raise NotFoundException("User not found")

        db.commit()
        db.refresh(user)
        return user


# ---------------------------------------------------------------------------
# BUSINESS MANAGEMENT
# ---------------------------------------------------------------------------

class AdminBusinessService:

    def list_businesses(
        self, db: Session, *,
        category:    Optional[str]  = None,
        is_verified: Optional[bool] = None,
        search:      Optional[str]  = None,
        skip:        int            = 0,
        limit:       int            = 50,
    ) -> dict:
        businesses, total = admin_business_crud.list_businesses(
            db,
            category=category,
            is_verified=is_verified,
            search=search,
            skip=skip,
            limit=limit,
        )
        return {"businesses": businesses, "total": total, "skip": skip, "limit": limit}

    def verify_business(
        self, db: Session, *,
        business_id: UUID,
        is_verified: bool,
        verification_badge: Optional[str] = None,
    ) -> dict:
        biz = admin_business_crud.update_verification(
            db,
            business_id=business_id,
            is_verified=is_verified,
            badge=verification_badge,
        )
        if not biz:
            raise NotFoundException("Business not found")

        db.commit()
        db.refresh(biz)
        return biz


# ---------------------------------------------------------------------------
# MODERATION QUEUE
# ---------------------------------------------------------------------------

class AdminModerationService:

    def get_queue(
        self, db: Session, *,
        skip:  int = 0,
        limit: int = 50,
    ) -> dict:
        reviews, total = moderation_queue_crud.get_flagged_reviews(
            db, skip=skip, limit=limit
        )

        # Flatten ORM rows → dicts matching ModerationQueueItem schema
        items = [
            {
                "review_id":       r.id,
                "reviewer_id":     r.reviewer_id,
                "reviewable_type": r.reviewable_type.value if hasattr(r.reviewable_type, "value") else r.reviewable_type,
                "reviewable_id":   r.reviewable_id,
                "rating":          r.rating,
                "title":           r.title,
                "body":            r.body,
                "flag_reason":     r.flag_reason,
                "status":          r.status.value if hasattr(r.status, "value") else r.status,
                "created_at":      r.created_at,
            }
            for r in reviews
        ]

        return {"items": items, "total": total, "skip": skip, "limit": limit}


# ---------------------------------------------------------------------------
# SINGLETONS
# ---------------------------------------------------------------------------

admin_dashboard_service  = AdminDashboardService()
admin_user_service       = AdminUserService()
admin_business_service   = AdminBusinessService()
admin_moderation_service = AdminModerationService()