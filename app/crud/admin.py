from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from uuid import UUID
from datetime import date, datetime, timedelta

from app.models.user         import User, UserTypeEnum, UserStatusEnum
from app.models.business     import Business
from app.models.hotels       import HotelBooking
from app.models.products     import ProductOrder
from app.models.food         import FoodOrder
from app.models.services     import ServiceBooking
from app.models.delivery     import Delivery
from app.models.reviews      import Review, ReviewStatusEnum
from app.models.chat         import Message
from app.models.wallet       import WalletTransaction
from app.models.analytics    import DailyAnalyticsSnapshot


# ---------------------------------------------------------------------------
# LIVE DASHBOARD QUERIES
# ---------------------------------------------------------------------------

class CRUDAdminDashboard:

    def _today(self) -> date:
        return datetime.utcnow().date()

    # ---------- users ----------

    def user_summary(self, db: Session) -> dict:
        today = self._today()

        totals = (
            db.query(
                func.count(User.id).label("total"),
                func.count(
                    User.id.op("*")  # placeholder; we filter per type below
                ).label("x"),
            )
            .one()
        )

        by_type = (
            db.query(User.user_type, func.count(User.id))
            .group_by(User.user_type)
            .all()
        )
        type_map = {str(row[0].value if hasattr(row[0], 'value') else row[0]): row[1] for row in by_type}

        new_today = (
            db.query(func.count(User.id))
            .filter(func.date(User.created_at) == today)
            .scalar()
        ) or 0

        # "active today" approximated by login — use created_at as proxy for now
        # In production track last_login_at
        active_today = new_today  # simplified; swap with last_login_at filter

        return {
            "total":        totals.total,
            "customers":    type_map.get("customer", 0),
            "businesses":   type_map.get("business", 0),
            "riders":       type_map.get("rider", 0),
            "new_today":    new_today,
            "active_today": active_today,
        }

    # ---------- orders ----------

    def order_summary(self, db: Session) -> dict:
        today = self._today()

        # Aggregate across hotel bookings, product orders, food orders, service bookings
        # For simplicity, count product_orders as the primary order proxy and add food_orders
        prod_today = (
            db.query(
                func.count(ProductOrder.id).label("total"),
                func.coalesce(func.sum(ProductOrder.total_amount), 0).label("revenue"),
            )
            .filter(func.date(ProductOrder.created_at) == today)
            .one()
        )

        food_today = (
            db.query(
                func.count(FoodOrder.id).label("total"),
                func.coalesce(func.sum(FoodOrder.total_amount), 0).label("revenue"),
            )
            .filter(func.date(FoodOrder.created_at) == today)
            .one()
        )

        hotel_today = (
            db.query(func.count(HotelBooking.id))
            .filter(func.date(HotelBooking.created_at) == today)
            .scalar()
        ) or 0

        service_today = (
            db.query(func.count(ServiceBooking.id))
            .filter(func.date(ServiceBooking.created_at) == today)
            .scalar()
        ) or 0

        total_orders  = (prod_today.total or 0) + (food_today.total or 0) + hotel_today + service_today
        revenue_today = float(prod_today.revenue or 0) + float(food_today.revenue or 0)

        # Cancelled today (product orders)
        cancelled = (
            db.query(func.count(ProductOrder.id))
            .filter(
                func.date(ProductOrder.created_at) == today,
                ProductOrder.order_status == "cancelled",
            )
            .scalar()
        ) or 0

        # Completed today
        completed = (
            db.query(func.count(ProductOrder.id))
            .filter(
                func.date(ProductOrder.created_at) == today,
                ProductOrder.order_status == "delivered",
            )
            .scalar()
        ) or 0

        return {
            "total_orders":     total_orders,
            "new_today":        total_orders,
            "completed_today":  completed,
            "cancelled_today":  cancelled,
            "revenue_today":    round(revenue_today, 2),
        }

    # ---------- deliveries ----------

    def delivery_summary(self, db: Session) -> dict:
        today = self._today()

        total = (
            db.query(func.count(Delivery.id))
            .filter(func.date(Delivery.created_at) == today)
            .scalar()
        ) or 0

        completed = (
            db.query(func.count(Delivery.id))
            .filter(
                func.date(Delivery.created_at) == today,
                Delivery.status == "delivered",
            )
            .scalar()
        ) or 0

        # Average delivery time in minutes — uses delivered_at - created_at
        avg_row = (
            db.query(
                func.avg(
                    func.extract("epoch", Delivery.delivered_at) - func.extract("epoch", Delivery.created_at)
                )
            )
            .filter(
                func.date(Delivery.created_at) == today,
                Delivery.status == "delivered",
                Delivery.delivered_at.isnot(None),
            )
            .scalar()
        )
        avg_min = round((avg_row or 0) / 60, 2)

        return {
            "total":            total,
            "completed_today":  completed,
            "avg_time_min":     avg_min,
        }

    # ---------- reviews / flags ----------

    def pending_moderation_count(self, db: Session) -> int:
        return (
            db.query(func.count(Review.id))
            .filter(Review.status.in_([ReviewStatusEnum.FLAGGED, ReviewStatusEnum.PENDING]))
            .scalar()
        ) or 0

    def platform_avg_rating(self, db: Session) -> float:
        row = (
            db.query(func.avg(Review.rating))
            .filter(Review.status == ReviewStatusEnum.APPROVED)
            .scalar()
        )
        return round(float(row) if row else 0.0, 2)

    # ---------- trends (from snapshots) ----------

    def get_trend(self, db: Session, *, metric: str, from_date: date, to_date: date) -> List[Dict]:
        """Pull a time-series from daily snapshots."""
        valid_metrics = {
            "new_users", "total_orders", "new_revenue",
            "completed_deliveries", "new_reviews", "total_messages",
        }
        if metric not in valid_metrics:
            metric = "new_users"

        rows = (
            db.query(DailyAnalyticsSnapshot.snapshot_date, getattr(DailyAnalyticsSnapshot, metric))
            .filter(
                DailyAnalyticsSnapshot.snapshot_date >= from_date,
                DailyAnalyticsSnapshot.snapshot_date <= to_date,
            )
            .order_by(DailyAnalyticsSnapshot.snapshot_date)
            .all()
        )
        return [{"date": row[0], "value": float(row[1] or 0)} for row in rows]

    # ---------- revenue report ----------

    def revenue_report(self, db: Session, *, from_date: date, to_date: date) -> dict:
        """Aggregate revenue by category between dates."""
        # Product orders
        prod = (
            db.query(func.coalesce(func.sum(ProductOrder.total_amount), 0))
            .filter(
                func.date(ProductOrder.created_at).between(from_date, to_date),
                ProductOrder.order_status == "delivered",
            )
            .scalar()
        ) or 0

        # Food orders
        food = (
            db.query(func.coalesce(func.sum(FoodOrder.total_amount), 0))
            .filter(
                func.date(FoodOrder.created_at).between(from_date, to_date),
                FoodOrder.order_status == "delivered",
            )
            .scalar()
        ) or 0

        # Hotel bookings (total_amount)
        hotel = (
            db.query(func.coalesce(func.sum(HotelBooking.total_amount), 0))
            .filter(
                func.date(HotelBooking.created_at).between(from_date, to_date),
                HotelBooking.status == "checked_out",
            )
            .scalar()
        ) or 0

        # Service bookings
        service = (
            db.query(func.coalesce(func.sum(ServiceBooking.total_amount), 0))
            .filter(
                func.date(ServiceBooking.created_at).between(from_date, to_date),
                ServiceBooking.status == "completed",
            )
            .scalar()
        ) or 0

        breakdown = []
        for label, val in [("product", prod), ("food", food), ("hotel", hotel), ("service", service)]:
            if val:
                breakdown.append({"category": label, "revenue": round(float(val), 2), "orders": 0})

        total = round(float(prod) + float(food) + float(hotel) + float(service), 2)

        return {
            "from_date":  from_date,
            "to_date":    to_date,
            "total":      total,
            "breakdown":  breakdown,
        }


