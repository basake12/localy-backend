from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime
from uuid import UUID


# ============================================
# DATE RANGE FILTER (shared)
# ============================================

class DateRangeFilter(BaseModel):
    from_date: date
    to_date:   date


# ============================================
# DASHBOARD OVERVIEW
# ============================================

class UserSummary(BaseModel):
    total:        int
    customers:    int
    businesses:   int
    riders:       int
    new_today:    int
    active_today: int


class OrderSummary(BaseModel):
    total_orders:     int
    new_today:        int
    completed_today:  int
    cancelled_today:  int
    revenue_today:    float    # NGN


class DeliverySummary(BaseModel):
    total:            int
    completed_today:  int
    avg_time_min:     float


class DashboardOverview(BaseModel):
    users:      UserSummary
    orders:     OrderSummary
    deliveries: DeliverySummary
    avg_platform_rating: float
    pending_reviews:     int    # flagged / pending moderation
    unread_flags:        int


# ============================================
# TREND DATA  (time-series for charts)
# ============================================

class TrendPoint(BaseModel):
    date:  date
    value: float


class TrendResponse(BaseModel):
    metric: str
    points: List[TrendPoint]


# ============================================
# USER MANAGEMENT (admin list)
# ============================================

class AdminUserOut(BaseModel):
    id:            UUID
    email:         str
    phone:         str
    user_type:     str
    status:        str
    full_name:     str
    is_email_verified: bool
    is_phone_verified: bool
    created_at:    datetime

    class Config:
        from_attributes = True


class AdminUserListOut(BaseModel):
    users: List[AdminUserOut]
    total: int
    skip:  int
    limit: int


class UserStatusUpdate(BaseModel):
    status: str   # active | suspended | banned


# ============================================
# BUSINESS MANAGEMENT
# ============================================

class AdminBusinessOut(BaseModel):
    id:                  UUID
    user_id:             UUID
    name:                str
    category:            str
    verification_badge:  str
    is_active:           bool
    is_verified:         bool
    created_at:          datetime

    class Config:
        from_attributes = True


class AdminBusinessListOut(BaseModel):
    businesses: List[AdminBusinessOut]
    total:      int
    skip:       int
    limit:      int


class BusinessVerifyUpdate(BaseModel):
    is_verified:        bool
    verification_badge: Optional[str] = None


# ============================================
# REVIEW MODERATION QUEUE
# ============================================

class ModerationQueueItem(BaseModel):
    review_id:       UUID
    reviewer_id:     UUID
    reviewable_type: str
    reviewable_id:   UUID
    rating:          int
    title:           Optional[str] = None
    body:            Optional[str] = None
    flag_reason:     Optional[str] = None
    status:          str
    created_at:      datetime

    class Config:
        from_attributes = True


class ModerationQueueOut(BaseModel):
    items: List[ModerationQueueItem]
    total: int
    skip:  int
    limit: int


# ============================================
# REVENUE REPORT
# ============================================

class RevenueBreakdown(BaseModel):
    category:  str       # hotel | product | food | service | ...
    revenue:   float
    orders:    int


class RevenueReport(BaseModel):
    from_date:   date
    to_date:     date
    total:       float
    breakdown:   List[RevenueBreakdown]