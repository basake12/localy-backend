"""
app/schemas/wallet_schema.py

FIXES vs previous version:
  1.  WalletOut: user_id → owner_id + owner_type. Blueprint §14.
  2.  WalletOut: is_active → is_suspended. Blueprint §14.
  3.  WalletOut: Added virtual_acct_number, virtual_acct_name, virtual_acct_bank,
      monnify_acct_ref. Blueprint §5.5 / §14.
  4.  WalletOut: wallet_number kept (acceptable extension).
  5.  WalletTransactionOut: reference_id → external_reference + idempotency_key.
      Blueprint §14 / §5.6 HARD RULE.
  6.  WalletTopUpRequest: min amount ₦500 → ₦1,000. Blueprint §5.1.
  7.  CryptoTopUpRequest / CryptoTopUpOut DELETED. Blueprint §5 specifies
      Monnify (bank) + Paystack (card) ONLY. No crypto funding channel.
  8.  FeeBreakdownOut added — shows fee breakdown at checkout. Blueprint §5.4:
      "All fees shown transparently in checkout summary before user confirms."
"""
from decimal import Decimal
from typing import List, Optional
from uuid import UUID
from datetime import datetime, date

from pydantic import BaseModel, Field, field_validator, ConfigDict

from app.models.wallet_model import TransactionType, TransactionStatus


# ─── Wallet ───────────────────────────────────────────────────────────────────

class WalletOut(BaseModel):
    """
    Public wallet representation.
    Blueprint §5.1 / §14.
    """
    model_config = ConfigDict(from_attributes=True)

    id:           UUID
    # Blueprint §14: owner_id + owner_type (not user_id alone)
    owner_id:     UUID
    owner_type:   str          # 'customer' | 'business' | 'rider'

    wallet_number: str         # e.g. LCY3947201 (display on wallet card)
    balance:       Decimal
    currency:      str

    # Blueprint §14: is_suspended (not is_active)
    # True = wallet suspended (e.g. on user ban)
    is_suspended: bool

    # Blueprint §14 / §5.5: Monnify virtual account fields
    # Populated by assign_virtual_account Celery task at registration.
    # Account number is permanent — never changes even on phone number update.
    virtual_acct_number: Optional[str] = None
    virtual_acct_name:   Optional[str] = None
    virtual_acct_bank:   Optional[str] = None
    monnify_acct_ref:    Optional[str] = None

    created_at:  datetime
    updated_at:  datetime


# ─── Top-Up (Paystack card) ───────────────────────────────────────────────────

class WalletTopUpRequest(BaseModel):
    """
    Initiate a card top-up via Paystack.
    Blueprint §5.1: minimum top-up ₦1,000.
    """
    amount: Decimal = Field(
        ...,
        ge=1000,    # Blueprint §5.1: minimum ₦1,000 (was ₦500 — FIXED)
        description="Amount in NGN. Minimum ₦1,000.",
    )
    payment_method: str = Field(
        ...,
        description="card | ussd",
    )


class TopUpInitResponse(BaseModel):
    """Returned when a card top-up is initialised."""
    authorization_url: str
    reference:         str
    amount:            Decimal
    gateway:           str


# ─── Monnify Virtual Account Info ─────────────────────────────────────────────

class VirtualAccountOut(BaseModel):
    """
    Returns the user's Monnify virtual account details.
    Blueprint §5.1: "Each user gets a unique, permanent Monnify virtual account."
    """
    account_number: str
    account_name:   str
    bank_name:      str
    monnify_ref:    Optional[str] = None


# ─── Withdraw ─────────────────────────────────────────────────────────────────

