"""
app/tasks/promotions_tasks.py

Celery background tasks for the Promotions feature.

Tasks:
  sync_promotion_statuses   — hourly: SCHEDULED→ACTIVE, ACTIVE→ENDED
  notify_streak_milestones  — daily: notify users near streak completion
"""
import logging
from celery import shared_task

from app.core.database import SessionLocal
from app.crud.promotions_crud import promotion_crud, streak_progress_crud
from app.models.promotions_model import PromotionStatus

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
        import asyncio
        from app.core.database import AsyncSessionLocal

        async def _sync():
            async with AsyncSessionLocal() as async_db:
                count = await promotion_crud.sync_statuses(async_db)
                await async_db.commit()
                return count

        count = asyncio.run(_sync())
        logger.info("Synced %d promotion status records", count)
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
        import asyncio
        from app.core.database import AsyncSessionLocal
        from app.models.promotions_model import PromotionType
        from sqlalchemy import select, and_
        from app.models.promotions_model import StreakProgress, Promotion
        from app.tasks.notification_tasks import send_push_notification_task
        from datetime import datetime, timezone

        async def _notify():
            async with AsyncSessionLocal() as db:
                now = datetime.now(timezone.utc)

                # Find all in-progress streaks in active promotions
                result = await db.execute(
                    select(StreakProgress)
                    .join(
                        Promotion,
                        StreakProgress.promotion_id == Promotion.id,
                    )
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
                    # Only notify if >= 50% done and not yet complete
                    if p.current_count < (p.target_count // 2):
                        continue

                    remaining = p.target_count - p.current_count
                    promo = p.promotion

                    title = "You're close to earning your reward! 🏆"
                    body = (
                        f"Complete {remaining} more "
                        f"{'action' if remaining == 1 else 'actions'} "
                        f"to earn ₦{promo.streak_reward_amount:,.0f} from '{promo.title}'. "
                        f"Offer ends {promo.end_date.strftime('%b %d')}."
                    )

                    send_push_notification_task.delay(
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
        logger.info("Sent %d streak milestone notifications", count)
        return f"Sent {count} streak milestone notifications"
    except Exception:
        logger.exception("Error in notify_streak_milestones task")
        raise