"""
app/api/v1/wallet.py

CHANGES:
  1. /topup/verify: Paystack returns amount in KOBO — divides by 100
     before passing to wallet_service.credit_wallet().

  2. /crypto/initiate added — starts a crypto top-up order.

  3. /crypto/webhook added — receives NOWPayments webhook to confirm
     crypto payments and credit the wallet.

  4. /transfer now accepts recipient_wallet_number (LCY1234567) instead
     of a UUID — more user-friendly, matches blueprint UX.

  5. /withdraw: Full implementation for Business/Rider users.
     Customers remain blocked (spend-only wallets per Blueprint).

  6. /business/credit: Internal endpoint — credits business wallet
     after platform fee deduction. Called by order/booking services.

  7. /rider/credit: Internal endpoint — credits rider wallet after
     delivery completion. Called by delivery service.

  8. /withdrawal-limits: Returns min/max withdrawal info for
     Business/Rider users.
"""
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.core.database import get_async_db
from app.dependencies import (
    get_async_current_active_user,
    get_async_fully_verified_user,
)
from app.models.user_model import User
from app.models.wallet_model import TransactionType
from app.schemas.wallet_schema import (
    WalletOut,
    WalletTopUpRequest,
    WalletWithdrawRequest,
    WalletTransferRequest,
    WalletTransactionOut,
    WalletTransactionListOut,
    TopUpInitResponse,
    CryptoTopUpRequest,
    CryptoTopUpOut,
)
from app.schemas.common_schema import SuccessResponse
from app.services.wallet_service import wallet_service
from app.services.payment_service import payment_service

router = APIRouter(tags=["Wallet"])


# ─── Balance ──────────────────────────────────────────────────────────────────

@router.get("", response_model=SuccessResponse[WalletOut])
async def get_my_wallet(
    db: AsyncSession  = Depends(get_async_db),
    user: User        = Depends(get_async_current_active_user),
):
    wallet = await wallet_service.get_user_wallet(db, user_id=user.id)
    return {"success": True, "data": wallet}


@router.get("/balance", response_model=SuccessResponse[WalletOut])
async def get_wallet_balance(
    db: AsyncSession  = Depends(get_async_db),
    user: User        = Depends(get_async_current_active_user),
):
    """Alias called by Flutter home screen wallet card."""
    wallet = await wallet_service.get_user_wallet(db, user_id=user.id)
    return {"success": True, "data": wallet}


# ─── Card / Bank / USSD Top-Up ────────────────────────────────────────────────

@router.post(
    "/topup",
    response_model=SuccessResponse[TopUpInitResponse],
    status_code=status.HTTP_201_CREATED,
)
async def topup_wallet(
    payload: WalletTopUpRequest,
    db: AsyncSession = Depends(get_async_db),
    user: User       = Depends(get_async_current_active_user),
):
    """
    Initialise a wallet top-up (Blueprint §4.1.1).

    payment_method → gateway routing (resolved by WalletTopUpRequest schema):
      card           → Paystack  (instant debit/credit card)
      bank_transfer  → Monnify   (dedicated virtual account, ~60s clearing)
      ussd           → Paystack  (USSD channel)

    Returns authorization_url for card/USSD flows.
    For bank_transfer, the virtual account is already assigned to the user
    (provisioned at registration via Monnify webhook) — no redirect needed.

    Minimum top-up: ₦500 | Daily limit: ₦500,000 (enforced at service layer)
    """
    payment = await payment_service.initialize_transaction(
        email=user.email,
        amount=payload.amount,
        metadata={
            "user_id":        str(user.id),
            "type":           "wallet_topup",
            "payment_method": payload.payment_method,   # card | bank_transfer | ussd
            "gateway":        payload.gateway,           # paystack | monnify (derived)
        },
        gateway=payload.gateway,   # derived automatically from payment_method
    )
    payment_data = payment.get("data", {})
    if not payment_data.get("authorization_url"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment gateway failed to initialize transaction",
        )
    return {
        "success": True,
        "data": TopUpInitResponse(
            authorization_url=payment_data["authorization_url"],
            reference=payment_data["reference"],
            amount=payload.amount,
            gateway=payload.gateway,
        ),
    }


