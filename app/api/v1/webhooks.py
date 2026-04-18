"""
app/api/v1/webhooks.py  ← NEW FILE

[AUDIT BUG-8 FIX] — Dedicated webhooks router mounted at /webhooks prefix.

Root cause of original bug:
  Monnify and Paystack webhook endpoints were defined inside wallet.py, which is
  mounted at prefix "/wallet" in router.py. This produced actual paths:
    /api/v1/wallet/webhooks/monnify/funding   ← WRONG
    /api/v1/wallet/webhooks/paystack/payment  ← WRONG

  Blueprint §15 / §5.1 / §5.5 specifies:
    POST /api/v1/webhooks/monnify/funding     ← CORRECT
    POST /api/v1/webhooks/paystack/payment    ← CORRECT

  Monnify was delivering to the wrong URL → 404 on every webhook → no wallet
  was ever funded via bank transfer.

Fix:
  1. This new file defines ONLY the webhook endpoints.
  2. router.py mounts this router at prefix "/webhooks".
  3. wallet.py now contains only wallet CRUD endpoints (no webhook routes).

Security:
  Blueprint §15: "Webhooks (no JWT — HMAC signature verification instead)"
  - NO authentication dependency (no Bearer token required).
  - HMAC-SHA512 verification MUST be the first operation before any payload processing.
  - Returns 200/400 only — never 401/403 (webhook callers are not users).

Blueprint §5.1 / §5.5:
  Monnify webhook:
    Header:   X-Monnify-Signature (HMAC-SHA512 of raw body using MONNIFY_SECRET_KEY)
    Verify:   hmac.compare_digest(computed, header)
    Idem key: wallet_transactions.external_reference UNIQUE constraint
    Flow:     credit wallet → emit WebSocket "wallet_credited" → push notification
    Timing:   within 60 seconds of bank transfer confirmation

  Paystack webhook:
    Header:   X-Paystack-Signature (HMAC-SHA512 of raw body using PAYSTACK_SECRET_KEY)
    Verify:   same pattern
    Event:    charge.success only
    Amounts:  IN KOBO — divide by 100 before crediting wallet
    Idem key: external_reference UNIQUE constraint (Paystack reference)
"""
import hashlib
import hmac
import json
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.config import settings
from app.core.database import get_async_db
from app.services.wallet_service import wallet_service

router = APIRouter(tags=["Webhooks"])
logger = logging.getLogger(__name__)


# ─── Monnify Virtual Account Funding ──────────────────────────────────────────

