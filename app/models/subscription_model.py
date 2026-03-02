from sqlalchemy import (
    Column, String, Numeric, Boolean, Enum,
    ForeignKey, DateTime, Text
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class SubscriptionPlanTypeEnum(str, enum.Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    PRO_DRIVER = "pro_driver"


class BillingCycleEnum(str, enum.Enum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


# ============================================
# SUBSCRIPTION PLAN MODEL
# ============================================

class SubscriptionPlan(BaseModel):
    """Available subscription plans"""

    __tablename__ = "subscription_plans"

    plan_type = Column(Enum(SubscriptionPlanTypeEnum), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    monthly_price = Column(Numeric(10, 2), nullable=False)
    annual_price = Column(Numeric(10, 2), nullable=False)
    features = Column(Text, nullable=False)  # JSON stored as text
    is_active = Column(Boolean, default=True)

    # Relationships
    subscriptions = relationship(
        "Subscription",
        back_populates="plan",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<SubscriptionPlan {self.name}>"


# ============================================
# SUBSCRIPTION MODEL
# ============================================

class Subscription(BaseModel):
    """User subscriptions"""

    __tablename__ = "subscriptions"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id"), nullable=False)

    billing_cycle = Column(Enum(BillingCycleEnum), nullable=False)
    status = Column(String(50), default="active", index=True)

    started_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    auto_renew = Column(Boolean, default=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    plan = relationship("SubscriptionPlan", back_populates="subscriptions")

    def __repr__(self):
        return f"<Subscription {self.plan.name} - {self.status}>"