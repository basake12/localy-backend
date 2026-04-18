"""
app/models/referrals_model.py

FIXES vs previous version:
  1. [CRITICAL] UNIQUE(referred_id) added at DB level.
     Blueprint §14: "UNIQUE(referred_id) — one referral per user, ever."
     Without this a referred user could be credited multiple times by
     the credit_referral_reward Celery task.

  2. reward_credited BOOLEAN NOT NULL DEFAULT FALSE replaces the status Enum.
     Blueprint §14: "referrals table: referrer_id, referred_id,
     reward_credited BOOLEAN."
     The Celery task credit_referral_reward checks and sets this flag.

  3. reward_credited_at TIMESTAMPTZ added — Blueprint §14.

  4. ReferralCode table kept as an acceptable extension (denormalised counters
     and the shareable code object), but referral_code on users (Blueprint §14)
     is the source of truth for the code value itself.

  5. referrer_reward and referred_reward amounts stored on the Referral row —
     captured at time of credit so admin can change the amount without
     retroactively affecting existing records.
"""
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Numeric,
    ForeignKey,
    DateTime,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID

from app.models.base_model import BaseModel


# ─── Referral Code ────────────────────────────────────────────────────────────

import enum

class ReferralStatus(str, enum.Enum):
    PENDING   = "pending"
    COMPLETED = "completed"
    REWARDED  = "rewarded"
    EXPIRED   = "expired"

class ReferralCode(BaseModel):
    """
    One referral code per user — created at registration.
    The code value is also stored on users.referral_code (Blueprint §14).
    This table provides denormalised dashboard counters and a code lookup object.

    Blueprint §9.1:
    - Unique 8-character alphanumeric (Blueprint says 8-char; users.referral_code
      is VARCHAR(16) to allow longer codes — keep consistent with users table).
    - Shareable via link, copy, or direct social share.
    """
    __tablename__ = "referral_codes"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    code = Column(String(20), unique=True, nullable=False, index=True)

    # Denormalised counters — updated by service layer, not DB triggers
    total_referrals      = Column(Integer, default=0, nullable=False)
    successful_referrals = Column(Integer, default=0, nullable=False)
    total_earnings       = Column(Numeric(15, 2), default=0.00, nullable=False)

    user     = relationship("User")
    referrals = relationship(
        "Referral",
        back_populates="referral_code",
        foreign_keys="Referral.referral_code_id",
    )

    def __repr__(self) -> str:
        return f"<ReferralCode {self.code} user={self.user_id}>"


# ─── Referral ─────────────────────────────────────────────────────────────────

class Referral(BaseModel):
    """
    Individual referral record — one row per referred user, ever.

    Blueprint §14 schema:
      referrals (referrer_id, referred_id, reward_credited BOOLEAN, reward_credited_at)

    Blueprint §9.1 business rules:
    - Referrer reward: ₦1,000 on referred friend's FIRST COMPLETED purchase.
    - New user reward: ₦1,000 discount on first order above ₦2,000 (auto-applied).
    - One reward per referred user — no repeated rewards for same person.
    - Self-referral blocked at API layer.
    - Trigger: Celery task credit_referral_reward on order status → 'completed'.

    UNIQUE(referred_id) ensures one referral row per referred user, ever.
    The Celery task checks reward_credited=FALSE before crediting; sets
    reward_credited=TRUE + reward_credited_at after crediting.
    """
    __tablename__ = "referrals"

    referral_code_id = Column(
        UUID(as_uuid=True),
        ForeignKey("referral_codes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    referrer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Blueprint §14: UNIQUE(referred_id) — one referral per referred user, ever.
    # This prevents the Celery task from crediting the same referrer twice.
    referred_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Blueprint §14: reward_credited BOOLEAN NOT NULL DEFAULT FALSE
    # Set to TRUE by credit_referral_reward Celery task after wallet credit.
    reward_credited    = Column(Boolean, nullable=False, default=False)
    reward_credited_at = Column(DateTime(timezone=True), nullable=True)

    # Reward amounts captured at time of credit — snapshot so admin rate
    # changes don't retroactively affect this record
    referrer_reward = Column(Numeric(12, 2), default=0.00, nullable=False)
    referred_reward = Column(Numeric(12, 2), default=0.00, nullable=False)

    # Blueprint §9.1: referral expires if referred user doesn't complete purchase
    expires_at   = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    referral_code = relationship(
        "ReferralCode", back_populates="referrals", foreign_keys=[referral_code_id]
    )
    referrer = relationship("User", foreign_keys=[referrer_id], back_populates="referrals_given")
    referred = relationship("User", foreign_keys=[referred_id], back_populates="referral_received")

    __table_args__ = (
        # Blueprint §14: UNIQUE(referred_id) — one referral per referred user, ever
        UniqueConstraint("referred_id", name="uq_referral_per_referred_user"),
    )

    def __repr__(self) -> str:
        return (
            f"<Referral referrer={self.referrer_id} referred={self.referred_id} "
            f"credited={self.reward_credited}>"
        )