class WalletWithdrawRequest(BaseModel):
    """
    Withdraw to registered bank account.
    Blueprint §5.2: min ₦1,000. Daily limit ₦1,000,000. PIN required.
    """
    amount:              Decimal = Field(..., ge=1000, description="Amount in NGN. Minimum ₦1,000.")
    bank_account_number: str     = Field(..., min_length=10, max_length=10)
    bank_code:           str     = Field(..., min_length=3)
    recipient_name:      str     = Field(..., min_length=2, description="Account holder name")
    description:         Optional[str] = None
    pin:                 str     = Field(..., min_length=4, max_length=4, description="4-digit transaction PIN")

    @field_validator("bank_account_number")
    @classmethod
    def numeric_account(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("Bank account number must be numeric")
        return v

    @field_validator("pin")
    @classmethod
    def pin_must_be_4_digits(cls, v: str) -> str:
        if not (len(v) == 4 and v.isdigit()):
            raise ValueError("PIN must be exactly 4 digits")
        return v


# ─── Transfer ─────────────────────────────────────────────────────────────────

class WalletTransferRequest(BaseModel):
    recipient_wallet_number: str     = Field(..., description="Recipient's LCY wallet number")
    amount:                  Decimal = Field(..., gt=0)
    description:             Optional[str] = None
    pin:                     str     = Field(..., min_length=4, max_length=4)


# ─── Transactions ─────────────────────────────────────────────────────────────

class WalletTransactionOut(BaseModel):
    """
    Blueprint §14 WalletTransaction fields.
    """
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id:               UUID
    wallet_id:        UUID
    transaction_type: TransactionType
    amount:           Decimal
    balance_before:   Decimal
    balance_after:    Decimal
    description:      Optional[str]  = None

    # Blueprint §14: external_reference (Monnify/Paystack ref) + idempotency_key
    external_reference: Optional[str] = None
    idempotency_key:    str

    status:           TransactionStatus
    meta_data:        Optional[dict] = None
    created_at:       datetime
    completed_at:     Optional[datetime] = None

    # Context links
    related_order_id:   Optional[UUID] = None
    related_booking_id: Optional[UUID] = None


class WalletTransactionListOut(BaseModel):
    transactions: List[WalletTransactionOut]
    total:        int
    page:         int
    page_size:    int


# ─── Fee Breakdown ────────────────────────────────────────────────────────────

class FeeBreakdownOut(BaseModel):
    """
    Blueprint §5.4: "All fees shown transparently in checkout summary
    before user confirms."

    Fee structure:
      Product / food orders:          ₦50 from customer + ₦50 from business
      Service / hotel / health:       ₦100 from customer + ₦100 from business
      Ticket purchases:               ₦50 from customer only

    Shown at checkout before payment confirmation.
    """
    product_price:    Decimal   # base price of the product/service
    customer_fee:     Decimal   # fee charged to customer
    business_fee:     Decimal   # fee deducted from business
    customer_total:   Decimal   # what customer pays (product_price + customer_fee)
    business_net:     Decimal   # what business receives (product_price - business_fee)
    platform_total:   Decimal   # platform revenue (customer_fee + business_fee)
    currency:         str = "NGN"
    transaction_type: str


# ─── Platform Revenue ─────────────────────────────────────────────────────────

class PlatformRevenueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                      UUID
    customer_id:             Optional[UUID] = None
    business_id:             Optional[UUID] = None
    customer_transaction_id: Optional[UUID] = None
    business_transaction_id: Optional[UUID] = None

    gross_amount:  Decimal   # total customer paid (product_price + customer_fee)
    platform_fee:  Decimal   # total platform revenue (customer_fee + business_fee)
    net_amount:    Decimal   # what business received (product_price - business_fee)

    transaction_type:      str
    transaction_reference: str
    related_entity_id:     Optional[UUID] = None
    description:           Optional[str]  = None
    meta_data:             Optional[dict] = None
    created_at:            datetime


class PlatformRevenueStats(BaseModel):
    total_revenue:           Decimal
    total_transactions:      int
    average_fee:             Decimal
    hotel_bookings_revenue:  Decimal
    hotel_bookings_count:    int
    food_orders_revenue:     Decimal
    food_orders_count:       int
    service_bookings_revenue: Decimal
    service_bookings_count:   int
    product_purchases_revenue: Decimal
    product_purchases_count:   int
    ticket_sales_revenue:    Decimal
    ticket_sales_count:      int
    health_appointments_revenue: Decimal
    health_appointments_count:   int
    start_date:              Optional[date] = None
    end_date:                Optional[date] = None


class PlatformRevenueListOut(BaseModel):
    revenues:  List[PlatformRevenueOut]
    total:     int
    page:      int
    page_size: int
    stats:     Optional[PlatformRevenueStats] = None


# ─── Payment Result (internal) ────────────────────────────────────────────────

class PaymentResult(BaseModel):
    """
    Returned by TransactionService.process_payment().
    All three atomic records created in one DB transaction.
    """
    customer_transaction:  WalletTransactionOut
    business_transaction:  WalletTransactionOut
    platform_revenue:      PlatformRevenueOut

    product_price:         Decimal   # base price (before fees)
    customer_fee:          Decimal   # fee charged to customer
    business_fee:          Decimal   # fee deducted from business
    platform_total_fee:    Decimal   # customer_fee + business_fee
    customer_total:        Decimal   # what customer paid
    business_net:          Decimal   # what business received
    transaction_reference: str