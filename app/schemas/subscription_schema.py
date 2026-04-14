from pydantic import BaseModel, ConfigDict, computed_field, model_validator
from typing import Optional, List, Union
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.models.subscription_model import (
    SubscriptionPlanTypeEnum,
    BillingCycleEnum,
    SubscriptionStatusEnum,
)

# ─── Tier rank map (used by Flutter SubscriptionGate logic) ─────────────
_TIER_RANK: dict[str, int] = {
    "free": 0,
    "starter": 1,
    "pro": 2,
    "enterprise": 3,
    "pro_driver": 1,
}


class SubscriptionPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_type: SubscriptionPlanTypeEnum
    name: str
    monthly_price: Decimal
    annual_price: Decimal
    features: List[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def tier(self) -> int:
        """Integer tier rank: 0=free, 1=starter, 2=pro, 3=enterprise."""
        return _TIER_RANK.get(self.plan_type.value, 0)


class SubscriptionCreate(BaseModel):
    """
    Subscribe to a plan.

    plan_id accepts:
      - A UUID:             "48b5d6e4-82b1-4eb7-86fb-0ffd2dc3878e"
      - A plan type string: "starter" | "pro" | "enterprise" | "free"

    The router resolves a plan type string to its UUID before calling the service.
    """
    plan_id: Union[UUID, str]
    billing_cycle: BillingCycleEnum
    payment_method: str = "wallet"


class SubscriptionUpgrade(BaseModel):
    """
    Upgrade or downgrade the current subscription.

    Accepts either field name for the plan identifier:
      - plan_id:     matches the same field used in SubscriptionCreate (Postman-friendly)
      - new_plan_id: original field name kept as alias for backwards compatibility

    Both accept a UUID or a plan type string ("starter", "pro", "enterprise").
    """
    # Accept plan_id OR new_plan_id — whichever the client sends
    plan_id: Optional[Union[UUID, str]] = None
    new_plan_id: Optional[Union[UUID, str]] = None
    billing_cycle: BillingCycleEnum
    payment_method: str = "wallet"

    @model_validator(mode="after")
    def resolve_plan_identifier(self) -> "SubscriptionUpgrade":
        """
        Normalise: whichever field is provided, expose the value as plan_id.
        Raises if neither is provided.
        """
        if self.plan_id is None and self.new_plan_id is None:
            raise ValueError(
                "Provide either 'plan_id' or 'new_plan_id' with the target plan "
                "UUID or plan type (e.g. 'starter', 'pro', 'enterprise')."
            )
        # Prefer plan_id; fall back to new_plan_id
        if self.plan_id is None:
            self.plan_id = self.new_plan_id
        return self

    @property
    def resolved_plan_id(self) -> Union[UUID, str]:
        """Convenience accessor used by the router."""
        return self.plan_id  # type: ignore[return-value]


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    plan_id: UUID
    billing_cycle: BillingCycleEnum
    status: SubscriptionStatusEnum
    started_at: datetime
    expires_at: datetime
    auto_renew: bool
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    # Nested plan — Flutter reads plan details from here
    plan: Optional[SubscriptionPlanOut] = None

    @computed_field
    @property
    def plan_name(self) -> str:
        """Convenience field — Flutter reads this directly."""
        return self.plan.name if self.plan else ""

    @computed_field
    @property
    def amount(self) -> Decimal:
        """Current period charge based on billing cycle."""
        if not self.plan:
            return Decimal("0")
        return (
            self.plan.monthly_price
            if self.billing_cycle == BillingCycleEnum.MONTHLY
            else self.plan.annual_price
        )

    @computed_field
    @property
    def tier(self) -> int:
        """Resolved tier rank of the active plan."""
        if not self.plan:
            return 0
        return _TIER_RANK.get(self.plan.plan_type.value, 0)


class SubscriptionCancelRequest(BaseModel):
    reason: Optional[str] = None


class AutoRenewToggle(BaseModel):
    auto_renew: bool