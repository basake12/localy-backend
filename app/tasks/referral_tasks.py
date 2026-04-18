"""
app/tasks/referral_tasks.py

Blueprint §9.1 Referral Programme:
  "Referral reward triggers via Celery task: credit_referral_reward
   (enqueued on order status change to 'completed')"
  "One reward per referred user — no repeated rewards for same person."
  Referrer reward: ₦1,000. Blueprint §9.1 + §16.2.

FIXES vs previous version:
  1. asyncio.get_event_loop().run_until_complete() → asyncio.run().
     get_event_loop() in non-async context is deprecated in Python 3.10
     and raises RuntimeError in Python 3.12 if no current event loop exists.
     Both expire_stale_referrals and credit_referral_reward were affected.

  2. Task names use full dotted path:
     "app.tasks.referral_tasks.expire_stale_referrals"
     "app.tasks.referral_tasks.credit_referral_reward"
     Matches celery_app.py beat schedule and include list.

Blueprint §16.4 HARD RULE: datetime.now(timezone.utc) — confirmed in called services.
"""

import asyncio
import logging
from decimal import Decimal
from uuid import UUID

from app.tasks.celery_app import celery

logger = logging.getLogger(__name__)


# ── expire_stale_referrals ────────────────────────────────────────────────────

@celery.task(
    name="app.tasks.referral_tasks.expire_stale_referrals",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def expire_stale_referrals(self) -> dict:
    """
    Marks PENDING referrals whose expires_at has passed as EXPIRED.
    Scheduled nightly via celery beat.
    Blueprint §9.1: "One reward per referred user — no repeated rewards."
    """
    try:
        from app.core.database import AsyncSessionLocal
        from app.crud.referral_crud import expire_stale_referrals as crud_expire

        async def _run() -> int:
            async with AsyncSessionLocal() as db:
                count = await crud_expire(db)
                return count

        # FIX: was asyncio.get_event_loop().run_until_complete(_run())
        # Deprecated Python 3.10+, RuntimeError in Python 3.12
        count = asyncio.run(_run())
        logger.info("expire_stale_referrals: expired %d referrals", count)
        return {"expired": count}
    except Exception as exc:
        logger.error("expire_stale_referrals failed: %s", exc)
        raise self.retry(exc=exc)


# ── credit_referral_reward ────────────────────────────────────────────────────

@celery.task(
    name="app.tasks.referral_tasks.credit_referral_reward",
    bind=True,
    max_retries=5,
    default_retry_delay=60,
)
def credit_referral_reward(
    self,
    referred_user_id: str,
    first_order_amount: str,
) -> dict:
    """
    Blueprint §16.2: credit_referral_reward — enqueued on order status = 'completed'.
    Blueprint §9.1:
      "Referrer reward: ₦1,000 credited to referrer's wallet on referred user's
       first COMPLETED purchase (not at registration)."
      "Referral reward triggers via Celery task: credit_referral_reward
       (enqueued on order status change to 'completed')"
      "One reward per referred user — no repeated rewards for same person.
       (referrals table: referrer_id, referred_id, reward_credited BOOLEAN)"

    Args:
        referred_user_id  : UUID string of the new (referred) user.
        first_order_amount: Decimal-string amount of the first order (e.g. "3500.00").
    """
    try:
        from app.core.database import AsyncSessionLocal
        from app.services.referral_service import complete_referral_on_first_transaction

        async def _run() -> None:
            async with AsyncSessionLocal() as db:
                await complete_referral_on_first_transaction(
                    db,
                    referred_user_id=UUID(referred_user_id),
                    first_order_amount=Decimal(first_order_amount),
                )

        # FIX: was asyncio.get_event_loop().run_until_complete(_run())
        asyncio.run(_run())

        logger.info(
            "credit_referral_reward: processed for user=%s order_amount=%s",
            referred_user_id, first_order_amount,
        )
        return {"status": "ok", "referred_user_id": referred_user_id}
    except Exception as exc:
        logger.error("credit_referral_reward failed user=%s: %s", referred_user_id, exc)
        raise self.retry(exc=exc)