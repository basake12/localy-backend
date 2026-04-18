"""
app/tasks/promotions_tasks.py

FIXES vs previous version:
  1. send_push_notification_task import FIXED.
     from app.tasks.notification_tasks import send_push_notification_task
     — that name didn't exist in notification_tasks.py causing ImportError
     which crashed the entire task at runtime.
     Fixed by importing the canonical name: send_push_notification
     (which also exports send_push_notification_task as an alias).
"""

import asyncio
import logging

from celery import shared_task

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


@shared_task(name="tasks.sync_promotion_statuses")
def sync_promotion_statuses() -> str:
    """
    Sync promotion statuses based on current time.
    - SCHEDULED → ACTIVE when start_date is reached
    - ACTIVE → ENDED when end_date has passed
    Runs every hour via Celery Beat.
    """
    db = SessionLocal()
    try:
        from app.core.database import AsyncSessionLocal
        from app.crud.promotions_crud import promotion_crud

        async def _sync():
            async with AsyncSessionLocal() as async_db:
                count = await promotion_crud.sync_statuses(async_db)
                await async_db.commit()
                return count

        count = asyncio.run(_sync())
        logger.info("sync_promotion_statuses: synced %d records", count)
        return f"Synced {count} promotion statuses"
    except Exception:
        logger.exception("Error in sync_promotion_statuses task")
        raise
    finally:
        db.close()


@shared_task(name="tasks.notify_streak_milestones")
def notify_streak_milestones() -> str:
    """
    Notify users who are close to completing a streak (>= 50% progress).
    Runs daily to keep users engaged with active streak promotions.
    """
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.promotions_model import PromotionType, PromotionStatus, StreakProgress, Promotion
        from sqlalchemy import select, and_
        from datetime import datetime, timezone

        # FIX: was `from app.tasks.notification_tasks import send_push_notification_task`
        # That name did not exist — ImportError crashed the entire task.
        # Correct import:
        from app.tasks.notification_tasks import send_push_notification

        async def _notify():
            async with AsyncSessionLocal() as db:
                now = datetime.now(timezone.utc)  # Blueprint §16.4

                result = await db.execute(
                    select(StreakProgress)
                    .join(Promotion, StreakProgress.promotion_id == Promotion.id)
                    .where(
                        and_(
                            StreakProgress.completed.is_(False),
                            StreakProgress.current_count > 0,
                            Promotion.status == PromotionStatus.ACTIVE,
                            Promotion.end_date >= now,
                        )
                    )
                )
                progresses = result.scalars().all()
                notified = 0

                for p in progresses:
                    if p.current_count < (p.target_count // 2):
                        continue

                    remaining = p.target_count - p.current_count
                    promo     = p.promotion

                    title = "You're close to earning your reward! 🏆"
                    body  = (
                        f"Complete {remaining} more "
                        f"{'action' if remaining == 1 else 'actions'} "
                        f"to earn ₦{promo.streak_reward_amount:,.0f} from "
                        f"'{promo.title}'. Offer ends {promo.end_date.strftime('%b %d')}."
                    )

                    # FIX: was send_push_notification_task.delay() — that name didn't exist
                    send_push_notification.delay(
                        user_id=str(p.user_id),
                        title=title,
                        body=body,
                        data={
                            "type":         "streak_milestone",
                            "promotion_id": str(p.promotion_id),
                            "remaining":    remaining,
                        },
                    )
                    notified += 1

                return notified

        count = asyncio.run(_notify())
        logger.info("notify_streak_milestones: sent %d notifications", count)
        return f"Sent {count} streak milestone notifications"
    except Exception:
        logger.exception("Error in notify_streak_milestones task")
        raise