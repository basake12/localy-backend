"""
app/models/wallet_model.py

CHANGES:
  1. wallet_number added — unique 10-digit number generated on creation,
     displayed on the wallet card like a virtual account number.
     Format: LCY + 7 random digits (e.g. LCY1234567). Unique index.

  2. account_number + bank_name added — populated when a virtual bank
     account is provisioned via Paystack/Monnify. Nullable until then.

  3. CryptoTopUp model added — tracks crypto funding requests (USDT/BTC)
     with wallet address, expected amount, and confirmation status.
     This is NOT a bank feature — it is a top-up channel only.

  4. PlatformRevenue model added — tracks all platform fees collected
     from transactions. Immutable audit trail for financial reporting.
"""
from sqlalchemy import (
    Column, String, Numeric, Boolean, Enum,
    ForeignKey, DateTime, Text, CheckConstraint, Integer,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
import secrets
import string

from app.models.base_model import BaseModel


# ─── Enums ───────────────────────────────────────────────────────────────────

class TransactionTypeEnum(str, enum.Enum):
    CREDIT         = "credit"
    DEBIT          = "debit"
    REFUND         = "refund"
    CASHBACK       = "cashback"
    REFERRAL_BONUS = "referral_bonus"
    TOP_UP         = "top_up"
    PAYMENT        = "payment"
    CRYPTO_TOP_UP  = "crypto_top_up"   # new: crypto funding channel


class TransactionStatusEnum(str, enum.Enum):
    PENDING   = "pending"
    COMPLETED = "completed"
    FAILED    = "failed"
    REVERSED  = "reversed"


class CryptoTopUpStatusEnum(str, enum.Enum):
    AWAITING_PAYMENT = "awaiting_payment"
    CONFIRMING       = "confirming"        # payment seen, waiting confirmations
    COMPLETED        = "completed"
    EXPIRED          = "expired"
    FAILED           = "failed"


# Aliases
TransactionType   = TransactionTypeEnum
TransactionStatus = TransactionStatusEnum


# ─── Wallet number generator ─────────────────────────────────────────────────

def generate_wallet_number() -> str:
    """
    Generate a unique 10-character wallet number.
    Format: LCY + 7 random digits — e.g. LCY3947201.
    Prefix 'LCY' makes it instantly recognisable as a Localy wallet.
    """
    digits = ''.join(secrets.choice(string.digits) for _ in range(7))
    return f"LCY{digits}"


# ─── Wallet ───────────────────────────────────────────────────────────────────

class Wallet(BaseModel):
    """User wallet for NGN transactions."""

    __tablename__ = "wallets"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Unique wallet identifier — shown on the wallet card
    wallet_number = Column(
        String(10),
        unique=True,
        nullable=False,
        default=generate_wallet_number,
        index=True,
    )

    balance  = Column(Numeric(15, 2), default=0.00, nullable=False)
    currency = Column(String(3), default="NGN", nullable=False)
    is_active = Column(Boolean, default=True)

    # Virtual bank account — provisioned by Paystack/Monnify (nullable until created)
    account_number = Column(String(20),  nullable=True)
    bank_name      = Column(String(100), nullable=True)

    # Relationships
    user         = relationship("User", back_populates="wallet")
    transactions = relationship(
        "WalletTransaction",
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="desc(WalletTransaction.created_at)",
    )
    crypto_top_ups = relationship(
        "CryptoTopUp",
        back_populates="wallet",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="positive_balance"),
    )

    def __repr__(self) -> str:
        return f"<Wallet {self.wallet_number} ₦{self.balance}>"


# ─── Wallet Transaction ───────────────────────────────────────────────────────

