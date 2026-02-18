from sqlalchemy import (
    Column, String, Numeric, Boolean, Enum,
    ForeignKey, DateTime, Text, CheckConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.models.base import BaseModel


# ============================================
# ENUMS
# ============================================

class TransactionTypeEnum(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    REFUND = "refund"
    CASHBACK = "cashback"
    REFERRAL_BONUS = "referral_bonus"
    TOP_UP = "top_up"
    PAYMENT = "payment"


class TransactionStatusEnum(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


# Aliases — import either name, both work
TransactionType = TransactionTypeEnum
TransactionStatus = TransactionStatusEnum


# ============================================
# WALLET MODEL
# ============================================

class Wallet(BaseModel):
    """User wallet for NGN transactions"""

    __tablename__ = "wallets"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    balance = Column(Numeric(15, 2), default=0.00, nullable=False)
    currency = Column(String(3), default="NGN", nullable=False)
    is_active = Column(Boolean, default=True)

    # Relationships
    user = relationship("User", back_populates="wallet")
    transactions = relationship(
        "WalletTransaction",
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="desc(WalletTransaction.created_at)"
    )

    __table_args__ = (
        CheckConstraint('balance >= 0', name='positive_balance'),
    )

    def __repr__(self):
        return f"<Wallet {self.user_id} Balance: {self.balance} {self.currency}>"


# ============================================
# WALLET TRANSACTION MODEL
# ============================================

class WalletTransaction(BaseModel):
    """All wallet transactions"""

    __tablename__ = "wallet_transactions"

    wallet_id = Column(UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True)

    transaction_type = Column(Enum(TransactionTypeEnum), nullable=False, index=True)
    amount = Column(Numeric(15, 2), nullable=False)
    balance_before = Column(Numeric(15, 2), nullable=False)
    balance_after = Column(Numeric(15, 2), nullable=False)

    status = Column(
        Enum(TransactionStatusEnum),
        default=TransactionStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    description = Column(Text, nullable=True)
    reference_id = Column(String(100), unique=True, nullable=True, index=True)
    meta_data = Column(Text, nullable=True)  # JSON stored as text

    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    wallet = relationship("Wallet", back_populates="transactions")

    def __repr__(self):
        return f"<WalletTransaction {self.transaction_type} {self.amount}>"