"""
app/schemas/promotions_schema.py

Request and response schemas for the Promotions feature.

Per Blueprint v2.0 Section 4.1.3 & 7.3:
- Admin creates/manages promotions
- Customers view active promotions and their own streak progress
- Promotion credits appear as wallet transactions
"""
from pydantic import BaseModel, Field, model_validator, ConfigDict
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.models.promotions_model import (
    PromotionType,
    PromotionStatus,
    StreakActionType,
)


# ─── Promotion Create (Admin) ─────────────────────────────────────────────────

class PromotionCreate(BaseModel):
    """Admin endpoint — create a new promotion."""

    title:       str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = None
    banner_url:  Optional[str] = None

    promotion_type: PromotionType
    start_date:     datetime
    end_date:       datetime

    # Limits
    max_total_redemptions: Optional[int] = Field(None, gt=0)
    max_per_user:          int           = Field(1, ge=1)
    is_public:             bool          = True

    # FUNDING_BONUS
    min_funding_amount: Optional[Decimal] = Field(None, gt=0)
    bonus_amount:       Optional[Decimal] = Field(None, gt=0)

    # CASHBACK_EVENT
    cashback_percentage:   Optional[Decimal] = Field(None, gt=0, le=100)
    max_cashback_amount:   Optional[Decimal] = Field(None, gt=0)
    max_cashback_per_user: Optional[Decimal] = Field(None, gt=0)
    applicable_modules:    Optional[List[str]] = None

    # DOUBLE_REFERRAL
    referral_multiplier: Optional[Decimal] = Field(None, gt=1)

    # STREAK_REWARD
    streak_action_type:   Optional[StreakActionType] = None
    streak_target_count:  Optional[int]              = Field(None, gt=1)
    streak_reward_amount: Optional[Decimal]          = Field(None, gt=0)

    @model_validator(mode="after")
    def validate_type_fields(self) -> "PromotionCreate":
        t = self.promotion_type

        if t == PromotionType.FUNDING_BONUS:
            if not self.min_funding_amount or not self.bonus_amount:
                raise ValueError(
                    "FUNDING_BONUS requires min_funding_amount and bonus_amount"
                )

        elif t == PromotionType.CASHBACK_EVENT:
            if not self.cashback_percentage:
                raise ValueError("CASHBACK_EVENT requires cashback_percentage")
            if not self.applicable_modules:
                raise ValueError(
                    "CASHBACK_EVENT requires applicable_modules (e.g. ['food', 'hotels'])"
                )

        elif t == PromotionType.DOUBLE_REFERRAL:
            if not self.referral_multiplier:
                raise ValueError("DOUBLE_REFERRAL requires referral_multiplier")

        elif t == PromotionType.STREAK_REWARD:
            if not self.streak_action_type:
                raise ValueError("STREAK_REWARD requires streak_action_type")
            if not self.streak_target_count:
                raise ValueError("STREAK_REWARD requires streak_target_count")
            if not self.streak_reward_amount:
                raise ValueError("STREAK_REWARD requires streak_reward_amount")

        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")

        return self


class PromotionUpdate(BaseModel):
    """Admin endpoint — partial update."""
    title:                 Optional[str]            = None
    description:           Optional[str]            = None
    banner_url:            Optional[str]            = None
    status:                Optional[PromotionStatus] = None
    end_date:              Optional[datetime]        = None
    max_total_redemptions: Optional[int]            = Field(None, gt=0)
    is_public:             Optional[bool]           = None

    # Allow updating bonus amount mid-run (takes effect on new redemptions)
    bonus_amount:          Optional[Decimal]        = Field(None, gt=0)
    cashback_percentage:   Optional[Decimal]        = Field(None, gt=0, le=100)
    max_cashback_amount:   Optional[Decimal]        = Field(None, gt=0)
    referral_multiplier:   Optional[Decimal]        = Field(None, gt=1)
    streak_reward_amount:  Optional[Decimal]        = Field(None, gt=0)


# ─── Promotion Out (Public) ───────────────────────────────────────────────────

class PromotionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:             UUID
    title:          str
    description:    Optional[str]  = None
    banner_url:     Optional[str]  = None
    promotion_type: PromotionType
    status:         PromotionStatus

    start_date: datetime
    end_date:   datetime

    # FUNDING_BONUS
    min_funding_amount: Optional[Decimal] = None
    bonus_amount:       Optional[Decimal] = None

    # CASHBACK_EVENT
    cashback_percentage:   Optional[Decimal]    = None
    max_cashback_amount:   Optional[Decimal]    = None
    max_cashback_per_user: Optional[Decimal]    = None
    applicable_modules:    Optional[List[str]]  = None

    # DOUBLE_REFERRAL
    referral_multiplier: Optional[Decimal] = None

    # STREAK_REWARD
    streak_action_type:   Optional[StreakActionType] = None
    streak_target_count:  Optional[int]              = None
    streak_reward_amount: Optional[Decimal]          = None

    max_per_user:         int
    current_redemptions:  int
    max_total_redemptions: Optional[int] = None
    is_public:            bool

    created_at: datetime
    updated_at: datetime


class PromotionListOut(BaseModel):
    promotions: List[PromotionOut]
    total:      int
    page:       int
    page_size:  int


# ─── Streak Progress Out ──────────────────────────────────────────────────────

class StreakProgressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            UUID
    promotion_id:  UUID
    user_id:       UUID
    current_count: int
    target_count:  int
    completed:     bool
    remaining:     int
    progress_pct:  float
    last_action_at: Optional[datetime] = None

    # Promotion summary for display
    promotion_title:        str
    promotion_end_date:     datetime
    streak_reward_amount:   Optional[Decimal] = None


class StreakProgressListOut(BaseModel):
    progresses: List[StreakProgressOut]


# ─── Promotion Redemption Out ─────────────────────────────────────────────────

class PromotionRedemptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                    UUID
    promotion_id:          UUID
    user_id:               UUID
    amount_credited:       Decimal
    trigger_type:          Optional[str]  = None
    wallet_transaction_id: Optional[UUID] = None
    meta_data:             Optional[dict] = None
    created_at:            datetime


class PromotionRedemptionListOut(BaseModel):
    redemptions: List[PromotionRedemptionOut]
    total:       int
    page:        int
    page_size:   int


# ─── Internal trigger schemas (used by other services) ───────────────────────

class FundingBonusCheck(BaseModel):
    """Return value when checking if a top-up qualifies for a bonus."""
    eligible:   bool
    promotion:  Optional[PromotionOut] = None
    bonus_amount: Optional[Decimal]   = None


class CashbackCheck(BaseModel):
    """Return value when checking cashback eligibility after an order."""
    eligible:        bool
    promotion:       Optional[PromotionOut] = None
    cashback_amount: Optional[Decimal]     = None


class ActiveReferralMultiplier(BaseModel):
    """Current referral multiplier — 1.0 means no active double-referral event."""
    multiplier:  Decimal = Decimal("1.0")
    promotion:   Optional[PromotionOut] = None
    base_amount: Decimal = Decimal("1000.00")
    final_amount: Decimal = Decimal("1000.00")


# ─── Admin analytics ─────────────────────────────────────────────────────────

class PromotionAnalytics(BaseModel):
    promotion_id:        UUID
    promotion_title:     str
    promotion_type:      PromotionType
    total_redemptions:   int
    total_amount_issued: Decimal
    unique_users:        int
    start_date:          datetime
    end_date:            datetime
    status:              PromotionStatus