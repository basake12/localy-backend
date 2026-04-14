"""
app/tasks/cleanup_tasks.py

Periodic housekeeping tasks — registered in celery_app.beat_schedule.

Import paths follow the project's naming convention:
  app/crud/subscription.py   → from app.crud.subscription import subscription_crud
  app/crud/reels.py          → from app.crud.reels import reel_crud
  app/models/notification.py → from app.models.notification import Notification
  app/models/user.py         → from app.models.user import User
"""
from datetime import datetime, timedelta

from celery import shared_task
from sqlalchemy import update

from app.core.database import SessionLocal


# ─────────────────────────────────────────────
# SUBSCRIPTIONS
# ─────────────────────────────────────────────

@shared_task(name="tasks.cleanup_expired_subscriptions")
def cleanup_expired_subscriptions():
    """Mark expired subscriptions as inactive. Runs daily at 03:00 WAT."""
    db = SessionLocal()
    try:
        # FIX: was app.crud.subscription_crud — correct module is subscription
        from app.crud.subscription import subscription_crud
        count = subscription_crud.mark_expired(db)
        db.commit()
        return f"Marked {count} subscriptions as expired"
    except Exception as exc:
        db.rollback()
        return f"Error cleaning subscriptions: {exc}"
    finally:
        db.close()


# ─────────────────────────────────────────────
# STORIES
# ─────────────────────────────────────────────

@shared_task(name="tasks.expire_old_stories")
def expire_old_stories():
    """
    Mark stories whose expires_at has passed as inactive.
    Blueprint requirement: 24-hour ephemeral content.
    Runs every 30 minutes via Celery Beat.
    """
    db = SessionLocal()
    try:
        # FIX: was app.crud.stories_crud — correct module is reels
        from app.crud.reels import reel_crud
        count = reel_crud.expire_old_stories(db)
        db.commit()
        return f"Expired {count} stories"
    except Exception as exc:
        db.rollback()
        return f"Error expiring stories: {exc}"
    finally:
        db.close()


# ─────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────

@shared_task(name="tasks.cleanup_old_notifications")
def cleanup_old_notifications(days: int = 90):
    """Delete read notifications older than N days. Runs weekly."""
    db = SessionLocal()
    try:
        # FIX: was app.models.notification_model — correct module is notification
        from app.models.notification import Notification
        cutoff = datetime.utcnow() - timedelta(days=days)
        count = (
            db.query(Notification)
            .filter(
                Notification.created_at < cutoff,
                Notification.is_read.is_(True),
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return f"Deleted {count} notifications older than {days} days"
    except Exception as exc:
        db.rollback()
        return f"Error cleaning notifications: {exc}"
    finally:
        db.close()


# ─────────────────────────────────────────────
# OTPS
# ─────────────────────────────────────────────

@shared_task(name="tasks.cleanup_expired_otps")
def cleanup_expired_otps():
    """Null out expired OTP codes from the users table. Runs every 15 min."""
    db = SessionLocal()
    try:
        # FIX: was app.models.user_model — correct module is user
        from app.models.user import User
        result = db.execute(
            update(User)
            .where(
                User.otp_expires_at < datetime.utcnow(),
                User.otp_code.isnot(None),
            )
            .values(otp_code=None, otp_expires_at=None)
        )
        db.commit()
        return f"Cleared OTP codes for {result.rowcount} users"
    except Exception as exc:
        db.rollback()
        return f"Error clearing OTPs: {exc}"
    finally:
        db.close()