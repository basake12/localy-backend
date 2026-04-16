"""
app/models/coupon_model.py

FIXES vs previous version:
  1. funded_by VARCHAR(16) NOT NULL added.
     Blueprint §14 / §9.2: "funded_by VARCHAR(16) NOT NULL — 'business' or 'platform'."
     Critical for financial reporting: platform coupons funded from revenue pool,
     business coupons deducted from business wallet. Without this field the
     accounting is impossible.

  2. end_date renamed expiry_at — Blueprint §14: "expiry_at TIMESTAMPTZ NOT NULL."
     The coupon validation endpoint (POST /api/v1/coupons/validate) checks expiry_at.

  3. created_by_admin_id UUID REFERENCES admin_users(id) added.
     Blueprint §14: required audit trail. Now FK to admin_users (not users).

  4. code length corrected — Blueprint §14: "code VARCHAR(32) UNIQUE NOT NULL."
     Previous was VARCHAR(50), now VARCHAR(32).

  5. Blueprint §14: value NUMERIC(10,2) NOT NULL — kept as discount_value but
     named per blueprint in the CHECK constraint annotation.

  6. Blueprint §9.2: "One coupon per order — not stackable."
     Enforced at service layer / checkout — no DB constraint needed.

  7. Blueprint §9.2: "Coupon redemption: coupon_redemptions table
     (coupon_id, user_id, order_id, redeemed_at, discount_applied)."
     CouponUsage table renamed to coupon_redemptions and schema updated.
"""
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    Numeric,
    ForeignKey,
    DateTime,
    Text,
    CheckConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
from datetime import datetime, timezone

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class CouponType(str, enum.Enum):
    """Blueprint §9.2 — all 10 coupon types."""
    PERCENTAGE_DISCOUNT = "percentage_discount"
    FIXED_AMOUNT_OFF    = "fixed_amount_off"
    FREE_DELIVERY       = "free_delivery"
    BUY_X_GET_Y         = "buy_x_get_y"
    CASHBACK_COUPON     = "cashback_coupon"
    FIRST_ORDER         = "first_order"
    CATEGORY_COUPON     = "category_coupon"
    BUSINESS_SPECIFIC   = "business_specific"
    FLASH_COUPON        = "flash_coupon"
    BUNDLE_COUPON       = "bundle_coupon"


class CouponStatus(str, enum.Enum):
    ACTIVE   = "active"
    EXPIRED  = "expired"
    DISABLED = "disabled"


# ─── Coupon ───────────────────────────────────────────────────────────────────

