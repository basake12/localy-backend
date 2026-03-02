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

        # Wallet stats
        total_wallet_balance = db.query(
            func.sum(WalletTransaction.balance_after)
        ).scalar() or 0

        return {
            "users": {
                "total": total_users,
                "customers": total_customers,
                "businesses": total_businesses,
                "riders": total_riders,
                "new_today": new_users_today,
                "active_30d": active_users
            },
            "wallet": {
                "total_balance": float(total_wallet_balance)
            },
            "timestamp": datetime.utcnow().isoformat()
        }

    def get_revenue_stats(
            self,
            db: Session,
            *,
            start_date: datetime,
            end_date: datetime
    ) -> Dict[str, Any]:
        """Calculate revenue statistics for date range."""
        # Get all completed transactions
        transactions = db.query(WalletTransaction).filter(
            and_(
                WalletTransaction.created_at >= start_date,
                WalletTransaction.created_at <= end_date,
                WalletTransaction.status == "completed"
            )
        ).all()

        total_revenue = sum(float(t.amount) for t in transactions if t.transaction_type == "credit")
        total_payouts = sum(float(t.amount) for t in transactions if t.transaction_type == "debit")

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_revenue": total_revenue,
            "total_payouts": total_payouts,
            "net_revenue": total_revenue - total_payouts,
            "transaction_count": len(transactions)
        }

    def get_category_stats(self, db: Session) -> Dict[str, Any]:
        """Get statistics per business category."""
        # Group businesses by category
        category_counts = db.query(
            Business.category,
            func.count(Business.id).label('count')
        ).group_by(Business.category).all()

        stats = {}
        for category, count in category_counts:
            stats[category.value] = {
                "business_count": count,
                "avg_rating": 0.0  # Can be enhanced
            }

        return stats

    def create_daily_snapshot(self, db: Session, date: datetime = None) -> DailyAnalyticsSnapshot:
        """Create daily analytics snapshot."""
        if date is None:
            date = datetime.utcnow()

        stats = self.get_dashboard_stats(db)

        snapshot = DailyAnalyticsSnapshot(
            date=date.date(),
            total_users=stats['users']['total'],
            new_users=stats['users']['new_today'],
            active_users=stats['users']['active_30d'],
            total_customers=stats['users']['customers'],
            total_businesses=stats['users']['businesses'],
            total_riders=stats['users']['riders'],
            total_wallet_balance=stats['wallet']['total_balance']
        )

        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        return snapshot

    def get_growth_trends(
            self,
            db: Session,
            *,
            days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get growth trends for last N days."""
        start_date = datetime.utcnow().date() - timedelta(days=days)

        snapshots = db.query(DailyAnalyticsSnapshot).filter(
            DailyAnalyticsSnapshot.date >= start_date
        ).order_by(DailyAnalyticsSnapshot.date).all()

        return [
            {
                "date": str(s.date),
                "total_users": s.total_users,
                "new_users": s.new_users,
                "active_users": s.active_users,
                "total_businesses": s.total_businesses
            }
            for s in snapshots
        ]


# Singleton instance
analytics_service = AnalyticsService()

