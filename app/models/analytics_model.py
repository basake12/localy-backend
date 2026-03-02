from sqlalchemy import Column, String, Integer, Numeric, Date, Index
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import BaseModel


class DailyAnalyticsSnapshot(BaseModel):
    """
    One row per day.  Populated by a nightly Celery task that walks
    the live tables and writes aggregates here.

    This keeps the admin dashboard fast without hammering the OLTP DB
    with heavy GROUP BY queries every time.
    """

    __tablename__ = "daily_analytics_snapshots"

    snapshot_date = Column(Date, unique=True, nullable=False, index=True)

    # ── User metrics ──
    total_users        = Column(Integer, default=0)
    new_users          = Column(Integer, default=0)   # created today
    active_users       = Column(Integer, default=0)   # logged in today
    customers          = Column(Integer, default=0)
    businesses         = Column(Integer, default=0)
    riders             = Column(Integer, default=0)

    # ── Commerce metrics ──
    total_orders       = Column(Integer, default=0)   # all order types
    new_orders         = Column(Integer, default=0)
    completed_orders   = Column(Integer, default=0)
    cancelled_orders   = Column(Integer, default=0)
    total_revenue      = Column(Numeric(15, 2), default=0)   # NGN
    new_revenue        = Column(Numeric(15, 2), default=0)

    # ── Delivery metrics ──
    total_deliveries       = Column(Integer, default=0)
    completed_deliveries   = Column(Integer, default=0)
    avg_delivery_time_min  = Column(Numeric(6, 2), default=0)

    # ── Engagement ──
    total_reviews     = Column(Integer, default=0)
    new_reviews       = Column(Integer, default=0)
    avg_platform_rating = Column(Numeric(3, 2), default=0)
    total_messages    = Column(Integer, default=0)   # chat messages sent today

    # ── Wallet ──
    total_wallet_balance    = Column(Numeric(15, 2), default=0)
    total_wallet_topups     = Column(Numeric(15, 2), default=0)
    total_wallet_payouts    = Column(Numeric(15, 2), default=0)

    # ── Flexible bucket for extra KPIs ──
    extra = Column(JSONB, default=dict)