@router.post("/topup/verify", response_model=SuccessResponse[WalletTransactionOut])
async def verify_topup(
    reference: str   = Query(...),
    db: AsyncSession = Depends(get_async_db),
    user: User       = Depends(get_async_current_active_user),
):
    """
    Verify payment callback and credit wallet.
    FIX: Paystack returns amount in KOBO → divide by 100 to get Naira.
    Idempotent — safe to call multiple times for same reference.
    """
    result   = await payment_service.verify_transaction(reference)
    pay_data = result.get("data", {})

    if not result.get("status") or pay_data.get("status") != "success":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Payment not successful",
        )

    # payment_service.verify_transaction() already converts kobo → NGN.
    # Do NOT divide by 100 here — that was a double-conversion bug.
    amount_ngn = Decimal(str(pay_data.get("amount", 0)))

    transaction = await wallet_service.credit_wallet(
        db,
        user_id=user.id,
        amount_ngn=amount_ngn,
        description="Wallet top-up via Paystack",
        reference=reference,
        metadata={"payment_data": pay_data},
    )
    return {"success": True, "data": transaction}


# ─── Crypto Top-Up ────────────────────────────────────────────────────────────

@router.post(
    "/crypto/initiate",
    response_model=SuccessResponse[CryptoTopUpOut],
    status_code=status.HTTP_201_CREATED,
)
async def initiate_crypto_topup(
    payload: CryptoTopUpRequest,
    db: AsyncSession = Depends(get_async_db),
    user: User       = Depends(get_async_current_active_user),
):
    """
    Start a crypto top-up.
    Returns a deposit address — user sends exact crypto amount to it.
    Wallet is credited in NGN automatically when payment is confirmed.
    """
    top_up = await wallet_service.initiate_crypto_top_up(
        db,
        user_id=user.id,
        ngn_amount=payload.ngn_amount,
        crypto_currency=payload.crypto_currency,
        crypto_network=payload.crypto_network,
    )
    return {"success": True, "data": top_up}


@router.post("/crypto/webhook", include_in_schema=False)
async def crypto_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """
    NOWPayments webhook — called when a crypto payment is confirmed.
    No auth — secured by payload signature verification.
    """
    body = await request.json()

    # Verify NOWPayments signature
    import hmac, hashlib
    from app.config import settings
    sig_header = request.headers.get("x-nowpayments-sig", "")
    sorted_body = _sort_dict_recursive(body)
    import json
    expected = hmac.new(
        settings.NOWPAYMENTS_IPN_SECRET.encode(),
        json.dumps(sorted_body, separators=(",", ":")).encode(),
        hashlib.sha512,
    ).hexdigest()
    if not hmac.compare_digest(sig_header, expected):
        raise HTTPException(status_code=400, detail="Invalid signature")

    payment_status  = body.get("payment_status")
    provider_id     = str(body.get("payment_id", ""))
    received_crypto = Decimal(str(body.get("actually_paid", 0)))
    confirmations   = int(body.get("confirmations", 0))

    if payment_status in ("finished", "confirmed"):
        await wallet_service.confirm_crypto_top_up(
            db,
            provider_order_id=provider_id,
            received_crypto=received_crypto,
            confirmations=confirmations,
        )

    return {"status": "ok"}


def _sort_dict_recursive(d):
    if isinstance(d, dict):
        return {k: _sort_dict_recursive(v) for k, v in sorted(d.items())}
    if isinstance(d, list):
        return [_sort_dict_recursive(i) for i in d]
    return d


# ─── Withdraw ─────────────────────────────────────────────────────────────────

