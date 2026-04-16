"""
app/models/wallet_model.py

FIXES vs previous version:
  1. [CRITICAL — FINANCIAL INTEGRITY] idempotency_key VARCHAR(255) UNIQUE
     NOT NULL added to WalletTransaction. Blueprint §5.6 HARD RULE:
     "All external payment operations use idempotency keys."
     Without this, Monnify webhook retry delivers double-credits.

  2. user_id renamed to owner_id + owner_type column added.
     Blueprint §14: owner_type IN ('customer','business','rider').
     Required for financial reporting, admin wallet view, and withdrawal
     logic differentiation between roles.

  3. is_suspended replaces is_active. Blueprint §14 / §5.5:
     "Virtual account suspended on ban, reactivated on unban."
     A suspended wallet is not an inactive wallet.

  4. Monnify virtual account fields added: virtual_acct_number,
     virtual_acct_name, virtual_acct_bank, monnify_acct_ref (UNIQUE).
     Blueprint §14 / §5.1 / §5.5. monnify_acct_ref UNIQUE prevents
     duplicate virtual account provisioning.

  5. external_reference VARCHAR(255) UNIQUE added.
     Blueprint §14: stores Monnify transaction reference or Paystack ref.

  6. related_order_id + related_booking_id added. Blueprint §14.

  7. wallet_number (LCY + 7 digits) kept — acceptable extension.

  8. CryptoTopUp model REMOVED — not specified in blueprint.
     Blueprint §5 specifies Monnify (bank transfer / virtual accounts)
     and Paystack (card) as the ONLY payment methods.

  9. All amounts NUMERIC(12,2) — never FLOAT. Blueprint §5.6 HARD RULE.

  10. platform_fee transaction_type added.
"""
from sqlalchemy import (
    Column,
    String,
    Numeric,
    Boolean,
    Enum,
    ForeignKey,
    DateTime,
    Text,
    CheckConstraint,
    Integer,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
import secrets
import string

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class TransactionTypeEnum(str, enum.Enum):
    """
    Blueprint §5 / §14 — all transaction types that flow through the wallet.
    platform_fee is explicit per §5.4 middleware spec.
    """
    CREDIT         = "credit"
    DEBIT          = "debit"
    REFUND         = "refund"
    CASHBACK       = "cashback"
    REFERRAL_BONUS = "referral_bonus"
    TOP_UP         = "top_up"
    PAYMENT        = "payment"
    PLATFORM_FEE   = "platform_fee"
    WITHDRAWAL     = "withdrawal"


class TransactionStatusEnum(str, enum.Enum):
    PENDING   = "pending"
    COMPLETED = "completed"
    FAILED    = "failed"
    REVERSED  = "reversed"


# Aliases kept for import compatibility
TransactionType   = TransactionTypeEnum
TransactionStatus = TransactionStatusEnum


# ─── Wallet number generator ──────────────────────────────────────────────────

def generate_wallet_number() -> str:
    """
    LCY + 7 random digits — e.g. LCY3947201.
    Prefix makes it instantly recognisable as a Localy wallet on the card UI.
    """
    digits = "".join(secrets.choice(string.digits) for _ in range(7))
    return f"LCY{digits}"


# ─── Wallet ───────────────────────────────────────────────────────────────────

class Wallet(BaseModel):
    """
    Blueprint §14 / §5.

    Every user (customer, business, rider) gets exactly one wallet created
    automatically at registration by the create_wallet Celery task (§3 / §16.2).

    - Customer wallets: spend-only, non-withdrawable (§5.1).
    - Business wallets: receive payments, withdraw to bank (§5.2).
    - Rider wallets: receive delivery earnings, withdraw to bank (§5.3).
    """
    __tablename__ = "wallets"

    # Blueprint §14: owner_id + owner_type (not user_id alone)
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,          # one wallet per user
        nullable=False,
        index=True,
    )
    # Blueprint §14: owner_type IN ('customer','business','rider')
    owner_type = Column(String(20), nullable=False)

    # Unique wallet card number — display only, not a payment instrument
    wallet_number = Column(
        String(10),
        unique=True,
        nullable=False,
        default=generate_wallet_number,
        index=True,
    )

    # Blueprint §5.6 HARD RULE: NUMERIC(12,2) — never FLOAT
    balance  = Column(Numeric(12, 2), default=0.00, nullable=False)
    currency = Column(String(3), default="NGN", nullable=False)

    # Blueprint §14 / §5.5: is_suspended — NOT is_active.
    # Wallet is suspended on user ban, reactivated on unban via Monnify API.
    is_suspended = Column(Boolean, default=False, nullable=False)

    # ── Monnify Virtual Account — Blueprint §5.1 / §5.5 ──────────────────────
    # Provisioned by assign_virtual_account Celery task at registration.
    # Account number is permanent — never changes, even on phone number update.
    virtual_acct_number = Column(String(20),  nullable=True)
    virtual_acct_name   = Column(String(255), nullable=True)
    virtual_acct_bank   = Column(String(100), nullable=True)
    # UNIQUE — prevents double-provisioning on Celery task retry.
    monnify_acct_ref    = Column(String(100), unique=True, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    user = relationship(
        "User", back_populates="wallet", foreign_keys=[owner_id]
    )
    transactions = relationship(
        "WalletTransaction",
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="desc(WalletTransaction.created_at)",
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="positive_balance"),
        CheckConstraint(
            "owner_type IN ('customer','business','rider')",
            name="valid_owner_type",
        ),
    )

    def __repr__(self) -> str:
        return f"<Wallet {self.wallet_number} owner={self.owner_id} ₦{self.balance}>"


