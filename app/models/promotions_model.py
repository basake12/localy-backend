"""
app/models/promotions_model.py

Per Blueprint v2.0 Section 4.1.3 & 7.3:
- Funding bonus: "Fund ₦5,000, get ₦200 bonus" (admin-configured)
- Cashback events: "Earn 5% cashback on all food orders this weekend"
- Double-referral events: multiplied referral reward during event
- Streak rewards: "Book 3 services this month and earn ₦1,500"
- All promotions are time-bounded and admin-configured — no hardcoding

Models:
  Promotion         — the promotion definition (admin-created)
  PromotionRedemption — records each time a user receives a promotion credit
  StreakProgress    — tracks per-user progress toward streak completion
"""
from sqlalchemy import (
    Column, String, Boolean, Integer, Numeric,
    Enum as SQLEnum, ForeignKey, DateTime, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
from datetime import datetime, timezone

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class PromotionType(str, enum.Enum):
    FUNDING_BONUS   = "funding_bonus"    # Fund above X → receive bonus NGN
    CASHBACK_EVENT  = "cashback_event"   # % back after qualifying order
    DOUBLE_REFERRAL = "double_referral"  # Referral reward multiplied
    STREAK_REWARD   = "streak_reward"    # Complete N actions → earn reward


class PromotionStatus(str, enum.Enum):
    SCHEDULED = "scheduled"  # Start date in the future
    ACTIVE    = "active"     # Currently running
    PAUSED    = "paused"     # Admin-paused mid-run
    ENDED     = "ended"      # Past end date or manually ended


class StreakActionType(str, enum.Enum):
    ANY_ORDER        = "any_order"        # Any completed order
    FOOD_ORDER       = "food_order"       # Food module specifically
    SERVICE_BOOKING  = "service_booking"  # Services module
    HOTEL_BOOKING    = "hotel_booking"    # Hotels module
    HEALTH_BOOKING   = "health_booking"   # Health module
    TICKET_PURCHASE  = "ticket_purchase"  # Tickets module
    ANY_BOOKING      = "any_booking"      # Any booking type


# ─── Promotion ────────────────────────────────────────────────────────────────

class Promotion(BaseModel):
    """
    Admin-configured promotion.

    Each promotion has a type that drives its trigger logic:

    FUNDING_BONUS:
      - User funds wallet >= min_funding_amount
      - bonus_amount is credited immediately
      - max_per_user prevents stacking (usually 1)

    CASHBACK_EVENT:
      - User completes an order in applicable_modules
      - cashback_percentage of order value credited to wallet
      - max_cashback_amount caps the bonus per transaction
      - max_cashback_per_user caps total across all triggers

    DOUBLE_REFERRAL:
      - Referral service reads referral_multiplier during active window
      - Referral reward = base ₦1,000 × referral_multiplier
      - Separate from base referral system — just overrides the amount

    STREAK_REWARD:
      - User completes streak_target_count actions of streak_action_type
      - Actions must occur within the promotion window
      - streak_reward_amount credited on completion
      - Per-user, one-time (max_per_user = 1 enforced in service)
    """

    __tablename__ = "promotions"

    # Identity
    title       = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    banner_url  = Column(String(500), nullable=True)  # Promotional image

    # Type & status
    promotion_type = Column(SQLEnum(PromotionType), nullable=False, index=True)
    status         = Column(
        SQLEnum(PromotionStatus),
        default=PromotionStatus.SCHEDULED,
        nullable=False,
        index=True,
    )

    # Time window (mandatory — all promotions are time-bounded)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date   = Column(DateTime(timezone=True), nullable=False)

    # ── FUNDING_BONUS fields ─────────────────────────────────────────────────
    # Trigger: user tops up wallet with amount >= min_funding_amount
    min_funding_amount = Column(Numeric(15, 2), nullable=True)
    bonus_amount       = Column(Numeric(15, 2), nullable=True)  # Fixed NGN credit

    # ── CASHBACK_EVENT fields ────────────────────────────────────────────────
    # Trigger: user completes an order in applicable_modules
    cashback_percentage    = Column(Numeric(5, 2), nullable=True)   # e.g. 5.00 for 5%
    max_cashback_amount    = Column(Numeric(15, 2), nullable=True)   # Per-transaction cap
    max_cashback_per_user  = Column(Numeric(15, 2), nullable=True)   # Lifetime cap per user
    applicable_modules     = Column(JSONB, default=list)             # ["food", "hotels", ...]

    # ── DOUBLE_REFERRAL fields ───────────────────────────────────────────────
    # Trigger: referral reward is calculated during this promotion's window
    referral_multiplier = Column(Numeric(5, 2), nullable=True)  # e.g. 2.0 for double

    # ── STREAK_REWARD fields ─────────────────────────────────────────────────
    # Trigger: user completes streak_target_count of streak_action_type
    streak_action_type   = Column(SQLEnum(StreakActionType), nullable=True)
    streak_target_count  = Column(Integer, nullable=True)
    streak_reward_amount = Column(Numeric(15, 2), nullable=True)

    # ── Limits ───────────────────────────────────────────────────────────────
    max_total_redemptions = Column(Integer, nullable=True)    # Platform-wide cap
    max_per_user          = Column(Integer, default=1)        # Per-user redemptions
    current_redemptions   = Column(Integer, default=0)        # Running total

    # ── Visibility ───────────────────────────────────────────────────────────
    is_public = Column(Boolean, default=True)  # Show on promotions screen

    # Created by admin (nullable for system-generated)
    created_by_admin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    redemptions    = relationship(
        "PromotionRedemption",
        back_populates="promotion",
        cascade="all, delete-orphan",
    )
    streak_progresses = relationship(
        "StreakProgress",
        back_populates="promotion",
        cascade="all, delete-orphan",
    )

    def is_currently_active(self) -> bool:
        """Runtime check — status field is source of truth, this validates timing."""
        if self.status != PromotionStatus.ACTIVE:
            return False
        now = datetime.now(timezone.utc)
        return self.start_date <= now <= self.end_date

    def has_capacity(self) -> bool:
        """Check if promotion still has redemption slots."""
        if self.max_total_redemptions is None:
            return True
        return self.current_redemptions < self.max_total_redemptions

    def __repr__(self) -> str:
        return f"<Promotion {self.title!r} type={self.promotion_type} status={self.status}>"


# ─── Promotion Redemption ─────────────────────────────────────────────────────

class PromotionRedemption(BaseModel):
    """
    Records each time a user successfully receives a promotion credit.

    This is an immutable audit trail — one record per reward credited.
    For STREAK_REWARD, one record is created when the streak completes.
    For CASHBACK_EVENT and FUNDING_BONUS, one record per trigger.
    For DOUBLE_REFERRAL, one record per referral during the window.
    """

    __tablename__ = "promotion_redemptions"

    promotion_id = Column(
        UUID(as_uuid=True),
        ForeignKey("promotions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The wallet transaction created for this redemption
    wallet_transaction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wallet_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Amount credited to the user's wallet
    amount_credited = Column(Numeric(15, 2), nullable=False)

    # What triggered this redemption (order, booking, top-up, referral)
    trigger_type = Column(String(100), nullable=True)  # e.g. "food_order"
    trigger_id   = Column(UUID(as_uuid=True), nullable=True)  # e.g. order UUID

    # Snapshot of promotion metadata at time of redemption
    meta_data = Column(JSONB, nullable=True)

    # Relationships
    promotion          = relationship("Promotion", back_populates="redemptions")
    wallet_transaction = relationship("WalletTransaction", foreign_keys=[wallet_transaction_id])
    user               = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<PromotionRedemption promo={self.promotion_id} user={self.user_id} ₦{self.amount_credited}>"


# ─── Streak Progress ──────────────────────────────────────────────────────────

class StreakProgress(BaseModel):
    """
    Tracks per-user progress toward a STREAK_REWARD promotion completion.

    One record per (promotion, user) pair — unique constraint enforced.
    current_count increments on each qualifying action.
    When current_count >= promotion.streak_target_count, the reward is credited
    and completed is set to True (reward can only be earned once per user).
    """

    __tablename__ = "streak_progresses"

    promotion_id = Column(
        UUID(as_uuid=True),
        ForeignKey("promotions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    current_count  = Column(Integer, default=0, nullable=False)
    target_count   = Column(Integer, nullable=False)  # Snapshot of promotion target
    completed      = Column(Boolean, default=False, nullable=False)
    last_action_at = Column(DateTime(timezone=True), nullable=True)

    # Track qualifying actions (list of trigger UUIDs as strings)
    qualifying_action_ids = Column(JSONB, default=list)

    # Relationships
    promotion = relationship("Promotion", back_populates="streak_progresses")
    user      = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("promotion_id", "user_id", name="uq_streak_progress_promo_user"),
    )

    @property
    def remaining(self) -> int:
        return max(0, self.target_count - self.current_count)

    @property
    def progress_pct(self) -> float:
        if self.target_count == 0:
            return 100.0
        return min(100.0, (self.current_count / self.target_count) * 100)

    def __repr__(self) -> str:
        return (
            f"<StreakProgress promo={self.promotion_id} user={self.user_id} "
            f"{self.current_count}/{self.target_count} completed={self.completed}>"
        )