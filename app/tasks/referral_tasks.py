# app/tasks/referral_tasks.py
"""
Celery tasks for the Referral System.

Tasks:
  - expire_stale_referrals   : Nightly — marks PENDING referrals EXPIRED
                               after 30 days (Blueprint §6.1: one-time trigger).
  - credit_referral_reward   : Async wrapper called by order/booking services
                               after a referred user's first transaction is
                               confirmed; decouples wallet credit from the
                               checkout critical path.
"""
import logging
from decimal import Decimal
from uuid import UUID

from app.tasks.celery_app import celery
# SyncSessionLocal removed — tasks use AsyncSessionLocal directly (see _run() below)

logger = logging.getLogger(__name__)


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
    """
    try:
        import asyncio
        from app.core.database import AsyncSessionLocal
        from app.crud.referral import expire_stale_referrals as crud_expire

        async def _run():
            async with AsyncSessionLocal() as db:
                count = await crud_expire(db)
                return count

        count = asyncio.get_event_loop().run_until_complete(_run())
        logger.info(f"expire_stale_referrals: expired {count} referrals")
        return {"expired": count}
    except Exception as exc:
        logger.error(f"expire_stale_referrals failed: {exc}")
        raise self.retry(exc=exc)


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
    Async Celery task to complete a referral and credit the referrer's wallet.
    Called by order/booking services after first transaction is confirmed.

    Args:
        referred_user_id  : UUID string of the new (referred) user.
        first_order_amount: Decimal-string amount of the first order (e.g. "3500.00").
    """
    try:
        import asyncio
        from app.core.database import AsyncSessionLocal
        from app.services.referral_service import complete_referral_on_first_transaction

        async def _run():
            async with AsyncSessionLocal() as db:
                await complete_referral_on_first_transaction(
                    db,
                    referred_user_id=UUID(referred_user_id),
                    first_order_amount=Decimal(first_order_amount),
                )

        asyncio.get_event_loop().run_until_complete(_run())
        logger.info(
            f"credit_referral_reward: processed referral for user {referred_user_id}"
        )
        return {"status": "ok", "referred_user_id": referred_user_id}
    except Exception as exc:
        logger.error(f"credit_referral_reward failed: {exc}")
        raise self.retry(exc=exc)