# ─── Wallet Transaction ───────────────────────────────────────────────────────

class WalletTransaction(BaseModel):
    """
    Immutable financial ledger entry. Blueprint §14 / §5.6.

    HARD RULES (§5.6):
    - idempotency_key UNIQUE NOT NULL — prevents double-charges on retry.
    - external_reference UNIQUE — Monnify/Paystack reference for dispute trail.
    - All amounts NUMERIC(12,2) — never FLOAT.
    - All timestamps TIMESTAMPTZ (timezone=True via BaseModel).
    - Every financial operation is wrapped in a PostgreSQL transaction.
    """
    __tablename__ = "wallet_transactions"

    wallet_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    transaction_type = Column(
        Enum(TransactionTypeEnum), nullable=False, index=True
    )

    # Blueprint §5.6: NUMERIC(12,2) — NEVER FLOAT
    amount         = Column(Numeric(12, 2), nullable=False)
    balance_before = Column(Numeric(12, 2), nullable=False)
    balance_after  = Column(Numeric(12, 2), nullable=False)

    status = Column(
        Enum(TransactionStatusEnum),
        default=TransactionStatusEnum.PENDING,
        nullable=False,
        index=True,
    )

    description = Column(Text, nullable=True)

    # Blueprint §14: external_reference VARCHAR(255) UNIQUE
    # Stores Monnify transaction reference or Paystack reference.
    # Used for dispute resolution (sender bank name + tx ref per §5.5).
    external_reference = Column(String(255), unique=True, nullable=True, index=True)

    # Blueprint §14 / §5.6 HARD RULE: idempotency_key UNIQUE NOT NULL
    # Generated by the service layer before every financial operation.
    # Monnify webhook idempotency: check external_reference UNIQUE constraint
    # PLUS this key before crediting. Zero tolerance for double-credit on retry.
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)

    # Blueprint §14: context links for transaction history display
    related_order_id   = Column(UUID(as_uuid=True), nullable=True)
    related_booking_id = Column(UUID(as_uuid=True), nullable=True)

    meta_data    = Column(JSONB, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    wallet = relationship("Wallet", back_populates="transactions")

    def __repr__(self) -> str:
        return (
            f"<WalletTransaction {self.transaction_type} ₦{self.amount} "
            f"key={self.idempotency_key}>"
        )


# ─── Platform Revenue ─────────────────────────────────────────────────────────

class PlatformRevenue(BaseModel):
    """
    Immutable audit trail of all platform fees collected.

    Blueprint §5.4 fee structure:
    - Product / food orders:    ₦50 flat (₦50 from business + ₦50 from customer)
    - Service / hotel / health: ₦100 flat (₦100 each side)
    - Ticket purchases:         ₦50 flat (from customer only)

    Admin financial dashboard (§11.3) reads from this table.
    transaction_reference = same value as wallet_transactions.idempotency_key
    for reconciliation.
    """
    __tablename__ = "platform_revenue"

    customer_transaction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wallet_transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    business_transaction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wallet_transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Blueprint §5.6 HARD RULE: NUMERIC(12,2)
    gross_amount = Column(Numeric(12, 2), nullable=False)
    platform_fee = Column(Numeric(12, 2), nullable=False)
    net_amount   = Column(Numeric(12, 2), nullable=False)

    # e.g. 'hotel_booking' | 'food_order' | 'service_booking' |
    #       'product_purchase' | 'ticket_sale' | 'health_appointment'
    transaction_type      = Column(String(50), nullable=False, index=True)
    transaction_reference = Column(String(255), unique=True, nullable=False, index=True)
    related_entity_id     = Column(UUID(as_uuid=True), nullable=True)
    description           = Column(Text, nullable=True)
    meta_data             = Column(JSONB, nullable=True)

    customer_transaction = relationship(
        "WalletTransaction", foreign_keys=[customer_transaction_id]
    )
    business_transaction = relationship(
        "WalletTransaction", foreign_keys=[business_transaction_id]
    )

    __table_args__ = (
        CheckConstraint("gross_amount > 0",  name="positive_gross_amount"),
        CheckConstraint("platform_fee >= 0", name="non_negative_platform_fee"),
        CheckConstraint("net_amount >= 0",   name="non_negative_net_amount"),
        CheckConstraint(
            "gross_amount = platform_fee + net_amount",
            name="correct_fee_calculation",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PlatformRevenue {self.transaction_type} "
            f"fee=₦{self.platform_fee} ref={self.transaction_reference}>"
        )