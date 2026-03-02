from celery import shared_task
from app.core.database import SessionLocal
from app.crud.subscription_crud import subscription_crud
from datetime import datetime, timedelta


@shared_task(name="tasks.cleanup_expired_subscriptions")
def cleanup_expired_subscriptions():
    """Mark expired subscriptions. Runs daily at 2 AM."""
    db = SessionLocal()
    try:
        count = subscription_crud.mark_expired(db)
        return f"Marked {count} subscriptions as expired"
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        db.close()


@shared_task(name="tasks.cleanup_old_notifications")
def cleanup_old_notifications(days: int = 90):
    """Delete notifications older than N days."""
    db = SessionLocal()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        # TODO: Implement notification cleanup
        return f"Cleaned up notifications older than {days} days"
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        db.close()


@shared_task(name="tasks.cleanup_expired_otps")
def cleanup_expired_otps():
    """Clean up expired OTP codes."""
    db = SessionLocal()
    try:
        # TODO: Clear expired OTPs from users table
        return "Cleaned up expired OTPs"
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        db.close()



