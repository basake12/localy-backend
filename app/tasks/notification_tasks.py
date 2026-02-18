from celery import shared_task
from app.core.database import SessionLocal
from app.crud.notification import notification_crud


@shared_task(name="tasks.create_notification")
def create_notification_async(
    user_id: str,
    notification_type: str,
    title: str,
    message: str,
    data: dict = None
):
    """Create notification asynchronously."""
    db = SessionLocal()
    try:
        notification = notification_crud.create_notification(
            db,
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            message=message,
            data=data
        )
        return f"Notification created for user {user_id}"
    except Exception as e:
        return f"Error creating notification: {str(e)}"
    finally:
        db.close()


@shared_task(name="tasks.send_push_notification")
def send_push_notification_async(user_id: str, title: str, message: str, data: dict = None):
    """Send push notification via FCM (Firebase Cloud Messaging)."""
    # TODO: Implement FCM push notification
    # This would use Firebase Admin SDK or similar
    pass