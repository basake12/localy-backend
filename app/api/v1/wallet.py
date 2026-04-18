"""
app/api/v1/wallet.py

FIXES:
  [AUDIT BUG-8 FIX] Monnify and Paystack webhook endpoints REMOVED from this file.

  Root cause:
    Webhook routes were defined here but wallet router is mounted at /wallet.
    Actual URLs were /api/v1/wallet/webhooks/... instead of /api/v1/webhooks/...
    Monnify was delivering to the wrong URL → 404 → no wallet ever funded.

  Fix:
    Webhook endpoints moved to app/api/v1/webhooks.py, mounted at /webhooks.
    router.py updated to include webhooks.router at prefix="/webhooks".

  This file now contains ONLY wallet CRUD endpoints:
    - GET  /wallet             → balance + virtual account info
    - GET  /wallet/balance     → alias for home screen card
    - GET  /wallet/virtual-account → Monnify account details
    - POST /wallet/topup       → initialise Paystack card top-up
    - POST /wallet/topup/verify → verify Paystack callback
    - POST /wallet/withdraw    → business/rider withdrawal
    - GET  /wallet/withdrawal-limits
    - POST /wallet/transfer    → wallet-to-wallet transfer
    - GET  /wallet/fee-breakdown → checkout fee preview
    - GET  /wallet/transactions  → transaction history
    - POST /wallet/business/credit → internal business wallet credit
    - POST /wallet/rider/credit    → internal rider wallet credit

All other wallet operations (webhook processing, fee middleware) are
in wallet_service.py and webhooks.py respectively.
"""
import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_async_db
from app.dependencies import (
    get_async_current_active_user,
    get_async_fully_verified_user,
    require_pin_set,
)
from app.models.user_model import User
from app.models.wallet_model import TransactionTypeEnum
from app.schemas.wallet_schema import (
    FeeBreakdownOut,
    TopUpInitResponse,
    VirtualAccountOut,
    WalletOut,
    WalletTopUpRequest,
    WalletTransactionListOut,
    WalletTransactionOut,
    WalletTransferRequest,
    WalletWithdrawRequest,
)
from app.schemas.common_schema import SuccessResponse
from app.services.wallet_service import wallet_service
from app.services.payment_service import payment_service
from app.services.transaction_service import transaction_service

router = APIRouter(tags=["Wallet"])
logger = logging.getLogger(__name__)


# ─── Balance ──────────────────────────────────────────────────────────────────

@router.get("", response_model=SuccessResponse[WalletOut])
async def get_my_wallet(
    db:   AsyncSession = Depends(get_async_db),
    user: User         = Depends(get_async_current_active_user),
):
    """
    Get wallet balance + virtual account info.
    Blueprint §5.1: balance + Monnify virtual account (permanent, never changes).
    """
    wallet = await wallet_service.get_user_wallet(db, user_id=user.id)
    return {"success": True, "data": wallet}


@router.get("/balance", response_model=SuccessResponse[WalletOut])
async def get_wallet_balance(
    db:   AsyncSession = Depends(get_async_db),
    user: User         = Depends(get_async_current_active_user),
):
    """Alias — Flutter home screen wallet card."""
    wallet = await wallet_service.get_user_wallet(db, user_id=user.id)
    return {"success": True, "data": wallet}


@router.get("/virtual-account", response_model=SuccessResponse[VirtualAccountOut])
async def get_virtual_account(
    db:   AsyncSession = Depends(get_async_db),
    user: User         = Depends(get_async_current_active_user),
):
    """
    Return this user's Monnify virtual account details.
    Blueprint §5.1: "Account number never changes, even on phone number update."
    Display this to the user as the funding destination for bank transfers.
    """
    wallet = await wallet_service.get_user_wallet(db, user_id=user.id)
    if not wallet.virtual_acct_number:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Virtual account not yet provisioned. Please try again in a moment.",
        )
    return {
        "success": True,
        "data": VirtualAccountOut(
            account_number=wallet.virtual_acct_number,
            account_name=wallet.virtual_acct_name or user.full_name,
            bank_name=wallet.virtual_acct_bank or "Monnify",
            monnify_ref=wallet.monnify_acct_ref,
        ),
    }


# ─── Card Top-Up (Paystack) ───────────────────────────────────────────────────

