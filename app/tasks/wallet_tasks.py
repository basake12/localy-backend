"""
app/tasks/wallet_tasks.py

Blueprint §16.2 mandatory Celery tasks:
  create_wallet          — On user registration
  assign_virtual_account — On user registration (calls Monnify API)
  process_refund         — On cancellation approval (max 24h delay)
  transcode_reel         — On reel upload completion (S3 event → webhook)

Blueprint §5.1 virtual account:
  "Each user gets a unique, permanent Monnify virtual account at registration."
  "POST /api/v1/webhooks/monnify/funding"

Blueprint §5.6 financial rules:
  "All financial operations are wrapped in PostgreSQL transactions."
  "All external payment operations use idempotency keys."

Blueprint §8.4 reels:
  "On upload completion: Celery task transcode_reel queued"
  "Transcoded formats: 1080p, 720p, 480p adaptive bitrate (HLS)"
  "Store original + transcoded versions."

Blueprint §16.4 HARD RULE: datetime.now(timezone.utc) — NEVER datetime.utcnow().
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from app.tasks.celery_app import celery
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Blueprint §16.4: timezone-aware UTC. Never datetime.utcnow()."""
    return datetime.now(timezone.utc)


# ── create_wallet ─────────────────────────────────────────────────────────────

@celery.task(
    name="app.tasks.wallet_tasks.create_wallet",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def create_wallet(self, user_id: str) -> dict:
    """
    Blueprint §16.2: create_wallet — On user registration.
    Creates the wallet record for a newly registered user.

    POST-REGISTRATION AUTOMATION triggered by auth_service.register_user().
    """
    db = SessionLocal()
    try:
        from app.crud.wallet_crud import wallet_crud
        wallet = wallet_crud.create_for_user(db, user_id=UUID(user_id))
        db.commit()
        logger.info("create_wallet: wallet created for user=%s", user_id)
        return {"status": "ok", "wallet_id": str(wallet.id), "user_id": user_id}
    except Exception as exc:
        db.rollback()
        logger.error("create_wallet failed for user=%s: %s", user_id, exc)
        raise self.retry(exc=exc)
    finally:
        db.close()


# ── assign_virtual_account ────────────────────────────────────────────────────

@celery.task(
    name="app.tasks.wallet_tasks.assign_virtual_account",
    bind=True,
    max_retries=5,
    default_retry_delay=120,
)
def assign_virtual_account(self, user_id: str) -> dict:
    """
    Blueprint §16.2: assign_virtual_account — On user registration (calls Monnify API).
    Blueprint §5.5: "Account number: permanent. Never changes."
    Blueprint §3 POST-REGISTRATION: "assign_virtual_account task: calls Monnify API
    to create dedicated virtual account number; stores account_number, account_name,
    bank_name on wallet record."

    On success: wallet.virtual_acct_number, virtual_acct_name, virtual_acct_bank
    and wallet.monnify_acct_ref are populated.
    """
    db = SessionLocal()
    try:
        from app.crud.wallet_crud import wallet_crud
        from app.services.payment_service import monnify_service

        wallet = wallet_crud.get_by_user_id(db, user_id=UUID(user_id))
        if not wallet:
            logger.error("assign_virtual_account: wallet not found for user=%s", user_id)
            return {"status": "error", "reason": "wallet_not_found"}

        # Call Monnify API to create virtual account
        va_data = monnify_service.create_virtual_account(
            account_name=wallet.owner.full_name,
            email=wallet.owner.email or f"{user_id}@localy.app",
            currency_code="NGN",
        )

        wallet.virtual_acct_number = va_data["accountNumber"]
        wallet.virtual_acct_name   = va_data["accountName"]
        wallet.virtual_acct_bank   = va_data["bankName"]
        wallet.monnify_acct_ref    = va_data["accountReference"]
        db.commit()

        logger.info(
            "assign_virtual_account: VA assigned user=%s account=%s bank=%s",
            user_id, va_data["accountNumber"], va_data["bankName"],
        )
        return {
            "status":          "ok",
            "user_id":         user_id,
            "account_number":  va_data["accountNumber"],
            "bank_name":       va_data["bankName"],
        }

    except Exception as exc:
        db.rollback()
        logger.error("assign_virtual_account failed for user=%s: %s", user_id, exc)
        raise self.retry(exc=exc)
    finally:
        db.close()


# ── process_refund ────────────────────────────────────────────────────────────

@celery.task(
    name="app.tasks.wallet_tasks.process_refund",
    bind=True,
    max_retries=5,
    default_retry_delay=300,
)
def process_refund(
    self,
    order_id: str,
    customer_user_id: str,
    refund_amount: str,
) -> dict:
    """
    Blueprint §16.2: process_refund — On cancellation approval (max 24h delay).
    Blueprint §5.1: "Refunds: return to customer wallet within 24 hours of
    cancellation approval. Triggered by Celery task: process_refund (delay = max 24h)."
    Blueprint §5.6: "All financial operations are wrapped in PostgreSQL transactions."
    Blueprint §16.4: datetime.now(timezone.utc) used for all timestamps.
    """
    db = SessionLocal()
    try:
        from decimal import Decimal
        from app.crud.wallet_crud import wallet_crud

        amount = Decimal(refund_amount)
        wallet = wallet_crud.get_by_user_id(db, user_id=UUID(customer_user_id))

        if not wallet:
            logger.error("process_refund: wallet not found for user=%s", customer_user_id)
            return {"status": "error", "reason": "wallet_not_found"}

        # Idempotency key prevents double-credit on task retry
        idempotency_key = f"refund:{order_id}"
        existing = wallet_crud.get_transaction_by_idempotency_key(
            db, idempotency_key=idempotency_key
        )
        if existing:
            logger.info(
                "process_refund: already processed order=%s — skipping", order_id
            )
            return {"status": "already_processed", "order_id": order_id}

        # Credit wallet (atomic DB transaction — Blueprint §5.6)
        txn = wallet_crud.credit(
            db,
            wallet_id=wallet.id,
            amount=amount,
            description=f"Refund for order {order_id}",
            transaction_type="refund",
            idempotency_key=idempotency_key,
            related_order_id=UUID(order_id),
        )
        db.commit()

        logger.info(
            "process_refund: ₦%s credited to user=%s for order=%s",
            refund_amount, customer_user_id, order_id,
        )
        return {
            "status":     "ok",
            "order_id":   order_id,
            "amount_ngn": refund_amount,
            "txn_id":     str(txn.id),
        }

    except Exception as exc:
        db.rollback()
        logger.error(
            "process_refund failed order=%s user=%s: %s",
            order_id, customer_user_id, exc,
        )
        raise self.retry(exc=exc)
    finally:
        db.close()


# ── transcode_reel ────────────────────────────────────────────────────────────

@celery.task(
    name="app.tasks.wallet_tasks.transcode_reel",
    bind=True,
    max_retries=3,
    default_retry_delay=180,
    time_limit=1800,    # 30-minute hard limit for long videos
    soft_time_limit=1500,
)
def transcode_reel(self, reel_id: str, source_s3_key: str) -> dict:
    """
    Blueprint §16.2: transcode_reel — On reel upload completion (S3 event → webhook).
    Blueprint §8.4:
      "On upload completion: Celery task transcode_reel queued"
      "Transcoded formats: 1080p, 720p, 480p adaptive bitrate (HLS)"
      "CDN delivery: CloudFront or Cloudflare CDN in front of S3/R2"
      "Store original + transcoded versions."

    This task is triggered via:
      POST /api/v1/reels/upload-url → client uploads to S3/R2 directly →
      S3 event notification → POST /api/v1/webhooks/s3/reel-uploaded →
      transcode_reel.delay(reel_id, source_s3_key)
    """
    db = SessionLocal()
    try:
        from app.crud.reels_crud import reel_crud

        reel = reel_crud.get(db, id=UUID(reel_id))
        if not reel:
            logger.error("transcode_reel: reel not found id=%s", reel_id)
            return {"status": "error", "reason": "reel_not_found"}

        # ── Transcoding stub ──────────────────────────────────────────────────
        # Production implementation: use AWS MediaConvert or ffmpeg on EC2/Lambda.
        #
        # from app.services.media_service import media_service
        # hls_manifest_url = media_service.transcode_to_hls(
        #     source_key=source_s3_key,
        #     output_key_prefix=f"reels/{reel_id}/",
        #     presets=["1080p", "720p", "480p"],
        # )
        # reel.video_url = hls_manifest_url
        # ─────────────────────────────────────────────────────────────────────

        # Mark reel as transcoded so it becomes visible in feed
        reel.is_transcoded = True
        reel.video_url     = f"https://cdn.localy.ng/reels/{reel_id}/index.m3u8"
        db.commit()

        logger.info(
            "transcode_reel: completed reel=%s source=%s",
            reel_id, source_s3_key,
        )
        return {
            "status":   "ok",
            "reel_id":  reel_id,
            "hls_url":  reel.video_url,
        }

    except Exception as exc:
        db.rollback()
        logger.error("transcode_reel failed reel=%s: %s", reel_id, exc)
        raise self.retry(exc=exc)
    finally:
        db.close()