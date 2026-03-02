from sqlalchemy import Column, String, Boolean, Integer, Numeric, Enum as SQLEnum, ForeignKey, DateTime, Text, \
    CheckConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
from datetime import datetime

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class CouponType(str, enum.Enum):
    PERCENTAGE = "percentage"  # % off
    FIXED = "fixed"  # Fixed amount off
    FREE_DELIVERY = "free_delivery"


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
    """

    __tablename__ = "coupons"

    # Basic info
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    # Discount details
    coupon_type = Column(SQLEnum(CouponType), nullable=False)
    discount_value = Column(Numeric(10, 2), nullable=False)  # Percentage or fixed amount
    max_discount = Column(Numeric(15, 2), nullable=True)  # Max discount for percentage coupons

    # Validity
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)

    # Usage limits
    max_uses = Column(Integer, nullable=True)  # Total uses allowed
    max_uses_per_user = Column(Integer, default=1)  # Uses per user
    current_uses = Column(Integer, default=0)  # Current total uses

    # Restrictions
    min_order_value = Column(Numeric(15, 2), nullable=True)  # Minimum order value
    applicable_categories = Column(JSONB, default=list)  # ["hotels", "food", ...]
    applicable_businesses = Column(JSONB, default=list)  # List of business IDs
    new_users_only = Column(Boolean, default=False)

    # Ownership (if business-specific)
    business_id = Column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True,
                         index=True)

    # Status
    status = Column(SQLEnum(CouponStatus), default=CouponStatus.ACTIVE, nullable=False, index=True)
    is_public = Column(Boolean, default=True)  # Show in app vs private code

    # Relationships
    business = relationship("Business", foreign_keys=[business_id])
    usages = relationship("CouponUsage", back_populates="coupon", cascade="all, delete-orphan")

    # NOTE: CheckConstraints removed to prevent PostgreSQL enum comparison errors in migrations
    # Validation is handled in application layer via is_valid() and can_user_use() methods

    def is_valid(self) -> bool:
        """Check if coupon is currently valid."""
        if self.status != CouponStatus.ACTIVE:
            return False

        now = datetime.utcnow()

        if self.start_date and now < self.start_date:
            return False

        if self.end_date and now > self.end_date:
            return False

        if self.max_uses and self.current_uses >= self.max_uses:
            return False

        return True

    def can_user_use(self, user_id: UUID, usage_count: int) -> bool:
        """Check if user can use this coupon."""
        if not self.is_valid():
            return False

        if self.max_uses_per_user and usage_count >= self.max_uses_per_user:
            return False

        return True


class CouponUsage(BaseModel):
    """
    Track coupon usage by users.
    """

    __tablename__ = "coupon_usages"

    coupon_id = Column(UUID(as_uuid=True), ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Order/booking reference
    order_type = Column(String(50), nullable=True)  # "hotel_booking", "product_order", etc.
    order_id = Column(UUID(as_uuid=True), nullable=True)

    # Discount applied
    discount_amount = Column(Numeric(15, 2), nullable=False)
    order_total = Column(Numeric(15, 2), nullable=False)
    final_amount = Column(Numeric(15, 2), nullable=False)

    # Relationships
    coupon = relationship("Coupon", back_populates="usages")
    user = relationship("User")