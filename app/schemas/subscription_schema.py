from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.models.subscription_model import SubscriptionPlanTypeEnum, BillingCycleEnum


class SubscriptionPlanOut(BaseModel):
    id: UUID
    plan_type: SubscriptionPlanTypeEnum
    name: str
    monthly_price: Decimal
    annual_price: Decimal
    features: str  # JSON string
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SubscriptionCreate(BaseModel):
    plan_id: UUID
    billing_cycle: BillingCycleEnum
    payment_method: str = "wallet"


class SubscriptionOut(BaseModel):
    id: UUID
    user_id: UUID
    plan_id: UUID
    billing_cycle: BillingCycleEnum
    status: str
    started_at: datetime
    expires_at: datetime
    auto_renew: bool
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    plan: Optional[SubscriptionPlanOut] = None

    class Config:
        from_attributes = True


class SubscriptionCancelRequest(BaseModel):
    reason: Optional[str] = None