class WalletTransaction(BaseModel):
    """Immutable ledger entry for every wallet movement."""

    __tablename__ = "wallet_transactions"

    wallet_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    transaction_type = Column(Enum(TransactionTypeEnum), nullable=False, index=True)
    amount           = Column(Numeric(15, 2), nullable=False)
    balance_before   = Column(Numeric(15, 2), nullable=False)
    balance_after    = Column(Numeric(15, 2), nullable=False)

    status = Column(
        Enum(TransactionStatusEnum),
        default=TransactionStatusEnum.PENDING,
        nullable=False,
        index=True,
    )

    description  = Column(Text,        nullable=True)
    reference_id = Column(String(100), unique=True, nullable=True, index=True)
    meta_data    = Column(JSONB,       nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    wallet = relationship("Wallet", back_populates="transactions")

    def __repr__(self) -> str:
        return f"<WalletTransaction {self.transaction_type} ₦{self.amount}>"


# ─── Crypto Top-Up ────────────────────────────────────────────────────────────

class CryptoTopUp(BaseModel):
    """
    Tracks a crypto funding request.

    Flow:
      1. User requests a top-up → record created with a deposit address
         and expected NGN equivalent.
      2. Crypto payment provider (NOWPayments / Binance Pay) sends a webhook
         when payment is detected/confirmed.
      3. Webhook handler credits the wallet and marks status = COMPLETED.

    This is NOT a bank feature — crypto is only a top-up INPUT channel.
    The wallet always holds NGN. The exchange rate is locked at creation time.
    """
    __tablename__ = "crypto_top_ups"

    wallet_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Crypto details
    crypto_currency   = Column(String(10), nullable=False)   # USDT, BTC, ETH
    crypto_network    = Column(String(20), nullable=False)   # TRC20, ERC20, BEP20
    deposit_address   = Column(String(200), nullable=False)  # user sends to this
    expected_crypto   = Column(Numeric(20, 8), nullable=False)  # exact crypto amount
    expected_ngn      = Column(Numeric(15, 2), nullable=False)  # NGN to credit on receipt
    exchange_rate     = Column(Numeric(20, 4), nullable=False)  # rate locked at creation

    # Payment provider reference
    provider_order_id = Column(String(100), nullable=True, index=True)
    provider          = Column(String(50), default="nowpayments")

    status = Column(
        Enum(CryptoTopUpStatusEnum),
        default=CryptoTopUpStatusEnum.AWAITING_PAYMENT,
        nullable=False,
        index=True,
    )

    # Actual received amounts (filled by webhook)
    received_crypto   = Column(Numeric(20, 8), nullable=True)
    confirmations     = Column(Integer, default=0)
    expires_at        = Column(DateTime(timezone=True), nullable=False)
    completed_at      = Column(DateTime(timezone=True), nullable=True)

    wallet = relationship("Wallet", back_populates="crypto_top_ups")

    def __repr__(self) -> str:
        return f"<CryptoTopUp {self.crypto_currency} {self.expected_ngn} NGN>"


# ─── Platform Revenue ─────────────────────────────────────────────────────────

class PlatformRevenue(BaseModel):
    """
    Immutable audit trail of all platform fees collected.
    
    Per Blueprint Section 4.4:
    - ₦50 flat fee on standard payments (products, food orders, tickets)
    - ₦100 flat fee on bookings (hotels, services, health appointments)
    - ₦50 flat fee on ticket purchases (per ticket)
    
    Each revenue entry is linked to:
    - The customer transaction (debit from customer wallet)
    - The business transaction (credit to business wallet)
    - The original reference (e.g., booking_id, order_id)
    
    This table enables:
    - Real-time platform revenue tracking
    - Financial reporting and analytics
    - Reconciliation of customer payments vs business credits
    - Audit trail for fee adjustments
    """
    __tablename__ = "platform_revenue"

    # Transaction references
    customer_transaction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wallet_transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Customer payment transaction",
    )
    
    business_transaction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wallet_transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Business credit transaction",
    )

    # User IDs for quick filtering
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Financial details
    gross_amount = Column(
        Numeric(15, 2),
        nullable=False,
        comment="Total amount paid by customer",
    )
    
    platform_fee = Column(
        Numeric(15, 2),
        nullable=False,
        comment="Platform fee deducted (₦50 or ₦100)",
    )
    
    net_amount = Column(
        Numeric(15, 2),
        nullable=False,
        comment="Amount credited to business (gross - fee)",
    )

    # Transaction metadata
    transaction_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="hotel_booking | food_order | service_booking | product_purchase | ticket_sale | health_appointment",
    )
    
    transaction_reference = Column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
        comment="Idempotency key - same as wallet transaction reference",
    )
    
    related_entity_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        comment="ID of the booking/order/purchase that generated this fee",
    )
    
    description = Column(Text, nullable=True)
    
    meta_data = Column(
        JSONB,
        nullable=True,
        comment="Additional context: module, category, location, etc.",
    )

    # Relationships (optional - for eager loading)
    customer_transaction = relationship(
        "WalletTransaction",
        foreign_keys=[customer_transaction_id],
        lazy="joined",
    )
    
    business_transaction = relationship(
        "WalletTransaction",
        foreign_keys=[business_transaction_id],
        lazy="joined",
    )

    __table_args__ = (
        CheckConstraint("gross_amount > 0", name="positive_gross_amount"),
        CheckConstraint("platform_fee >= 0", name="non_negative_platform_fee"),
        CheckConstraint("net_amount >= 0", name="non_negative_net_amount"),
        CheckConstraint(
            "gross_amount = platform_fee + net_amount",
            name="correct_fee_calculation",
        ),
    )

    def __repr__(self) -> str:
        return f"<PlatformRevenue {self.transaction_type} fee=₦{self.platform_fee} ref={self.transaction_reference}>"