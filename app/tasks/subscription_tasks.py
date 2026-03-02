from celery import shared_task
from app.core.database import SessionLocal
from app.services.subscription_service import subscription_service
from app.crud.subscription_crud import subscription_crud
from app.tasks.email import send_email_async


@shared_task(name="tasks.auto_renew_subscriptions")
def auto_renew_subscriptions():
    """Auto-renew subscriptions expiring soon. Runs daily at 12:30 AM."""
    db = SessionLocal()
    try:
        count = subscription_service.auto_renew_subscriptions(db)
        return f"Auto-renewed {count} subscriptions"
    except Exception as e:
        return f"Error auto-renewing subscriptions: {str(e)}"
    finally:
        db.close()


@shared_task(name="tasks.send_expiry_reminders")
def send_expiry_reminders():
    """Send subscription expiry reminders."""
    db = SessionLocal()
    try:

        # Get subscriptions expiring in 3 days
        expiring = subscription_crud.get_expiring_soon(db, days=3)

        for subscription in expiring:
            # Send email reminder
            send_email_async.delay(
                subscription.user.email,
                "Subscription Expiring Soon",
                f"Your subscription expires on {subscription.end_date}"
            )

        return f"Sent {len(expiring)} expiry reminders"
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        db.close()