# ---------------------------------------------------------------------------
# USER ADMIN
# ---------------------------------------------------------------------------

class CRUDAdminUser:

    def list_users(
        self, db: Session, *,
        user_type: Optional[str] = None,
        status:    Optional[str] = None,
        search:    Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple:
        """Returns (users, total_count)"""
        q = db.query(User)

        if user_type:
            q = q.filter(User.user_type == user_type)
        if status:
            q = q.filter(User.status == status)
        if search:
            pattern = f"%{search}%"
            q = q.filter(
                User.email.ilike(pattern)
                | User.phone.ilike(pattern)
                | User.full_name.ilike(pattern)
            )

        total = q.with_entities(func.count(User.id)).scalar() or 0
        users = q.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
        return users, total

    def update_status(self, db: Session, *, user_id: UUID, status: str) -> Optional[User]:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.status = status
            db.flush()
        return user


# ---------------------------------------------------------------------------
# BUSINESS ADMIN
# ---------------------------------------------------------------------------

class CRUDAdminBusiness:

    def list_businesses(
        self, db: Session, *,
        category:    Optional[str] = None,
        is_verified: Optional[bool] = None,
        search:      Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple:
        q = db.query(Business)

        if category:
            q = q.filter(Business.category == category)
        if is_verified is not None:
            q = q.filter(Business.is_verified == is_verified)
        if search:
            pattern = f"%{search}%"
            q = q.filter(Business.name.ilike(pattern))

        total = q.with_entities(func.count(Business.id)).scalar() or 0
        businesses = q.order_by(Business.created_at.desc()).offset(skip).limit(limit).all()
        return businesses, total

    def update_verification(self, db: Session, *, business_id: UUID, is_verified: bool, badge: str = None) -> Optional[Business]:
        biz = db.query(Business).filter(Business.id == business_id).first()
        if biz:
            biz.is_verified = is_verified
            if badge:
                biz.verification_badge = badge
            db.flush()
        return biz


# ---------------------------------------------------------------------------
# MODERATION QUEUE
# ---------------------------------------------------------------------------

class CRUDModerationQueue:

    def get_flagged_reviews(
        self, db: Session, *,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple:
        q = db.query(Review).filter(
            Review.status.in_([ReviewStatusEnum.FLAGGED, ReviewStatusEnum.PENDING])
        )
        total = q.with_entities(func.count(Review.id)).scalar() or 0
        reviews = q.order_by(Review.created_at.asc()).offset(skip).limit(limit).all()
        return reviews, total


# ---------------------------------------------------------------------------
# SINGLETONS
# ---------------------------------------------------------------------------

admin_dashboard_crud   = CRUDAdminDashboard()
admin_user_crud        = CRUDAdminUser()
admin_business_crud    = CRUDAdminBusiness()
moderation_queue_crud  = CRUDModerationQueue()