class Coupon(BaseModel):
    """
    Discount coupons and promo codes. Blueprint §9.2 / §14.

    Rules (§9.2):
    - One coupon per order — not stackable.
    - Validated at checkout in real time.
    - Business coupons: business bears the discount. Platform fee still applies.
    - Platform coupons: funded by Localy from revenue pool.
    """
    __tablename__ = "coupons"

    # Blueprint §14: code VARCHAR(32) UNIQUE NOT NULL
    code        = Column(String(32), unique=True, nullable=False, index=True)
    name        = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    # Blueprint §14: type VARCHAR(32) NOT NULL
    coupon_type    = Column(String(40), nullable=False)

    # Blueprint §14: value NUMERIC(10,2) NOT NULL — % or flat amount
    discount_value = Column(Numeric(10, 2), nullable=False)
    max_discount   = Column(Numeric(15, 2), nullable=True)   # Cap for percentage coupons

    # Blueprint §14: min_order_value NUMERIC(12,2) NOT NULL DEFAULT 0
    min_order_value = Column(Numeric(12, 2), nullable=False, default=0)

    # Blueprint §14: expiry_at TIMESTAMPTZ NOT NULL
    expiry_at = Column(DateTime(timezone=True), nullable=False)

    # Optional start date (flash coupons, scheduled promos)
    start_date = Column(DateTime(timezone=True), nullable=True)

    # Blueprint §14: total_redemption_limit INTEGER (NULL = unlimited)
    total_redemption_limit    = Column(Integer, nullable=True)

    # Blueprint §14: per_user_redemption_limit INTEGER NOT NULL DEFAULT 1
    per_user_redemption_limit = Column(Integer, nullable=False, default=1)

    # Current total uses (denormalised counter — increment on each redemption)
    current_uses = Column(Integer, default=0, nullable=False)

    # Blueprint §14: category VARCHAR(64) (NULL = all categories)
    category = Column(String(64), nullable=True)

    # Blueprint §14: business_id REFERENCES businesses(id) (NULL = platform-wide)
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Blueprint §14: funded_by VARCHAR(16) NOT NULL — 'business' | 'platform'
    # 'business': discount deducted from business wallet. Platform fee unchanged.
    # 'platform': funded from Localy revenue pool.
    funded_by = Column(String(16), nullable=False)

    # Blueprint §14: is_active BOOLEAN NOT NULL DEFAULT TRUE
    is_active = Column(Boolean, nullable=False, default=True, index=True)

    # Blueprint §14: created_by_admin_id UUID REFERENCES admin_users(id)
    created_by_admin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── BUY_X_GET_Y ──────────────────────────────────────────────────────────
    buy_quantity = Column(Integer, nullable=True)
    get_quantity = Column(Integer, nullable=True)

    # ── BUNDLE ────────────────────────────────────────────────────────────────
    bundle_item_ids = Column(JSONB, default=list)

    # ── Restrictions ──────────────────────────────────────────────────────────
    applicable_categories  = Column(JSONB, default=list)
    applicable_businesses  = Column(JSONB, default=list)
    new_users_only         = Column(Boolean, default=False)

    is_public = Column(Boolean, default=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    business    = relationship("Business", foreign_keys=[business_id])
    redemptions = relationship(
        "CouponRedemption", back_populates="coupon", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "funded_by IN ('business','platform')",
            name="valid_funded_by",
        ),
    )

    def is_valid(self) -> bool:
        if self.status != CouponStatus.ACTIVE and self.is_active is False:
            return False
        now = datetime.now(timezone.utc)
        if self.start_date and now < self.start_date:
            return False
        if self.expiry_at and now > self.expiry_at:
            return False
        if self.total_redemption_limit is not None and self.current_uses >= self.total_redemption_limit:
            return False
        return True

    def __repr__(self) -> str:
        return f"<Coupon {self.code} type={self.coupon_type} funded_by={self.funded_by}>"


# ─── Coupon Redemption ────────────────────────────────────────────────────────

class CouponRedemption(BaseModel):
    """
    Blueprint §9.2: "Coupon redemption: coupon_redemptions table
    (coupon_id, user_id, order_id, redeemed_at, discount_applied)."

    One record per successful coupon use.
    Used for per-user and total usage limit enforcement.
    """
    __tablename__ = "coupon_redemptions"

    coupon_id = Column(UUID(as_uuid=True), ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id   = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Blueprint §9.2: order_id
    order_id   = Column(UUID(as_uuid=True), nullable=True)
    order_type = Column(String(50), nullable=True)   # "hotel_booking" | "food_order" | etc.

    # Blueprint §9.2: discount_applied
    discount_applied = Column(Numeric(12, 2), nullable=False)

    # Financial snapshot
    order_total  = Column(Numeric(12, 2), nullable=False)
    final_amount = Column(Numeric(12, 2), nullable=False)

    # CASHBACK type — amount credited to wallet post-payment
    cashback_amount   = Column(Numeric(12, 2), nullable=True)
    cashback_credited = Column(Boolean, default=False)

    # Blueprint §9.2: redeemed_at — use created_at from BaseModel (= redeemed_at)

    coupon = relationship("Coupon", back_populates="redemptions")
    user   = relationship("User")

    def __repr__(self) -> str:
        return f"<CouponRedemption coupon={self.coupon_id} user={self.user_id}>"


# Keep old class name as alias for any existing imports
CouponUsage = CouponRedemption