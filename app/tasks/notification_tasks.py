"""
notification_tasks.py

Celery tasks for dispatching notifications to external providers.

These tasks are called by NotificationService.send() — they run
asynchronously so the originating HTTP request is never blocked by
provider latency or failures.
"""
import logging
from uuid import UUID

from app.tasks.celery_app import celery
from app.core.database import SessionLocal
# Correct import path: notifications_crud (plural), not notification_crud
from app.crud.notifications_crud import notification_crud
from app.models.notifications_model import NotificationStatusEnum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PUSH — Firebase Cloud Messaging
# ---------------------------------------------------------------------------

@celery.task(
    name="tasks.dispatch_push",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def dispatch_push_task(
    self,
    notification_id: str,
    tokens: list[str],
    title: str,
    body: str,
    action_url: str = None,
) -> str:
    """
    Send FCM push notification to one or more device tokens.
    Production: replace stub with firebase_admin.messaging or google-auth POST.
    """
    db = SessionLocal()
    try:
        notif = notification_crud.get(db, notification_id=UUID(notification_id))
        if not notif:
            return f"Notification {notification_id} not found — skipped"

        # --- FCM stub ---
        # from firebase_admin import messaging
        # messages = [
        #     messaging.Message(
        #         notification=messaging.Notification(title=title, body=body),
        #         data={"route": action_url or ""},
        #         token=token,
        #     )
        #     for token in tokens
        # ]
        # batch_response = messaging.send_each(messages)
        # failed = [r for r in batch_response.responses if not r.success]
        # meta = {"sent": batch_response.success_count, "failed": len(failed)}

        meta = {"message_id": "fcm_stub", "tokens_sent": len(tokens)}
        logger.info("[PUSH] Sent to %d tokens for notification %s", len(tokens), notification_id)

        notification_crud.update_status(
            db, notification=notif, status=NotificationStatusEnum.SENT, meta=meta
        )
        db.commit()
        return f"Push sent to {len(tokens)} devices"

    except Exception as exc:
        db.rollback()
        logger.error("[PUSH] Failed for notification %s: %s", notification_id, exc)
        try:
            notif = notification_crud.get(db, notification_id=UUID(notification_id))
            if notif:
                notification_crud.update_status(
                    db, notification=notif,
                    status=NotificationStatusEnum.FAILED,
                    meta={"error": str(exc)},
                )
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# EMAIL — Transactional (Mailgun / SES / Resend)
# ---------------------------------------------------------------------------

@celery.task(
    name="tasks.dispatch_email",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def dispatch_email_task(
    self,
    notification_id: str,
    to_email: str,
    subject: str,
    body: str,
    action_url: str = None,
) -> str:
    """
    Send transactional email.
    Production: replace stub with boto3 SES, Resend, or Mailgun.
    """
    db = SessionLocal()
    try:
        notif = notification_crud.get(db, notification_id=UUID(notification_id))
        if not notif:
            return f"Notification {notification_id} not found — skipped"

        # --- Email stub ---
        # import boto3
        # client = boto3.client("ses", region_name="eu-west-1")
        # client.send_email(
        #     Source="no-reply@localy.app",
        #     Destination={"ToAddresses": [to_email]},
        #     Message={
        #         "Subject": {"Data": subject},
        #         "Body": {"Html": {"Data": f"<p>{body}</p>"}},
        #     },
        # )

        meta = {"provider": "ses_stub", "to": to_email}
        logger.info("[EMAIL] Sent to %s for notification %s", to_email, notification_id)

        notification_crud.update_status(
            db, notification=notif, status=NotificationStatusEnum.SENT, meta=meta
        )
        db.commit()
        return f"Email sent to {to_email}"

    except Exception as exc:
        db.rollback()
        logger.error("[EMAIL] Failed for notification %s: %s", notification_id, exc)
        raise self.retry(exc=exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# SMS — Termii (Nigerian provider)
# ---------------------------------------------------------------------------

@celery.task(
    name="tasks.dispatch_sms",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def dispatch_sms_task(
    self,
    notification_id: str,
    phone: str,
    body: str,
) -> str:
    """
    Send SMS via Termii.
    Production: POST to https://api.ng.termii.com/api/sms/send
    """
    db = SessionLocal()
    try:
        notif = notification_crud.get(db, notification_id=UUID(notification_id))
        if not notif:
            return f"Notification {notification_id} not found — skipped"

        # --- Termii stub ---
        # import httpx
        # httpx.post("https://api.ng.termii.com/api/sms/send", json={
        #     "to": phone, "from": "Localy",
        #     "sms": body, "type": "plain",
        #     "channel": "generic", "api_key": settings.TERMII_API_KEY,
        # })

        meta = {"provider": "termii_stub", "phone": phone}
        logger.info("[SMS] Sent to %s for notification %s", phone, notification_id)

        notification_crud.update_status(
            db, notification=notif, status=NotificationStatusEnum.SENT, meta=meta
        )
        db.commit()
        return f"SMS sent to {phone}"

    except Exception as exc:
        db.rollback()
        logger.error("[SMS] Failed for notification %s: %s", notification_id, exc)
        raise self.retry(exc=exc)
    finally:
        db.close()