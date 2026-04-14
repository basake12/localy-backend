import logging
from datetime import datetime, timedelta

from celery import shared_task
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.services.subscription_service import subscription_service
from app.models.subscription_model import Subscription
from app.tasks.email_tasks import send_email_async

logger = logging.getLogger(__name__)


@shared_task(name="tasks.auto_renew_subscriptions")
def auto_renew_subscriptions() -> str:
    """Auto-renew subscriptions expiring within 24 hours. Runs daily at 12:30 AM."""
    db = SessionLocal()
    try:
        count = subscription_service.auto_renew_subscriptions(db)
        logger.info("Auto-renewed %d subscriptions", count)
        return f"Auto-renewed {count} subscriptions"
    except Exception:
        logger.exception("Error in auto_renew_subscriptions task")
        raise
    finally:
        db.close()


@shared_task(name="tasks.expire_subscriptions")
def expire_subscriptions() -> str:
    """
    Mark past-expiry subscriptions as expired and downgrade business tiers to free.
    Runs every hour.
    """
    db = SessionLocal()
    try:
        count = subscription_service.check_and_expire_subscriptions(db)
        logger.info("Expired %d subscriptions and synced business tiers", count)
        return f"Expired {count} subscriptions"
    except Exception:
        logger.exception("Error in expire_subscriptions task")
        raise
    finally:
        db.close()


@shared_task(name="tasks.send_expiry_reminders")
def send_expiry_reminders() -> str:
    """Send subscription expiry reminder emails (3 days before expiry)."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        cutoff = now + timedelta(days=3)

        # Single query — eagerly load user and plan to avoid N+1
        subscriptions = (
            db.query(Subscription)
            .options(
                joinedload(Subscription.user),
                joinedload(Subscription.plan),
            )
            .filter(
                Subscription.status == "active",
                Subscription.auto_renew.is_(True),
                Subscription.expires_at <= cutoff,
                Subscription.expires_at > now,
            )
            .all()
        )

        sent = 0
        for sub in subscriptions:
            if not sub.user or not sub.user.email:
                logger.warning(
                    "Skipping expiry reminder for subscription %s: user/email missing",
                    sub.id,
                )
                continue

            plan_name = sub.plan.name if sub.plan else "your plan"
            expiry_str = sub.expires_at.strftime("%B %d, %Y")

            send_email_async.delay(
                sub.user.email,
                "Your Localy Subscription is Expiring Soon",
                (
                    f"Hi {sub.user.full_name or 'there'},\n\n"
                    f"Your {plan_name} subscription expires on {expiry_str}. "
                    "Auto-renewal is enabled — please ensure your wallet has sufficient "
                    "funds to avoid interruption.\n\n"
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