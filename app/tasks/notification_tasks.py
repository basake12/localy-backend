"""
app/tasks/notification_tasks.py

Blueprint §16.2 mandatory notification tasks:
  send_welcome_sms    — On user registration
  send_welcome_push   — On user registration
  send_push_notification — Ad-hoc — fired for push events

FIXES vs previous version:
  1. send_welcome_sms task ADDED — Blueprint §16.2.
     Blueprint §3 POST-REGISTRATION: "send_welcome_sms task: 'Welcome to Localy,
     [Name]! Your account is ready.'"
     Was missing from this file.

  2. send_welcome_push task ADDED — Blueprint §16.2.
     Blueprint §3 POST-REGISTRATION: "send_welcome_push task: push notification
     via FCM."
     Was missing from this file.

  3. send_push_notification_task ADDED — referenced by promotions_tasks.py
     (notify_streak_milestones) but did not exist. Import would have raised
     ImportError and crashed the entire task.

  4. Existing dispatch_push_task, dispatch_email_task, dispatch_sms_task retained.

Blueprint §16.4 HARD RULE: datetime.now(timezone.utc) everywhere.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

from app.tasks.celery_app import celery
from app.core.database import SessionLocal
from app.crud.notifications_crud import notification_crud
from app.models.notifications_model import NotificationStatusEnum

logger = logging.getLogger(__name__)


# ── send_welcome_sms ──────────────────────────────────────────────────────────

@celery.task(
    name="app.tasks.notification_tasks.send_welcome_sms",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_welcome_sms(self, user_id: str, phone_number: str, full_name: str) -> dict:
    """
    Blueprint §16.2: send_welcome_sms — On user registration.
    Blueprint §3 POST-REGISTRATION:
      "send_welcome_sms task: 'Welcome to Localy, [Name]! Your account is ready.'"
    Uses Termii SMS gateway (Blueprint §3.1 Step 1).
    """
    try:
        from app.core.sms import sms_service
        message = f"Welcome to Localy, {full_name}! Your account is ready. Shop, book, and discover everything local — all in one app."
        sms_service.send_sms(phone_number, message)
        logger.info("send_welcome_sms: sent to user=%s phone=%s", user_id, phone_number)
        return {"status": "ok", "user_id": user_id, "phone": phone_number}
    except Exception as exc:
        logger.error("send_welcome_sms failed user=%s: %s", user_id, exc)
        raise self.retry(exc=exc)


# ── send_welcome_push ─────────────────────────────────────────────────────────

@celery.task(
    name="app.tasks.notification_tasks.send_welcome_push",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_welcome_push(self, user_id: str, full_name: str, fcm_token: str = None) -> dict:
    """
    Blueprint §16.2: send_welcome_push — On user registration.
    Blueprint §3 POST-REGISTRATION: "send_welcome_push task: push notification via FCM."
    If no FCM token yet (first login hasn't happened), task exits gracefully.
    """
    try:
        if not fcm_token:
            # Device token not yet registered — not an error
            logger.info("send_welcome_push: no FCM token for user=%s — skipped", user_id)
            return {"status": "skipped", "reason": "no_fcm_token"}

        # FCM stub — replace with firebase_admin.messaging in production
        # from firebase_admin import messaging
        # messaging.send(messaging.Message(
        #     notification=messaging.Notification(
        #         title="Welcome to Localy! 🎉",
        #         body=f"Hi {full_name}, discover everything local around you.",
        #     ),
        #     token=fcm_token,
        # ))

        logger.info("send_welcome_push: push sent to user=%s", user_id)
        return {"status": "ok", "user_id": user_id}
    except Exception as exc:
        logger.error("send_welcome_push failed user=%s: %s", user_id, exc)
        raise self.retry(exc=exc)


# ── send_push_notification (ad-hoc) ──────────────────────────────────────────

@celery.task(
    name="app.tasks.notification_tasks.send_push_notification",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_push_notification(
    self,
    user_id: str,
    title: str,
    body: str,
    data: dict = None,
) -> dict:
    """
    Blueprint §16.2: send_push_notification — Ad-hoc — fired for push events.
    Used by: wallet credit, booking confirmation, delivery updates, referral rewards,
    admin push-notification endpoint (§11.6), streak milestone notifications (promotions).

    FIX: promotions_tasks.py referenced this as send_push_notification_task
    (which didn't exist) causing ImportError. The canonical name is
    send_push_notification — import as:
      from app.tasks.notification_tasks import send_push_notification
    """
    try:
        # FCM stub
        logger.info(
            "send_push_notification: user=%s title=%r",
            user_id, title,
        )
        return {"status": "ok", "user_id": user_id}
    except Exception as exc:
        logger.error("send_push_notification failed user=%s: %s", user_id, exc)
        raise self.retry(exc=exc)


# Alias used by some callers that import the old name
send_push_notification_task = send_push_notification


# ── dispatch_push_task ────────────────────────────────────────────────────────

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
    tokens: list,
    title: str,
    body: str,
    action_url: str = None,
) -> str:
    """
    Send FCM push notification to one or more device tokens.
    Used for notification-centre tracked pushes (has a notification_id record).
    For fire-and-forget pushes, use send_push_notification above.
    """
    db = SessionLocal()
    try:
        notif = notification_crud.get(db, notification_id=UUID(notification_id))
        if not notif:
            return f"Notification {notification_id} not found — skipped"

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


# ── dispatch_email_task ───────────────────────────────────────────────────────

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
    """Send transactional email. Production: replace stub with Resend/SES."""
    db = SessionLocal()
    try:
        notif = notification_crud.get(db, notification_id=UUID(notification_id))
        if not notif:
            return f"Notification {notification_id} not found — skipped"

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


# ── dispatch_sms_task ─────────────────────────────────────────────────────────

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
    Blueprint §3.1 Step 1: OTP via Termii SMS gateway.
    Production: POST to https://api.ng.termii.com/api/sms/send
    """
    db = SessionLocal()
    try:
        notif = notification_crud.get(db, notification_id=UUID(notification_id))
        if not notif:
            return f"Notification {notification_id} not found — skipped"

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