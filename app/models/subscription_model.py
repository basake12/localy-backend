from sqlalchemy import (
    Column, String, Numeric, Boolean, Enum,
    ForeignKey, DateTime
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
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


class SubscriptionStatusEnum(str, enum.Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PENDING = "pending"


# ============================================
# SUBSCRIPTION PLAN MODEL
# ============================================

class SubscriptionPlan(BaseModel):
    """Available subscription plans."""

    __tablename__ = "subscription_plans"

    plan_type = Column(Enum(SubscriptionPlanTypeEnum), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    monthly_price = Column(Numeric(10, 2), nullable=False)
    annual_price = Column(Numeric(10, 2), nullable=False)
    # JSONB for proper querying and indexing — stored as list of feature strings
    features = Column(JSONB, nullable=False, default=list)
    is_active = Column(Boolean, default=True)

    # Relationships
    subscriptions = relationship(
        "Subscription",
        back_populates="plan",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<SubscriptionPlan {self.name}>"


# ============================================
# SUBSCRIPTION MODEL
# ============================================

class Subscription(BaseModel):
    """User subscriptions."""

    __tablename__ = "subscriptions"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id"),
        nullable=False,
    )

    billing_cycle = Column(Enum(BillingCycleEnum), nullable=False)
    status = Column(
        Enum(SubscriptionStatusEnum),
        default=SubscriptionStatusEnum.ACTIVE,
        nullable=False,
        index=True,
    )

    started_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    auto_renew = Column(Boolean, default=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    plan = relationship("SubscriptionPlan", back_populates="subscriptions")
    user = relationship("User", back_populates="subscriptions")

    def __repr__(self) -> str:
        return f"<Subscription {self.plan_id} - {self.status}>"