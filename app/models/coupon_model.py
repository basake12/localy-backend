from sqlalchemy import Column, String, Boolean, Integer, Numeric, Enum as SQLEnum, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
from datetime import datetime, timezone

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class CouponType(str, enum.Enum):
    PERCENTAGE = "percentage"           # % off total order
    FIXED = "fixed"                     # Fixed amount off
    FREE_DELIVERY = "free_delivery"     # Waives delivery fee
    BUY_X_GET_Y = "buy_x_get_y"        # e.g. Buy 2 get 1 free
    CASHBACK = "cashback"               # % back to wallet after purchase
    FIRST_ORDER = "first_order"         # Only on user's first transaction
    CATEGORY = "category"               # Applies to a specific business category
    BUSINESS_SPECIFIC = "business_specific"  # Only redeemable at one business
    FLASH = "flash"                     # Short time window + limited redemptions
    BUNDLE = "bundle"                   # Specific item combinations in cart


class CouponStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    DISABLED = "disabled"


# ============================================
# MODELS
# ============================================

class Coupon(BaseModel):
    """
    Discount coupons and promo codes.
    Supports all 10 Blueprint coupon types — both platform-funded and business-funded.
    """

    __tablename__ = "coupons"

    # ── Basic info ────────────────────────────────────────────────────────────
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    # ── Discount details ──────────────────────────────────────────────────────
    coupon_type = Column(SQLEnum(CouponType), nullable=False)
    discount_value = Column(Numeric(10, 2), nullable=False)  # % or fixed amount
    max_discount = Column(Numeric(15, 2), nullable=True)     # Cap for percentage coupons

    # ── BUY_X_GET_Y fields ───────────────────────────────────────────────────
    # e.g. buy_quantity=2, get_quantity=1 means "buy 2 get 1 free"
    buy_quantity = Column(Integer, nullable=True)
    get_quantity = Column(Integer, nullable=True)

    # ── CASHBACK fields ───────────────────────────────────────────────────────
    # cashback_rate is stored in discount_value for CASHBACK type (% back to wallet)
    # No additional column needed — discount_value serves as the cashback %.

    # ── BUNDLE fields ─────────────────────────────────────────────────────────
    # List of item/product IDs (as strings) that must all be in the cart
    bundle_item_ids = Column(JSONB, default=list)

    # ── Validity ──────────────────────────────────────────────────────────────
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)

    # ── Usage limits ──────────────────────────────────────────────────────────
    max_uses = Column(Integer, nullable=True)         # Total uses (None = unlimited)
    max_uses_per_user = Column(Integer, default=1)    # Per-user cap
    current_uses = Column(Integer, default=0)         # Running total

    # ── Restrictions ──────────────────────────────────────────────────────────
    min_order_value = Column(Numeric(15, 2), nullable=True)
    applicable_categories = Column(JSONB, default=list)   # ["food", "hotels", ...]
    applicable_businesses = Column(JSONB, default=list)   # List of business UUIDs
    new_users_only = Column(Boolean, default=False)       # FIRST_ORDER gate

    # ── Ownership (None = platform-wide) ──────────────────────────────────────
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status = Column(SQLEnum(CouponStatus), default=CouponStatus.ACTIVE, nullable=False, index=True)
    is_public = Column(Boolean, default=True)  # False = private/targeted code

    # ── Relationships ─────────────────────────────────────────────────────────
    business = relationship("Business", foreign_keys=[business_id])
    usages = relationship("CouponUsage", back_populates="coupon", cascade="all, delete-orphan")

    # ─────────────────────────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        """Check if coupon is currently active and within its time window."""
        if self.status != CouponStatus.ACTIVE:
            return False

        now = datetime.now(timezone.utc)

        if self.start_date and now < self.start_date:
            return False

        if self.end_date and now > self.end_date:
            return False

        if self.max_uses is not None and self.current_uses >= self.max_uses:
            return False

        return True

    def can_user_use(self, usage_count: int) -> bool:
        """
        Check if a specific user may still use this coupon.
        usage_count = how many times they have already used it.
        """
        if not self.is_valid():
            return False

        if self.max_uses_per_user is not None and usage_count >= self.max_uses_per_user:
            return False

        return True

    def is_flash_active(self) -> bool:
        """
        FLASH coupons must have an end_date and must still be within window.
        Same as is_valid() but semantic alias for clarity in service layer.
        """
        return self.is_valid()


class CouponUsage(BaseModel):
    """
    Records each time a coupon is successfully used.
    Used for per-user and total usage enforcement.
    """

    __tablename__ = "coupon_usages"

    coupon_id = Column(UUID(as_uuid=True), ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Order/booking reference
    order_type = Column(String(50), nullable=True)   # "hotel_booking", "food_order", etc.
    order_id = Column(UUID(as_uuid=True), nullable=True)

    # Financial snapshot at time of use
    discount_amount = Column(Numeric(15, 2), nullable=False)
    order_total = Column(Numeric(15, 2), nullable=False)
    final_amount = Column(Numeric(15, 2), nullable=False)

    # For CASHBACK type — amount credited to wallet after payment
    cashback_amount = Column(Numeric(15, 2), nullable=True)
    cashback_credited = Column(Boolean, default=False)

    # Relationships
    coupon = relationship("Coupon", back_populates="usages")
    user = relationship("User")