@router.post("/withdraw", response_model=SuccessResponse[WalletTransactionOut])
async def withdraw_from_wallet(
    payload: WalletWithdrawRequest,
    db: AsyncSession = Depends(get_async_db),
    user: User       = Depends(get_async_fully_verified_user),
):
    """
    Withdraw funds to bank account.

    Per Blueprint Section 4.2 & 4.3:
    - CUSTOMERS CANNOT WITHDRAW (wallet is spend-only)
    - Business and Rider users can withdraw to bank account
    - Minimum: ₦1,000, Maximum: ₦1,000,000/day
    - PIN required for all withdrawals
    """
    if user.user_type == "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer wallets are spend-only. Withdrawals not permitted.",
        )

    if user.user_type not in ["business", "rider"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only business and rider accounts can withdraw funds",
        )

    transaction = await wallet_service.process_payout(
        db,
        user_id=user.id,
        amount_ngn=payload.amount,
        bank_account=payload.bank_account_number,
        bank_code=payload.bank_code,
        recipient_name=payload.recipient_name,
        description=payload.description,
    )

    return {"success": True, "data": transaction}


# ─── Withdrawal Limits ────────────────────────────────────────────────────────

@router.get("/withdrawal-limits", response_model=SuccessResponse[dict])
async def get_withdrawal_limits(
    user: User = Depends(get_async_current_active_user),
):
    """Get withdrawal limits for current user."""
    if user.user_type == "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customers cannot withdraw funds",
        )

    return {
        "success": True,
        "data": {
            "minimum_withdrawal": 1000.00,
            "maximum_daily_withdrawal": 1000000.00,
            "currency": "NGN",
            "processing_time": "Same day or next business day",
        },
    }


# ─── Transfer ─────────────────────────────────────────────────────────────────

@router.post("/transfer", response_model=SuccessResponse[dict])
async def transfer_funds(
    payload: WalletTransferRequest,
    db: AsyncSession = Depends(get_async_db),
    user: User       = Depends(get_async_fully_verified_user),
):
    """
    Transfer to another Localy user by wallet number (e.g. LCY1234567).
    Requires phone verification.
    """
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


# ─── Transactions ─────────────────────────────────────────────────────────────

@router.get("/transactions", response_model=SuccessResponse[WalletTransactionListOut])
async def get_wallet_transactions(
    transaction_type: TransactionType = Query(None),
    skip:  int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db:   AsyncSession = Depends(get_async_db),
    user: User         = Depends(get_async_current_active_user),
):
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


# ─── Internal: Business Wallet Credit ─────────────────────────────────────────

@router.post(
    "/business/credit",
    response_model=SuccessResponse[WalletTransactionOut],
    include_in_schema=False,  # Internal endpoint
)
async def credit_business_wallet(
    business_user_id: UUID,
    gross_amount: Decimal,
    platform_fee: Decimal,
    description: str,
    reference: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Internal endpoint to credit business wallet after payment.
    Called by order/booking services after customer payment.

    Per Blueprint: Platform fee deducted before crediting business.
    """
    transaction = await wallet_service.credit_business_wallet(
        db,
        business_user_id=business_user_id,
        gross_amount=gross_amount,
        platform_fee=platform_fee,
        description=description,
        reference=reference,
    )

    return {"success": True, "data": transaction}


# ─── Internal: Rider Earnings Credit ──────────────────────────────────────────

@router.post(
    "/rider/credit",
    response_model=SuccessResponse[WalletTransactionOut],
    include_in_schema=False,  # Internal endpoint
)
async def credit_rider_earnings(
    rider_user_id: UUID,
    delivery_fee: Decimal,
    delivery_id: UUID,
    description: str,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Internal endpoint to credit rider wallet after delivery completion.
    Called by delivery service.
    """
    transaction = await wallet_service.credit_rider_earnings(
        db,
        rider_user_id=rider_user_id,
        delivery_fee=delivery_fee,
        delivery_id=delivery_id,
        description=description,
    )

    return {"success": True, "data": transaction}