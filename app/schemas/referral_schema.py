from pydantic import BaseModel, field_validator
from typing import Optional, List
from decimal import Decimal
from datetime import datetime
from uuid import UUID

from app.models.referrals_model import ReferralStatus


# ============================================
# REFERRAL CODE
# ============================================

class ReferralCodeResponse(BaseModel):
    id: UUID
    user_id: UUID
    code: str
    total_referrals: int
    successful_referrals: int
    total_earnings: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================
# REFERRAL
# ============================================

class ReferralResponse(BaseModel):
    id: UUID
    referrer_id: UUID
    referred_id: UUID
    status: ReferralStatus
    referrer_reward: Decimal
    referred_reward: Decimal
    expires_at: Optional[datetime]
    completed_at: Optional[datetime]
    rewarded_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================
# AGGREGATE â€” used by referrals screen
# ============================================

class ReferralItem(BaseModel):
    """Single row in the referrals list shown to the referrer."""
    referral_id: UUID
    referee_name: str           # display name of the referred user
    joined_at: datetime         # when the referred user registered
    status: ReferralStatus
    reward: Decimal             # referrer_reward credited


class ReferralDashboard(BaseModel):
    """Complete payload for the ReferralsScreen."""
    referral_code: str
    reward_amount: Decimal      # per-invite reward (from platform config)
    total_referrals: int
    successful_referrals: int
    total_earned: Decimal
    referrals: List[ReferralItem]


# ============================================
# APPLY (used at registration)
# ============================================

class ApplyReferralCodeRequest(BaseModel):
    code: str

    @field_validator("code")
    @classmethod
    def code_uppercase(cls, v: str) -> str:
        return v.strip().upper()


class ApplyReferralCodeResponse(BaseModel):
    message: str
    referred_reward: Decimal    # bonus credit given to new user
