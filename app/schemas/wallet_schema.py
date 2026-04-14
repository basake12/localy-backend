"""
app/schemas/wallet_schema.py

CHANGES:
  1. WalletOut now includes wallet_number, account_number, bank_name
     — Flutter WalletCard can display the wallet number and virtual
     account details without a separate API call.

  2. CryptoTopUpRequest / CryptoTopUpOut schemas added for the
     crypto funding channel (USDT/BTC/ETH).

  3. WalletWithdrawRequest adds recipient_name for Paystack transfer
     recipient resolution.

  4. Amount in Naira (not kobo) — all amounts are in NGN throughout.
     The payment service layer handles kobo conversion internally.

  5. PlatformRevenueOut, PlatformRevenueStats, and PlatformRevenueListOut
     added for platform fee tracking and admin analytics.
"""
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Literal
from uuid import UUID
from datetime import datetime, date
from decimal import Decimal

from app.models.wallet_model import (
    TransactionType,
    TransactionStatus,
    CryptoTopUpStatusEnum,
)


# ─── Wallet ───────────────────────────────────────────────────────────────────

class WalletOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:             UUID
    user_id:        UUID
    wallet_number:  str          # e.g. LCY3947201
    balance:        Decimal
    currency:       str
    is_active:      bool
    account_number: Optional[str] = None   # virtual bank account (nullable)
    bank_name:      Optional[str] = None
    created_at:     datetime
    updated_at:     datetime


# ─── Top-Up ───────────────────────────────────────────────────────────────────

class WalletTopUpRequest(BaseModel):
    amount: Decimal = Field(
        ...,
        ge=500,
        description="Amount in NGN — minimum ₦500 (enforced here so the user is "
                    "blocked before a Paystack charge is created)",
    )
    payment_method: str = Field(
        ...,
        description="card | bank_transfer | ussd",
    )
    gateway: str = Field(
        default="paystack",
        description="paystack | monnify",
    )


class TopUpInitResponse(BaseModel):
    """Returned when a card/bank top-up is initialised."""
    authorization_url: str
    reference:         str
    amount:            Decimal
    gateway:           str


# ─── Crypto Top-Up ────────────────────────────────────────────────────────────

CryptoCurrency = Literal["USDT", "BTC", "ETH", "BNB", "USDC"]
CryptoNetwork  = Literal["TRC20", "ERC20", "BEP20", "Bitcoin", "Ethereum"]

class CryptoTopUpRequest(BaseModel):
    """
    Request a crypto deposit address.
    The wallet will be credited in NGN once the crypto payment is confirmed.
    """
    ngn_amount:       Decimal = Field(..., gt=0, description="NGN amount to receive")
    crypto_currency:  CryptoCurrency  = "USDT"
    crypto_network:   CryptoNetwork   = "TRC20"


class CryptoTopUpOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               UUID
    wallet_id:        UUID
    crypto_currency:  str
    crypto_network:   str
    deposit_address:  str           # user sends crypto here
    expected_crypto:  Decimal       # exact crypto amount to send
    expected_ngn:     Decimal       # NGN that will be credited
    exchange_rate:    Decimal
    provider_order_id: Optional[str]
    status:           CryptoTopUpStatusEnum
    expires_at:       datetime
    created_at:       datetime


# ─── Withdraw ─────────────────────────────────────────────────────────────────

class WalletWithdrawRequest(BaseModel):
    amount:              Decimal = Field(..., gt=0, description="Amount in NGN")
    bank_account_number: str    = Field(..., min_length=10, max_length=10)
    bank_code:           str    = Field(..., min_length=3)
    recipient_name:      str    = Field(..., min_length=2, description="Account holder name")
    description:         Optional[str] = None

    @field_validator("bank_account_number")
    @classmethod
    def numeric_account(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("Bank account number must be numeric")
        return v


# ─── Transfer ─────────────────────────────────────────────────────────────────

class WalletTransferRequest(BaseModel):
    recipient_wallet_number: str     = Field(..., description="Recipient's LCY wallet number")
    amount:                  Decimal = Field(..., gt=0)
    description:             Optional[str] = None
    # recipient_id kept for internal use — resolved from wallet_number
    recipient_id: Optional[UUID] = None


# ─── Transactions ─────────────────────────────────────────────────────────────

class WalletTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id:               UUID
    wallet_id:        UUID
    transaction_type: TransactionType
    amount:           Decimal
    balance_before:   Decimal
    balance_after:    Decimal
    description:      Optional[str]  = None
    reference_id:     Optional[str]  = None
    status:           TransactionStatus
    meta_data:        Optional[dict] = None
    created_at:       datetime
    completed_at:     Optional[datetime] = None


class WalletTransactionListOut(BaseModel):
    transactions: List[WalletTransactionOut]
    total:        int
    page:         int
    page_size:    int


# ─── Platform Revenue ─────────────────────────────────────────────────────────

class PlatformRevenueOut(BaseModel):
    """
    Single platform revenue record.
    Used in admin analytics and financial reporting.
    """
    model_config = ConfigDict(from_attributes=True)

    id:                      UUID
    customer_id:             Optional[UUID] = None
    business_id:             Optional[UUID] = None
    customer_transaction_id: Optional[UUID] = None
    business_transaction_id: Optional[UUID] = None
    
    gross_amount:            Decimal
    platform_fee:            Decimal
    net_amount:              Decimal
    
    transaction_type:        str
    transaction_reference:   str
    related_entity_id:       Optional[UUID] = None
    description:             Optional[str]  = None
    meta_data:               Optional[dict] = None
    
    created_at:              datetime


class PlatformRevenueStats(BaseModel):
    """
    Aggregated platform revenue statistics.
    Used in admin dashboard for financial overview.
    """
    total_revenue:           Decimal  # Sum of all platform_fee
    total_transactions:      int      # Count of revenue records
    average_fee:             Decimal  # Average platform fee
    
    # Breakdown by transaction type
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
    
    # Date range for stats
    start_date:              Optional[date] = None
    end_date:                Optional[date] = None


class PlatformRevenueListOut(BaseModel):
    """Paginated list of platform revenue records."""
    revenues:  List[PlatformRevenueOut]
    total:     int
    page:      int
    page_size: int
    stats:     Optional[PlatformRevenueStats] = None  # Optional summary stats


# ─── Payment Processing (Internal) ────────────────────────────────────────────

class PaymentResult(BaseModel):
    """
    Internal schema returned by TransactionService.process_payment().
    Contains all three transaction records created in a single atomic operation.
    """
    customer_transaction: WalletTransactionOut
    business_transaction: WalletTransactionOut
    platform_revenue:     PlatformRevenueOut
    
    # Convenience fields
    gross_amount:         Decimal
    platform_fee:         Decimal
    net_amount:           Decimal
    transaction_reference: str