@router.post(
    "/topup",
    response_model=SuccessResponse[TopUpInitResponse],
    status_code=status.HTTP_201_CREATED,
)
async def topup_wallet(
    payload: WalletTopUpRequest,
    db:      AsyncSession = Depends(get_async_db),
    user:    User         = Depends(get_async_current_active_user),
):
    """
    Initialise a Paystack card top-up.
    Blueprint §5.1: minimum ₦1,000. Daily limit ₦2,000,000.

    For bank transfers: users send money to their permanent Monnify virtual account
    (GET /wallet/virtual-account). The Monnify webhook at /api/v1/webhooks/monnify/funding
    auto-credits the wallet — no action needed here.
    """
    # Blueprint §14: email optional; phone_number always present
    paystack_email = user.email or f"{user.phone_number}@localy.ng"

    payment = await payment_service.initialize_transaction(
        email=paystack_email,
        amount=payload.amount,
        metadata={
            "user_id":        str(user.id),
            "type":           "wallet_topup",
            "payment_method": payload.payment_method,
        },
    )
    payment_data = payment.get("data", {})
    if not payment_data.get("authorization_url"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Paystack failed to initialise transaction. Please try again.",
        )
    return {
        "success": True,
        "data": TopUpInitResponse(
            authorization_url=payment_data["authorization_url"],
            reference=payment_data["reference"],
            amount=payload.amount,
            gateway="paystack",
        ),
    }


@router.post("/topup/verify", response_model=SuccessResponse[WalletTransactionOut])
async def verify_topup(
    reference: str        = Query(...),
    db:        AsyncSession = Depends(get_async_db),
    user:      User         = Depends(get_async_current_active_user),
):
    """
    Verify Paystack callback and credit wallet.
    Blueprint §5.1: "Paystack amounts are in kobo — DIVIDE by 100 before crediting."
    Idempotent — safe to call multiple times for the same reference.
    The webhook at /webhooks/paystack/payment handles automatic verification;
    this endpoint is for manual fallback from Flutter callback URL.
    """
    result   = await payment_service.verify_transaction(reference)
    pay_data = result.get("data", {})

    if not result.get("status") or pay_data.get("status") != "success":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Payment not successful or reference not found.",
        )

    # payment_service.verify_transaction() returns amount in kobo.
    # Blueprint §5.1: "DIVIDE by 100 before crediting wallet"
    amount_kobo = Decimal(str(pay_data.get("amount", 0)))
    amount_ngn  = amount_kobo / Decimal("100")

    txn = await wallet_service.credit_wallet(
        db,
        user_id=user.id,
        amount_ngn=amount_ngn,
        description="Wallet top-up via Paystack card payment",
        reference=reference,
        metadata={"payment_data": pay_data},
    )
    return {"success": True, "data": txn}


# ─── Withdraw ─────────────────────────────────────────────────────────────────

@router.post("/withdraw", response_model=SuccessResponse[WalletTransactionOut])
async def withdraw_from_wallet(
    payload: WalletWithdrawRequest,
    db:      AsyncSession = Depends(get_async_db),
    user:    User         = Depends(get_async_fully_verified_user),
):
    """
    Withdraw to bank account.

    Blueprint §5.2:
      - CUSTOMERS CANNOT WITHDRAW — wallet is spend-only.
      - Business / Rider: min ₦1,000, max ₦1,000,000/day.
      - Processing: same/next business day.

    Blueprint §3.3 HARD RULE:
      PIN is required for ALL wallet withdrawals — no exceptions.
      Verified in request body (payload.pin).
    """
    # Blueprint §3.3: verify PIN before withdrawal — no exceptions
    from app.core.security import verify_pin
    if not user.pin_hash or not verify_pin(payload.pin, user.pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "incorrect_pin", "message": "Incorrect PIN."},
        )

    # Blueprint §5.1: customer wallets are spend-only, non-withdrawable
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    if role_val == "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer wallets are spend-only. Withdrawals are not permitted.",
        )
    if role_val not in ("business", "rider"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only business and rider accounts can withdraw funds.",
        )

    txn = await wallet_service.process_payout(
        db,
        user_id=user.id,
        amount_ngn=payload.amount,
        bank_account=payload.bank_account_number,
        bank_code=payload.bank_code,
        recipient_name=payload.recipient_name,
        description=payload.description,
    )
    return {"success": True, "data": txn}


@router.get("/withdrawal-limits", response_model=SuccessResponse[dict])
async def get_withdrawal_limits(
    user: User = Depends(get_async_current_active_user),
):
    """
    Return withdrawal limits. Blueprint §5.2.
    Customer wallets cannot withdraw at all — spend-only.
    """
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    if role_val == "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customers cannot withdraw funds. Customer wallets are spend-only.",
        )
    return {
        "success": True,
        "data": {
            "minimum_withdrawal": 1000.0,    # Blueprint §5.2: min ₦1,000
            "maximum_daily":      1000000.0, # Blueprint §5.2: max ₦1,000,000/day
            "currency":           "NGN",
            "processing_time":    "Same business day or next business day",
            "pin_required":       True,       # Blueprint §3.3 HARD RULE
        },
    }


# ─── Transfer ─────────────────────────────────────────────────────────────────

