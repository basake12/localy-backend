"""
app/schemas/admin_schema.py

FIXES vs previous version:
  1. AdminUserOut.phone → phone_number (Blueprint §14 column name).
  2. AdminUserOut.user_type → role (Blueprint §14 column name).
  3. AdminUserOut.email: str → Optional[str] (nullable in §14).
  4. AdminBusinessOut.name → business_name (Blueprint §14 column name).
  5. UserStatusUpdate replaced with UserBanRequest — uses is_active/is_banned
     booleans (§14) with mandatory reason field (§11.1).
  6. WalletAdjustmentRequest ADDED — mandatory reason field (§11.1).
  7. SubscriptionUpdateRequest ADDED — for tier upgrade/downgrade (§11.2).
  8. ProductLimitOverrideRequest ADDED — for product limit override (§11.2).
  9. FeeConfigRequest ADDED — for platform fee rate changes (§11.3).
  10. CouponCreateRequest ADDED — for admin coupon issuance (§11.3).
  11. PromotionCreateRequest ADDED — for admin promotions (§11.3).
  12. PushNotificationRequest ADDED — for push to segments (§11.6).
  13. TermsUpdateRequest ADDED — for T&C/Privacy Policy update (§11.6).
  14. FeatureFlagRequest ADDED — for feature flag toggles (§11.6).
  15. WithdrawalActionRequest ADDED — for approve/hold withdrawals (§11.3).
  16. ContentRemoveRequest ADDED — for content removal with reason (§11.4).
"""

from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import Optional, List, Any, Dict
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID


# ── Shared ────────────────────────────────────────────────────────────────────

class DateRangeFilter(BaseModel):
    from_date: date
    to_date:   date


class PaginatedResponse(BaseModel):
    total: int
    skip:  int
    limit: int


# ── Dashboard ─────────────────────────────────────────────────────────────────

class UserSummary(BaseModel):
    total:        int
    customers:    int
    businesses:   int
    riders:       int
    new_today:    int
    active_today: int


class OrderSummary(BaseModel):
    total_orders:    int
    new_today:       int
    completed_today: int
    cancelled_today: int
    revenue_today:   float  # ₦ NGN


class DeliverySummary(BaseModel):
    total:           int
    completed_today: int
    avg_time_min:    float


class DashboardOverview(BaseModel):
    users:               UserSummary
    orders:              OrderSummary
    deliveries:          DeliverySummary
    avg_platform_rating: float
    pending_reviews:     int
    unread_flags:        int


# ── Trends ────────────────────────────────────────────────────────────────────

class TrendPoint(BaseModel):
    date:  date
    value: float


class TrendResponse(BaseModel):
    metric: str
    points: List[TrendPoint]


# ── Revenue Report ────────────────────────────────────────────────────────────

class RevenueBreakdown(BaseModel):
    category: str   # hotel | product | food | service | health | events
    revenue:  float
    orders:   int


class RevenueReport(BaseModel):
    from_date: date
    to_date:   date
    total:     float
    breakdown: List[RevenueBreakdown]


# ── §11.1 User Management ─────────────────────────────────────────────────────

class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:           UUID
    # FIX: was 'email: str' — email is Optional in §14
    email:        Optional[str] = None
    # FIX: was 'phone: str' — §14 column is phone_number
    phone_number: str
    # FIX: was 'user_type: str' — §14 column is role
    role:         str
    full_name:    str
    is_active:    bool
    is_banned:    bool
    is_phone_verified: bool
    referral_code: str
    created_at:   datetime


class AdminUserListOut(BaseModel):
    users: List[AdminUserOut]
    total: int
    skip:  int
    limit: int


class UserEditRequest(BaseModel):
    """
    Blueprint §11.1: Edit any user profile — name, phone, email, role, account status.
    """
    full_name:    Optional[str] = None
    phone_number: Optional[str] = None
    email:        Optional[str] = None


class UserBanRequest(BaseModel):
    """
    Blueprint §11.1:
    "Suspend, ban, or delete account — mandatory reason log (immutable)"
    FIX: replaces UserStatusUpdate which used a status string.
    Blueprint §14 uses is_active + is_banned booleans, not a status enum.
    reason is MANDATORY and cannot be empty.
    """
    action: str = Field(..., description="suspended | banned | reactivated")
    reason: str = Field(..., min_length=10, description="Mandatory reason — logged immutably")


class WalletAdjustmentRequest(BaseModel):
    """
    Blueprint §11.1:
    "Manually credit or debit any wallet — requires written reason,
     logged with admin ID, immutable. (admin_wallet_adjustments table)"
    """
    adjustment_type: str    = Field(..., description="credit | debit")
    amount:          Decimal = Field(..., gt=0, description="Amount in ₦ NGN — must be positive")
    reason:          str    = Field(..., min_length=10, description="Mandatory written reason")
    related_order_id: Optional[UUID] = None


class WalletAdjustmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                   UUID
    wallet_id:            UUID
    performed_by_admin_id: UUID
    adjustment_type:      str
    amount:               Decimal
    balance_before:       Decimal
    balance_after:        Decimal
    reason:               str
    created_at:           datetime


class AdminPinResetRequest(BaseModel):
    """Blueprint §11.1: Reset user PIN on their behalf (triggers SMS OTP)."""
    confirm: bool = Field(..., description="Must be True to confirm PIN reset")


# ── §11.2 Business Management ─────────────────────────────────────────────────

class AdminBusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                UUID
    user_id:           UUID
    # FIX: was 'name: str' — §14 column is business_name
    business_name:     str
    category:          str
    is_verified:       bool
    is_active:         bool
    subscription_tier: str
    subscription_tier_rank: int
    created_at:        datetime


class AdminBusinessListOut(BaseModel):
    businesses: List[AdminBusinessOut]
    total:      int
    skip:       int
    limit:      int


class BusinessVerifyUpdate(BaseModel):
    """Blueprint §11.2: Verify or reject business registration."""
    is_verified: bool
    reason:      Optional[str] = None   # required on rejection


class SubscriptionUpdateRequest(BaseModel):
    """
    Blueprint §11.2: Upgrade/downgrade subscription tier manually.
    Blueprint §15: POST /admin/businesses/{id}/subscription
    """
    tier: str = Field(..., description="free | starter | pro | enterprise")
    reason: str = Field(..., min_length=5)


class ProductLimitOverrideRequest(BaseModel):
    """
    Blueprint §11.2:
    "Override product listing limit for specific businesses
     (set product_limit_override=TRUE, product_limit_override_value=N)"
    Blueprint §2.2 implementation note: admin_panel override fields.
    """
    override_enabled: bool
    override_value:   Optional[int] = Field(None, ge=1, le=10000)


class FeaturedStatusRequest(BaseModel):
    """Blueprint §11.2: Set featured status manually (overrides rotation algorithm)."""
    is_featured:       bool
    featured_until:    Optional[datetime] = None  # None = indefinite
    reason:            Optional[str] = None


# ── §11.3 Financial Controls ──────────────────────────────────────────────────

class PlatformFeeConfig(BaseModel):
    """
    Blueprint §11.3:
    "Adjust platform fee rates — changes apply to NEW transactions only."
    Blueprint §5.4: ₦50 orders, ₦100 bookings, ₦50 tickets.
    """
    fee_standard_ngn: Optional[Decimal] = Field(None, gt=0, description="₦ per product/food order side")
    fee_booking_ngn:  Optional[Decimal] = Field(None, gt=0, description="₦ per hotel/service/health booking side")
    fee_ticket_ngn:   Optional[Decimal] = Field(None, gt=0, description="₦ per ticket (customer only)")
    reason:           str = Field(..., min_length=5, description="Reason for rate change — logged immutably")


class WithdrawalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:           UUID
    wallet_id:    UUID
    owner_type:   str   # business | rider
    amount:       Decimal
    bank_name:    Optional[str]
    account_number: Optional[str]
    status:       str
    requested_at: datetime


class WithdrawalListOut(BaseModel):
    withdrawals: List[WithdrawalOut]
    total:       int
    skip:        int
    limit:       int


class WithdrawalActionRequest(BaseModel):
    """
    Blueprint §11.3:
    "Approve or hold withdrawals above configurable threshold (per role)."
    """
    action: str = Field(..., description="approve | hold | reject")
    reason: Optional[str] = None   # required for hold/reject


class CouponCreateRequest(BaseModel):
    """
    Blueprint §11.3: Issue platform-funded coupons.
    Blueprint §9.2: full coupon type list.
    """
    code:                     str     = Field(..., min_length=3, max_length=32)
    coupon_type:              str     = Field(..., description="percentage_discount|fixed_amount_off|free_delivery|cashback_coupon|first_order|category_coupon|flash_coupon")
    value:                    Decimal = Field(..., gt=0)
    min_order_value:          Decimal = Field(Decimal("0"), ge=0)
    expiry_at:                datetime
    total_redemption_limit:   Optional[int] = None
    per_user_redemption_limit: int = 1
    category:                 Optional[str] = None
    business_id:              Optional[UUID] = None
    funded_by:                str = "platform"


class PromotionCreateRequest(BaseModel):
    """
    Blueprint §11.3:
    "All promotions: time-bounded. Admin can create, pause, edit, end without
     code deployment."
    """
    title:       str
    description: str
    type:        str    = Field(..., description="wallet_bonus|cashback|referral_boost|bundle")
    value:       Decimal
    start_at:    datetime
    end_at:      datetime
    config:      Dict[str, Any] = Field(default_factory=dict)


