"""
app/api/v1/wallet.py

FIXES vs previous version:
  1.  [CRITICAL] Monnify webhook endpoint added:
      POST /webhooks/monnify/funding
      Blueprint §5.1 / §5.5: "Transfer from any Nigerian bank triggers Monnify
      webhook → POST /api/v1/webhooks/monnify/funding"
      HMAC-SHA512 signature verification via X-Monnify-Signature header.
      Blueprint §5.5: "verify HMAC signature → check idempotency key"

  2.  Crypto endpoints (/crypto/initiate, /crypto/webhook) DELETED.
      Blueprint §5: Monnify + Paystack ONLY.

  3.  user.user_type → user.role.value throughout. Blueprint §14.

  4.  WalletTopUpRequest minimum ₦500 → ₦1,000. Blueprint §5.1.

  5.  /topup uses user.phone_number as Paystack identifier fallback.
      user.email is optional in blueprint — phone_number is always present.

  6.  /withdraw uses user.role.value instead of user.user_type.

  7.  /withdrawal-limits returns correct blueprint values
      (min ₦1,000, max ₦1,000,000 daily).

  8.  Paystack CHARGE.SUCCESS webhook endpoint added.
      Blueprint §5.1: "On Paystack webhook CHARGE.SUCCESS: same idempotency
      flow as Monnify above. Paystack amounts are in kobo — DIVIDE by 100."
"""
import hashlib
import hmac
import json
import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_async_db
from app.dependencies import (
    get_async_current_active_user,
    get_async_fully_verified_user,
    require_pin_set,
)
from app.models.user_model import User
from app.models.wallet_model import TransactionType
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
    """Get wallet balance + virtual account info. Blueprint §5.1."""
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
    Blueprint §5.1: permanent, never changes even on phone number update.
    """
    wallet = await wallet_service.get_user_wallet(db, user_id=user.id)
    if not wallet.virtual_acct_number:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Virtual account not yet provisioned. Please try again shortly.",
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

    For bank transfers, users send money to their permanent Monnify virtual
    account — no action needed here. The Monnify webhook auto-credits the wallet.
    """
    # Use phone_number as Paystack email fallback (email is optional in blueprint)
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
            detail="Paystack failed to initialise transaction",
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
    # Do NOT divide again — that would be a double-conversion.
    amount_ngn = Decimal(str(pay_data.get("amount", 0)))

    txn = await wallet_service.credit_wallet(
        db,
        user_id=user.id,
        amount_ngn=amount_ngn,
        description="Wallet top-up via Paystack",
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
    - Business / Rider: min ₦1,000, max ₦1,000,000/day, same/next business day.
    - Blueprint §3.3: PIN required for all withdrawals — verified in request body.
    """
    # Blueprint §3.3: verify PIN before withdrawal
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
    """Return withdrawal limits. Blueprint §5.2."""
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    if role_val == "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customers cannot withdraw funds.",
        )
    return {
        "success": True,
        "data": {
            "minimum_withdrawal":    1000.0,    # Blueprint §5.2
            "maximum_daily":         1000000.0, # Blueprint §5.2
            "currency":              "NGN",
            "processing_time":       "Same business day or next business day",
            "pin_required":          True,       # Blueprint §3.3
        },
    }


# ─── Transfer ─────────────────────────────────────────────────────────────────

@router.post("/transfer", response_model=SuccessResponse[dict])
async def transfer_funds(
    payload: WalletTransferRequest,
    db:      AsyncSession = Depends(get_async_db),
    user:    User         = Depends(get_async_fully_verified_user),
):
    """Transfer to another Localy user by wallet number (LCY...)."""
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
    transaction_type: str   = Query(..., description="product_purchase|food_order|hotel_booking|service_booking|health_appointment|ticket_sale"),
    user:             User  = Depends(get_async_current_active_user),
):
    """
    Return fee breakdown for checkout display.
    Blueprint §5.4: "All fees shown transparently in checkout summary
    before user confirms."
    """
    breakdown = transaction_service.get_fee_breakdown(
        Decimal(str(product_price)), transaction_type
    )
    return {"success": True, "data": FeeBreakdownOut(**breakdown)}


# ─── Transactions ─────────────────────────────────────────────────────────────

@router.get("/transactions", response_model=SuccessResponse[WalletTransactionListOut])
async def get_wallet_transactions(
    transaction_type: TransactionType = Query(None),
    skip:  int        = Query(0, ge=0),
    limit: int        = Query(20, ge=1, le=100),
    db:    AsyncSession = Depends(get_async_db),
    user:  User         = Depends(get_async_current_active_user),
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


# ─── Monnify Webhook — PRIMARY FUNDING (Blueprint §5.1 / §5.5) ───────────────

@router.post("/webhooks/monnify/funding", include_in_schema=False)
async def monnify_funding_webhook(
    request: Request,
    db:      AsyncSession = Depends(get_async_db),
):
    """
    Monnify bank transfer webhook — auto-credits wallet on bank transfer.

    Blueprint §5.1:
      "Transfer from any Nigerian bank triggers Monnify webhook →
      POST /api/v1/webhooks/monnify/funding
      Backend: verify HMAC signature → check idempotency key
      (stored in wallet_transactions.external_reference — UNIQUE constraint)
      On success: credit wallet, emit WebSocket event 'wallet_credited',
      send push notification 'Your wallet has been funded with ₦X,XXX'
      Processing time: within 60 seconds of bank transfer confirmation"

    Blueprint §5.5: HMAC-SHA512 verification.
      Header: X-Monnify-Signature
      Verify: hmac.compare_digest(computed_sig, header_sig)
    """
    raw_body = await request.body()

    # ── Signature verification — Blueprint §5.5 ───────────────────────────────
    sig_header = request.headers.get("X-Monnify-Signature", "")
    computed   = hmac.new(
        settings.MONNIFY_SECRET_KEY.encode(),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    if not hmac.compare_digest(sig_header, computed):
        logger.warning("Monnify webhook: invalid HMAC signature")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    # Only process PAID events
    payment_status = payload.get("paymentStatus") or payload.get("transactionStatus")
    if payment_status != "PAID":
        logger.info("Monnify webhook: skipping non-PAID event (status=%s)", payment_status)
        return {"status": "ignored", "reason": "not_paid"}

    # Blueprint §5.1: "Monnify amounts are in Naira (amountPaid) — no conversion"
    amount_ngn        = Decimal(str(payload.get("amountPaid", 0)))
    monnify_reference = payload.get("transactionReference", "")
    virtual_account   = payload.get("destinationAccountNumber", "") or \
                        (payload.get("reservedAccountInfo", {}) or {}).get("accountNumber", "")
    sender_bank       = (payload.get("paymentSource", {}) or {}).get("bankName", "")
    sender_name       = (payload.get("paymentSource", {}) or {}).get("accountName", "")

    if not monnify_reference or not virtual_account:
        logger.error("Monnify webhook: missing reference or virtual account. payload=%s", payload)
        return {"status": "error", "reason": "missing_fields"}

    txn = await wallet_service.handle_monnify_funding(
        db,
        monnify_reference=monnify_reference,
        virtual_account_number=virtual_account,
        amount_ngn=amount_ngn,
        sender_bank=sender_bank,
        sender_name=sender_name,
    )
    if txn:
        logger.info("Monnify: wallet credited ₦%s ref=%s", amount_ngn, monnify_reference)
    return {"status": "ok"}


# ─── Paystack Webhook (CHARGE.SUCCESS) ───────────────────────────────────────

@router.post("/webhooks/paystack/payment", include_in_schema=False)
async def paystack_payment_webhook(
    request: Request,
    db:      AsyncSession = Depends(get_async_db),
):
    """
    Paystack CHARGE.SUCCESS webhook.
    Blueprint §5.1: "On Paystack webhook CHARGE.SUCCESS: same idempotency flow.
    Paystack amounts are in kobo — DIVIDE by 100 before crediting wallet."

    Signature verification: X-Paystack-Signature = HMAC-SHA512 of raw body
    using PAYSTACK_SECRET_KEY.
    """
    raw_body = await request.body()

    # Signature verification
    sig_header = request.headers.get("X-Paystack-Signature", "")
    computed   = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode(),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    if not hmac.compare_digest(sig_header, computed):
        logger.warning("Paystack webhook: invalid HMAC signature")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    event = payload.get("event")
    if event != "charge.success":
        return {"status": "ignored"}

    data      = payload.get("data", {})
    reference = data.get("reference", "")
    metadata  = data.get("metadata", {})

    # Only process wallet top-up events
    if metadata.get("type") != "wallet_topup":
        return {"status": "ignored", "reason": "not_wallet_topup"}

    user_id_str = metadata.get("user_id")
    if not user_id_str:
        logger.error("Paystack webhook: missing user_id in metadata. ref=%s", reference)
        return {"status": "error", "reason": "missing_user_id"}

    # Blueprint §5.1: "Paystack amounts in kobo — DIVIDE by 100 before crediting"
    amount_kobo = Decimal(str(data.get("amount", 0)))
    amount_ngn  = amount_kobo / 100

    txn = await wallet_service.credit_wallet(
        db,
        user_id=UUID(user_id_str),
        amount_ngn=amount_ngn,
        description="Wallet top-up via Paystack",
        reference=reference,
        metadata={"payment_data": data},
    )
    logger.info("Paystack: wallet credited ₦%s ref=%s", amount_ngn, reference)
    return {"status": "ok"}


# ─── Internal: Business / Rider Wallet Credit ─────────────────────────────────

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
    Use transaction_service.process_payment() for standard flows.
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
    """Internal: credit rider wallet after delivery completion."""
    txn = await wallet_service.credit_rider_earnings(
        db,
        rider_user_id=rider_user_id,
        delivery_fee=delivery_fee,
        delivery_id=delivery_id,
        description=description,
    )
    return {"success": True, "data": txn}