@router.post(
    "/monnify/funding",
    include_in_schema=False,   # Exclude from Swagger — internal endpoint
    summary="Monnify bank transfer webhook",
)
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
      (idempotency key = monnify_transaction_reference, stored in
       wallet_transactions.external_reference — UNIQUE constraint)
      On success: credit wallet, emit WebSocket event 'wallet_credited',
      send push notification 'Your wallet has been funded with ₦X,XXX'
      Processing time: within 60 seconds of bank transfer confirmation"

    Blueprint §5.5 HMAC-SHA512 verification:
      Header: X-Monnify-Signature
      Key:    MONNIFY_SECRET_KEY
      Method: HMAC-SHA512 hexdigest of raw body bytes

    [BUG-8 FIX] Correct path: /api/v1/webhooks/monnify/funding
    [BUG-9 FIX] HMAC compare: computed value first, external header second.
    """
    raw_body = await request.body()

    # ── Signature verification — Blueprint §5.5 ───────────────────────────────
    sig_header = request.headers.get("X-Monnify-Signature", "")
    computed   = hmac.new(
        settings.MONNIFY_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    # [BUG-9 FIX] computed (local) first, sig_header (external) second.
    if not hmac.compare_digest(computed, sig_header):
        logger.warning(
            "Monnify webhook: invalid HMAC signature from %s",
            request.client.host if request.client else "unknown",
        )
        # Return 400, not 401/403 — webhook callers are not authenticated users.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    # ── Parse payload ─────────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    # Only process PAID events — Blueprint §5.1
    payment_status = payload.get("paymentStatus") or payload.get("transactionStatus")
    if payment_status != "PAID":
        logger.info(
            "Monnify webhook: skipping non-PAID event (status=%s)", payment_status
        )
        return {"status": "ignored", "reason": "not_paid"}

    # ── Extract fields ────────────────────────────────────────────────────────
    # Blueprint §5.1: "Monnify sends amountPaid in Naira — no kobo conversion needed"
    amount_ngn        = Decimal(str(payload.get("amountPaid", 0)))
    monnify_reference = payload.get("transactionReference", "")
    reserved_info     = payload.get("reservedAccountInfo") or {}
    virtual_account   = (
        payload.get("destinationAccountNumber")
        or reserved_info.get("accountNumber", "")
    )
    payment_source    = payload.get("paymentSource") or {}
    sender_bank       = payment_source.get("bankName", "")
    sender_name       = payment_source.get("accountName", "")

    if not monnify_reference or not virtual_account:
        logger.error(
            "Monnify webhook: missing transactionReference or virtual account. "
            "payload keys: %s", list(payload.keys()),
        )
        return {"status": "error", "reason": "missing_fields"}

    if amount_ngn <= 0:
        logger.warning("Monnify webhook: zero or negative amount, ref=%s", monnify_reference)
        return {"status": "error", "reason": "invalid_amount"}

    # ── Credit wallet ─────────────────────────────────────────────────────────
    txn = await wallet_service.handle_monnify_funding(
        db,
        monnify_reference=monnify_reference,
        virtual_account_number=virtual_account,
        amount_ngn=amount_ngn,
        sender_bank=sender_bank,
        sender_name=sender_name,
    )

    if txn:
        logger.info(
            "Monnify: wallet credited ₦%s ref=%s", amount_ngn, monnify_reference
        )
    else:
        logger.info(
            "Monnify: webhook processed but no credit (duplicate or below minimum) ref=%s",
            monnify_reference,
        )

    # Always return 200 to Monnify so they don't retry
    return {"status": "ok"}


# ─── Paystack Card Payment ─────────────────────────────────────────────────────

@router.post(
    "/paystack/payment",
    include_in_schema=False,   # Exclude from Swagger — internal endpoint
    summary="Paystack card payment webhook",
)
async def paystack_payment_webhook(
    request: Request,
    db:      AsyncSession = Depends(get_async_db),
):
    """
    Paystack CHARGE.SUCCESS webhook — credits wallet after card top-up.

    Blueprint §5.1:
      "On Paystack webhook CHARGE.SUCCESS: same idempotency flow as Monnify.
       Paystack amounts are in kobo — DIVIDE by 100 before crediting wallet."

    Signature verification:
      Header: X-Paystack-Signature (HMAC-SHA512 of raw body using PAYSTACK_SECRET_KEY)

    Idempotency:
      Paystack reference stored as external_reference (UNIQUE constraint).
      Duplicate webhook deliveries are safe — second call returns the existing txn.

    [BUG-8 FIX] Correct path: /api/v1/webhooks/paystack/payment
    [BUG-9 FIX] HMAC compare: computed value first, external header second.
    """
    raw_body = await request.body()

    # ── Signature verification ────────────────────────────────────────────────
    sig_header = request.headers.get("X-Paystack-Signature", "")
    computed   = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    # [BUG-9 FIX] computed (local) first, sig_header (external) second.
    if not hmac.compare_digest(computed, sig_header):
        logger.warning(
            "Paystack webhook: invalid HMAC signature from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    # ── Parse payload ─────────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    event = payload.get("event")
    if event != "charge.success":
        logger.info("Paystack webhook: ignoring event '%s'", event)
        return {"status": "ignored"}

    # ── Extract fields ────────────────────────────────────────────────────────
    data      = payload.get("data") or {}
    reference = data.get("reference", "")
    metadata  = data.get("metadata") or {}

    # Only process wallet top-up events (other Paystack events, e.g. subscription,
    # are handled elsewhere)
    if metadata.get("type") != "wallet_topup":
        logger.info(
            "Paystack webhook: ignoring non-wallet_topup event (ref=%s type=%s)",
            reference, metadata.get("type"),
        )
        return {"status": "ignored", "reason": "not_wallet_topup"}

    user_id_str = metadata.get("user_id")
    if not user_id_str:
        logger.error(
            "Paystack webhook: missing user_id in metadata. ref=%s", reference
        )
        return {"status": "error", "reason": "missing_user_id"}

    # Blueprint §5.1: "Paystack amounts are in kobo — DIVIDE by 100 before crediting"
    amount_kobo = Decimal(str(data.get("amount", 0)))
    amount_ngn  = amount_kobo / Decimal("100")

    if amount_ngn <= 0:
        logger.warning("Paystack webhook: zero or negative amount, ref=%s", reference)
        return {"status": "error", "reason": "invalid_amount"}

    # ── Credit wallet ─────────────────────────────────────────────────────────
    # wallet_service.credit_wallet() is the correct method for Paystack top-ups.
    # It handles idempotency via external_reference UNIQUE constraint.
    try:
        user_id = UUID(user_id_str)
    except ValueError:
        logger.error("Paystack webhook: invalid user_id UUID '%s', ref=%s", user_id_str, reference)
        return {"status": "error", "reason": "invalid_user_id"}

    txn = await wallet_service.credit_wallet(
        db,
        user_id=user_id,
        amount_ngn=amount_ngn,
        description="Wallet top-up via card payment (Paystack)",
        reference=reference,
        metadata={"paystack_data": data},
    )

    logger.info(
        "Paystack: wallet credited ₦%s ref=%s user=%s",
        amount_ngn, reference, user_id_str,
    )
    # Always return 200 to Paystack so they don't retry
    return {"status": "ok"}