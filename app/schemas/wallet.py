
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.models.wallet import TransactionType, TransactionStatus


class WalletOut(BaseModel):
    id: UUID
    user_id: UUID
    balance: Decimal
    total_credits: Decimal
    total_debits: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WalletTransactionBase(BaseModel):
    amount: Decimal = Field(..., gt=0)
    description: Optional[str] = None


class WalletTopUpRequest(WalletTransactionBase):
    payment_method: str = Field(..., description="card, bank_transfer, ussd")


class WalletWithdrawRequest(WalletTransactionBase):
    bank_account_number: str
    bank_code: str


class WalletTransferRequest(WalletTransactionBase):
    recipient_id: UUID


class WalletTransactionOut(BaseModel):
    id: UUID
    wallet_id: UUID
    transaction_type: TransactionType
    amount: Decimal
    balance_before: Decimal
    balance_after: Decimal
    description: Optional[str] = None
    reference: str
    status: TransactionStatus
    metadata: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class WalletTransactionListOut(BaseModel):
    transactions: List[WalletTransactionOut]
    total: int
    page: int
    page_size: int
