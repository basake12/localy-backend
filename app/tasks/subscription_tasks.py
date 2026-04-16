"""
app/tasks/subscription_tasks.py

FIXES vs previous version:
  1.  [HARD RULE §16.4] datetime.utcnow() → datetime.now(timezone.utc).

  2.  Task name 'tasks.auto_renew_subscriptions' renamed to match
      Blueprint §16.2 task: renew_subscription.

  3.  downgrade_expired_subscriptions task added.
      Blueprint §8.1: "Failed payment: 7-day grace period → auto-downgrade
      to Free on day 8."

  4.  cleanup_expired_subscriptions task added for Celery beat schedule.

Start: celery -A app.tasks.celery_app.celery worker --loglevel=info
"""
import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.services.subscription_service import subscription_service
from app.models.subscription_model import Subscription

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


@shared_task(name="app.tasks.subscription_tasks.renew_subscription")
def renew_subscription() -> str:
    """
    Auto-renew subscriptions expiring within 24 hours.
    Blueprint §16.2: "Daily — check expiring subscriptions."
    """
    db = SessionLocal()
    try:
        count = subscription_service.auto_renew_subscriptions(db)
        logger.info("Auto-renewed %d subscriptions", count)
        return f"Renewed {count} subscriptions"
    except Exception:
        logger.exception("Error in renew_subscription task")
        raise
    finally:
        db.close()


@shared_task(name="app.tasks.subscription_tasks.cleanup_expired_subscriptions")
def cleanup_expired_subscriptions() -> str:
    """
    Mark past-expiry subscriptions as expired.
    Sets 7-day grace period state on Business.subscription_status.
    Blueprint §8.1.
    """
    db = SessionLocal()
    try:
        count = subscription_service.check_and_expire_subscriptions(db)
        logger.info("Marked %d subscriptions as expired (grace period started)", count)
        return f"Expired {count} subscriptions"
    except Exception:
        logger.exception("Error in cleanup_expired_subscriptions task")
        raise
    finally:
        db.close()


@shared_task(name="app.tasks.subscription_tasks.downgrade_expired_subscriptions")
def downgrade_expired_subscriptions() -> str:
    """
    Downgrade businesses past 7-day grace period to Free tier.
    Blueprint §8.1: "Failed payment: 7-day grace period → auto-downgrade
    to Free on day 8."
    Run daily alongside cleanup_expired_subscriptions.
    """
    db = SessionLocal()
    try:
        count = subscription_service.downgrade_expired_subscriptions(db)
        logger.info("Downgraded %d businesses to Free tier (past grace period)", count)
        return f"Downgraded {count} businesses"
    except Exception:
        logger.exception("Error in downgrade_expired_subscriptions task")
        raise
    finally:
        db.close()


@shared_task(name="app.tasks.subscription_tasks.send_expiry_reminders")
def send_expiry_reminders() -> str:
    """
    Send subscription expiry reminder emails (3 days before expiry).
    """
    db = SessionLocal()
    try:
        now    = _utcnow()   # Blueprint §16.4 HARD RULE
        cutoff = now + timedelta(days=3)

        subscriptions = (
            db.query(Subscription)
            .options(
                joinedload(Subscription.user),
                joinedload(Subscription.plan),
            )
            .filter(
                Subscription.status     == "active",
                Subscription.auto_renew == True,
                Subscription.expires_at <= cutoff,
                Subscription.expires_at >  now,
            )
            .all()
        )

        from app.tasks.email_tasks import send_email_async

        sent = 0
        for sub in subscriptions:
            if not sub.user or not sub.user.email:
                continue

            plan_name  = sub.plan.name if sub.plan else "your plan"
            expiry_str = sub.expires_at.strftime("%B %d, %Y")
            name       = sub.user.full_name or "there"

            send_email_async.delay(
                sub.user.email,
                "Your Localy Subscription is Expiring Soon",
                (
                    f"Hi {name},\n\n"
                    f"Your {plan_name} subscription expires on {expiry_str}. "
                    "Auto-renewal is enabled — ensure your wallet has sufficient "
                    "funds to avoid service interruption.\n\n"
                    "— The Localy Team"
                ),
            )
            sent += 1

        logger.info("Sent %d expiry reminder emails", sent)
        return f"Sent {sent} expiry reminders"

    except Exception:
        logger.exception("Error in send_expiry_reminders task")
        raise
    finally:
        db.close()