from sqlalchemy import Column, String, Boolean, Numeric, Enum as SQLEnum, ForeignKey, DateTime, Integer
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class ReferralStatus(str, enum.Enum):
    PENDING = "pending"  # Referred user registered but not completed action
    COMPLETED = "completed"  # Referred user completed required action
    REWARDED = "rewarded"  # Reward given to referrer
    EXPIRED = "expired"


# ============================================
# MODELS
# ============================================

class ReferralCode(BaseModel):
    """
    User referral codes.
    Each user gets a unique referral code.
    """

    __tablename__ = "referral_codes"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False,
                     index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)

    # Stats
    total_referrals = Column(Integer, default=0)
    successful_referrals = Column(Integer, default=0)  # Completed required action
    total_earnings = Column(Numeric(15, 2), default=0.00)  # Total rewards earned

    # Relationships
    user = relationship("User")
    referrals = relationship("Referral", back_populates="referral_code", foreign_keys="Referral.referral_code_id")


class Referral(BaseModel):
    """
    Track individual referrals.
    """

    __tablename__ = "referrals"

    referral_code_id = Column(UUID(as_uuid=True), ForeignKey("referral_codes.id", ondelete="CASCADE"), nullable=False,
                              index=True)
    referrer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                         index=True)  # Person who referred
    referred_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                         index=True)  # Person who was referred

    # Reward details
    status = Column(SQLEnum(ReferralStatus), default=ReferralStatus.PENDING, nullable=False, index=True)
    referrer_reward = Column(Numeric(15, 2), default=0.00)  # Reward for referrer
    referred_reward = Column(Numeric(15, 2), default=0.00)  # Reward for referred user

    # Completion tracking
    completed_at = Column(DateTime(timezone=True), nullable=True)
    rewarded_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    referral_code = relationship("ReferralCode", back_populates="referrals", foreign_keys=[referral_code_id])
    referrer = relationship("User", foreign_keys=[referrer_id])
    referred = relationship("User", foreign_keys=[referred_id])