@router.post("/transfer", response_model=SuccessResponse[dict])
async def transfer_funds(
    payload: WalletTransferRequest,
    db:      AsyncSession = Depends(get_async_db),
    user:    User         = Depends(get_async_fully_verified_user),
):
    """
    Transfer to another Localy user by wallet number (LCY...).
    Blueprint §3.3: PIN required for all wallet transactions — verified here.
    """
    from app.core.security import verify_pin
    if not user.pin_hash or not verify_pin(payload.pin, user.pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "incorrect_pin", "message": "Incorrect PIN."},
        )

    debit_txn, credit_txn = await wallet_service.transfer(
        db,
        from_user_id=user.id,
        recipient_wallet_number=payload.recipient_wallet_number,
        amount_ngn=payload.amount,
        description=payload.description or "Wallet transfer",
    )
    return {
        "success": True,
        "data": {
            "debit_transaction_id":  str(debit_txn.id),
            "credit_transaction_id": str(credit_txn.id),
            "amount":                float(payload.amount),
            "recipient_wallet":      payload.recipient_wallet_number,
        },
    }


# ─── Fee Preview ──────────────────────────────────────────────────────────────

@router.get("/fee-breakdown", response_model=SuccessResponse[FeeBreakdownOut])
async def get_fee_breakdown(
    product_price:    float = Query(..., gt=0),
    transaction_type: str   = Query(
        ...,
        description="product_purchase|food_order|hotel_booking|service_booking|health_appointment|ticket_sale",
    ),
    user: User = Depends(get_async_current_active_user),
):
    """
    Return fee breakdown for checkout display.
    Blueprint §5.4: "All fees shown transparently in checkout summary before user confirms."
    """
    breakdown = transaction_service.get_fee_breakdown(
        Decimal(str(product_price)), transaction_type
    )
    return {"success": True, "data": FeeBreakdownOut(**breakdown)}


# ─── Transactions ─────────────────────────────────────────────────────────────

@router.get("/transactions", response_model=SuccessResponse[WalletTransactionListOut])
async def get_wallet_transactions(
    transaction_type: TransactionTypeEnum = Query(None),
    skip:  int        = Query(0, ge=0),
    limit: int        = Query(20, ge=1, le=100),
    db:    AsyncSession = Depends(get_async_db),
    user:  User         = Depends(get_async_current_active_user),
):
    """
    Full wallet transaction history with pagination.
    Blueprint §5.2: "Full history — date, description, amount, balance, downloadable."
    """
    transactions, total = await wallet_service.get_transaction_history(
        db,
        user_id=user.id,
        transaction_type=transaction_type,
        skip=skip,
        limit=limit,
    )
    return {
        "success": True,
        "data": WalletTransactionListOut(
            transactions=transactions,
            total=total,
            page=skip // limit + 1,
            page_size=limit,
        ),
    }


# ─── Internal: Business / Rider Wallet Credit ─────────────────────────────────
# These endpoints are for internal service-to-service calls only.
# They are excluded from the public Swagger docs.

@router.post(
    "/business/credit",
    response_model=SuccessResponse[WalletTransactionOut],
    include_in_schema=False,
)
async def credit_business_wallet(
    business_user_id: UUID,
    gross_amount:     Decimal,
    platform_fee:     Decimal,
    description:      str,
    idempotency_key:  str,
    reference:        Optional[str] = None,
    db:               AsyncSession  = Depends(get_async_db),
):
    """
    Internal: credit business wallet after fee deduction.
    Blueprint §5.4: business receives gross_amount - platform_fee.
    Use transaction_service.process_payment() for standard checkout flows.
    idempotency_key is REQUIRED. Blueprint §5.6 HARD RULE.
    """
    txn = await wallet_service.credit_business_wallet(
        db,
        business_user_id=business_user_id,
        gross_amount=gross_amount,
        platform_fee=platform_fee,
        description=description,
        idempotency_key=idempotency_key,
        reference=reference,
    )
    return {"success": True, "data": txn}


@router.post(
    "/rider/credit",
    response_model=SuccessResponse[WalletTransactionOut],
    include_in_schema=False,
)
async def credit_rider_earnings(
    rider_user_id: UUID,
    delivery_fee:  Decimal,
    delivery_id:   UUID,
    description:   str,
    db:            AsyncSession = Depends(get_async_db),
):
    """
    Internal: credit rider wallet after delivery completion.
    Blueprint §5.3: same withdrawal rules as business wallet.
    """
    txn = await wallet_service.credit_rider_earnings(
        db,
        rider_user_id=rider_user_id,
        delivery_fee=delivery_fee,
        delivery_id=delivery_id,
        description=description,
    )
    return {"success": True, "data": txn}