class ReferralConfigRequest(BaseModel):
    """
    Blueprint §11.3:
    "Set and adjust referral reward amounts."
    Blueprint §9.1: referrer ₦1,000; new user ₦1,000 off first order > ₦2,000.
    """
    reward_amount:           Optional[Decimal] = None
    new_user_discount:       Optional[Decimal] = None
    min_order_for_discount:  Optional[Decimal] = None
    is_active:               Optional[bool]    = None


# ── §11.4 Content Moderation ──────────────────────────────────────────────────

class ModerationQueueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    review_id:       UUID
    reviewer_id:     UUID
    reviewable_type: str
    reviewable_id:   UUID
    rating:          int
    title:           Optional[str]
    body:            Optional[str]
    flag_reason:     Optional[str]
    status:          str
    created_at:      datetime


class ModerationQueueOut(BaseModel):
    items: List[ModerationQueueItem]
    total: int
    skip:  int
    limit: int


class ContentRemoveRequest(BaseModel):
    """
    Blueprint §11.4:
    "Remove any content — business receives automated notification with reason."
    """
    reason:     str  = Field(..., min_length=10)
    notify_business: bool = True


class KeywordBlocklistRequest(BaseModel):
    """Blueprint §11.4: Keyword blocklist management (auto-review triggers)."""
    keywords:   List[str]
    action:     str = Field(..., description="add | remove")


class AppealResponseRequest(BaseModel):
    """
    Blueprint §11.4:
    "Appeal system: businesses can contest removal; admin responds within 5 business days."
    """
    decision: str  = Field(..., description="upheld | overturned")
    message:  str  = Field(..., min_length=20)


# ── §11.5 Analytics & Reporting ───────────────────────────────────────────────

class ModuleStats(BaseModel):
    module:       str
    order_volume: int
    booking_volume: int
    revenue:      float
    top_businesses: List[Dict[str, Any]]


class SubscriptionAnalytics(BaseModel):
    new_subscriptions:    int
    upgrades:             int
    downgrades:           int
    cancellations:        int
    churn_rate_pct:       float
    mrr:                  float   # Monthly Recurring Revenue ₦
    tier_breakdown:       Dict[str, int]


class PlatformAnalytics(BaseModel):
    dau:                  int
    mau:                  int
    gmv:                  float   # Gross Merchandise Value ₦
    total_revenue:        float
    wallet_adoption_rate: float   # % of users with funded wallets
    period_from:          date
    period_to:            date


# ── §11.6 Configuration Panel ─────────────────────────────────────────────────

class FeatureFlagRequest(BaseModel):
    """
    Blueprint §11.6:
    "Feature flag toggles: enable/disable any module or feature (no code deploy)"
    """
    key:     str  = Field(..., description="e.g. feature_flag_hotels_enabled")
    enabled: bool


class TermsUpdateRequest(BaseModel):
    """
    Blueprint §11.6:
    "T&C and Privacy Policy: admin edits via rich text editor;
     mobile always fetches latest version."
    Blueprint §15: PATCH /admin/content/terms
    Blueprint §3.1 step 8: "Full text fetched from backend (admin-editable, always latest version)"
    """
    content: str  = Field(..., min_length=100, description="Rich text HTML content")
    version: str  = Field(..., description="e.g. v2.1")


class PrivacyPolicyUpdateRequest(BaseModel):
    """Same structure as TermsUpdateRequest but for Privacy Policy."""
    content: str = Field(..., min_length=100)
    version: str


class PushNotificationRequest(BaseModel):
    """
    Blueprint §11.6 + §15: POST /admin/push-notifications
    "Push notifications: to all users, a segment (e.g. all Pro businesses in Lagos),
     or a specific user."
    """
    title:   str = Field(..., max_length=100)
    body:    str = Field(..., max_length=500)
    # Target segment — mutually exclusive with user_id
    segment: Optional[str] = Field(None, description="all | customers | businesses | riders | pro_businesses | enterprise_businesses")
    user_id: Optional[UUID] = None   # specific user — overrides segment


class ConfigValueRequest(BaseModel):
    """Generic admin config key-value update (§11.6 configuration panel)."""
    value:       str  = Field(..., description="New config value")
    description: Optional[str] = None


class ConfigValueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key:         str
    value:       str
    description: Optional[str]
    updated_at:  datetime


class SupportAgentCreateRequest(BaseModel):
    """
    Blueprint §11.6:
    "Manage support agent accounts and assign incoming tickets."
    """
    email:     EmailStr
    password:  str  = Field(..., min_length=10)
    full_name: str
    role:      str  = Field("support_agent", description="support_agent | admin")


# ── Admin User (admin_users table) ────────────────────────────────────────────

class AdminUserProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:           UUID
    email:        str
    full_name:    str
    role:         str
    is_active:    bool
    last_login_at: Optional[datetime]
    created_at:   datetime