"""
Analytics and reporting service.
"""
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, timedelta
from typing import Dict, Any, List

from app.models.user_model import User
from app.models.business_model import Business
from app.models.rider_model import Rider
from app.models.wallet_model import WalletTransaction
from app.models.analytics_model import DailyAnalyticsSnapshot


class AnalyticsService:
    """Analytics and reporting."""

    def get_dashboard_stats(self, db: Session) -> Dict[str, Any]:
        """Get real-time dashboard statistics."""
        # User stats
        total_users = db.query(User).count()
        total_customers = db.query(User).filter(User.user_type == "customer").count()
        total_businesses = db.query(Business).count()
        total_riders = db.query(Rider).count()

        # Today's stats
        today = datetime.utcnow().date()
        new_users_today = db.query(User).filter(
            func.date(User.created_at) == today
        ).count()

        # Active users (logged in last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        active_users = db.query(User).filter(
            User.last_login >= thirty_days_ago
        ).count()

        # Wallet stats — aggregate via SQL, not Python loop
        total_wallet_balance = db.query(
            func.coalesce(func.sum(WalletTransaction.amount), 0)
        ).filter(
            WalletTransaction.transaction_type == "credit",
            WalletTransaction.status == "completed",
        ).scalar() or 0

        return {
            "users": {
                "total": total_users,
                "customers": total_customers,
                "businesses": total_businesses,
                "riders": total_riders,
                "new_today": new_users_today,
                "active_30d": active_users,
            },
            "wallet": {
                "total_balance": float(total_wallet_balance),
            },
            "timestamp": datetime.utcnow().isoformat(),
        }

    def get_revenue_stats(
            self,
            db: Session,
            *,
            start_date: datetime,
            end_date: datetime,
    ) -> Dict[str, Any]:
        """Calculate revenue statistics for date range using SQL aggregation."""
        base_filter = and_(
            WalletTransaction.created_at >= start_date,
            WalletTransaction.created_at <= end_date,
            WalletTransaction.status == "completed",
        )

        total_revenue = db.query(
            func.coalesce(func.sum(WalletTransaction.amount), 0)
        ).filter(base_filter, WalletTransaction.transaction_type == "credit").scalar() or 0

        total_payouts = db.query(
            func.coalesce(func.sum(WalletTransaction.amount), 0)
        ).filter(base_filter, WalletTransaction.transaction_type == "debit").scalar() or 0

        transaction_count = db.query(func.count(WalletTransaction.id)).filter(
            base_filter
        ).scalar() or 0

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_revenue": float(total_revenue),
            "total_payouts": float(total_payouts),
            "net_revenue": float(total_revenue) - float(total_payouts),
            "transaction_count": transaction_count,
        }

    def get_category_stats(self, db: Session) -> Dict[str, Any]:
        """Get statistics per business category."""
        category_counts = db.query(
            Business.category,
            func.count(Business.id).label("count"),
        ).group_by(Business.category).all()

        stats = {}
        for category, count in category_counts:
            stats[category.value if hasattr(category, "value") else str(category)] = {
                "business_count": count,
                "avg_rating": 0.0,  # Enhanced in a separate query if needed
            }

        return stats

    def create_daily_snapshot(
        self, db: Session, snapshot_dt: datetime = None
    ) -> DailyAnalyticsSnapshot:
        """Create or replace daily analytics snapshot.

        Uses correct model field names: snapshot_date, customers,
        businesses, riders (not total_* prefixed variants).
        """
        if snapshot_dt is None:
            snapshot_dt = datetime.utcnow()

        snap_date = snapshot_dt.date()

        # Upsert: remove existing row for the date, then insert fresh
        existing = (
            db.query(DailyAnalyticsSnapshot)
            .filter(DailyAnalyticsSnapshot.snapshot_date == snap_date)
            .first()
        )
        if existing:
            db.delete(existing)
            db.flush()

        stats = self.get_dashboard_stats(db)

        snapshot = DailyAnalyticsSnapshot(
            snapshot_date=snap_date,                            # ✅ correct field
            total_users=stats["users"]["total"],
            new_users=stats["users"]["new_today"],
            active_users=stats["users"]["active_30d"],
            customers=stats["users"]["customers"],              # ✅ correct field
            businesses=stats["users"]["businesses"],            # ✅ correct field
            riders=stats["users"]["riders"],                    # ✅ correct field
            total_wallet_balance=stats["wallet"]["total_balance"],
        )

        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        return snapshot

    def get_growth_trends(
            self,
            db: Session,
            *,
            days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Get growth trends for last N days from snapshot table."""
        start_date = datetime.utcnow().date() - timedelta(days=days)

        snapshots = (
            db.query(DailyAnalyticsSnapshot)
            .filter(DailyAnalyticsSnapshot.snapshot_date >= start_date)  # ✅ correct field
            .order_by(DailyAnalyticsSnapshot.snapshot_date)
            .all()
        )

        return [
            {
                "date": str(s.snapshot_date),           # ✅ correct field
                "total_users": s.total_users,
                "new_users": s.new_users,
                "active_users": s.active_users,
                "total_businesses": s.businesses,       # ✅ correct field
                "total_riders": s.riders,               # ✅ correct field
            }
            for s in snapshots
        ]


# Singleton instance
analytics_service = AnalyticsService()