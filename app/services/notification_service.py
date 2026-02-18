"""
notification_service.py

Central dispatch for all app notifications.

Other modules call:
    notification_service.send(db, payload=NotificationPayload(...))

The service:
  1. Resolves which channels are enabled for the user+category.
  2. Persists an in_app row (always, for notification bell).
  3. Dispatches push / email / sms through provider stubs.

Provider stubs are pluggable — swap real SDK calls in production.
"""

import logging
from typing import Optional, List
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime

from app.crud.notifications import notification_crud, preference_crud, device_token_crud
from app.models.notifications import (
    Notification,
    NotificationChannelEnum,
    NotificationStatusEnum,
    NotificationCategoryEnum,
)
from app.models.user import User
from app.schemas.notifications import NotificationPayload, PreferenceToggle
from app.core.exceptions import NotFoundException

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CHANNEL PROVIDERS  (stubbed — replace with real SDK calls)
# ---------------------------------------------------------------------------

def _send_push(tokens: List[str], title: str, body: str, action_url: str = None) -> dict:
    """
    Send FCM push notification.
    Returns {message_id} on success.
    Production: use google-cloud-messaging or firebase_admin.
    """
    logger.info(f"[PUSH] tokens={tokens} title={title!r}")
    # Stub — in production iterate tokens and call FCM
    return {"message_id": "fcm_stub_id", "tokens_sent": len(tokens)}


def _send_email(to_email: str, subject: str, body: str, action_url: str = None) -> dict:
    """
    Send transactional email.
    Production: use fastapi-mail or boto3 SES.
    """
    logger.info(f"[EMAIL] to={to_email} subject={subject!r}")
    return {"ses_message_id": "ses_stub_id"}


def _send_sms(phone: str, body: str) -> dict:
    """
    Send SMS via Termii (Nigerian provider).
    Production: POST to Termii API.
    """
    logger.info(f"[SMS] phone={phone} body={body!r}")
    return {"termii_message_id": "termii_stub_id"}


# ---------------------------------------------------------------------------
# NOTIFICATION SERVICE
# ---------------------------------------------------------------------------

class NotificationService:

    # ---------- main entry point ----------

    def send(self, db: Session, *, payload: NotificationPayload) -> List[Notification]:
        """
        Fan-out a notification event to all enabled channels for the target user.
        Always creates an in_app row (notification bell).
        """
        user = db.query(User).filter(User.id == payload.user_id).first()
        if not user:
            logger.warning(f"Notification target user {payload.user_id} not found — skipped")
            return []

        # Determine which channels to use
        channels = payload.channels or self._resolve_channels(
            db, user_id=payload.user_id, category=payload.category
        )

        # Always include in_app
        if NotificationChannelEnum.IN_APP not in channels:
            channels.append(NotificationChannelEnum.IN_APP)

        created: List[Notification] = []
        for channel in channels:
            notif = notification_crud.create(db, data={
                "user_id":     payload.user_id,
                "channel":     channel,
                "category":    payload.category,
                "title":       payload.title,
                "body":        payload.body,
                "action_url":  payload.action_url,
                "icon_url":    payload.icon_url,
                "status":      NotificationStatusEnum.PENDING,
            })
            created.append(notif)

            # Dispatch to provider
            try:
                meta = self._dispatch(db, user=user, notif=notif, payload=payload)
                notification_crud.update_status(
                    db, notification=notif,
                    status=NotificationStatusEnum.SENT,
                    meta=meta,
                )
            except Exception as e:
                logger.error(f"[NOTIF] dispatch failed for {channel}: {e}")
                notification_crud.update_status(
                    db, notification=notif,
                    status=NotificationStatusEnum.FAILED,
                    meta={"error": str(e)},
                )

        db.commit()
        return created

    # ---------- helpers ----------

    def _resolve_channels(self, db: Session, *, user_id: UUID, category: str) -> List[str]:
        """Return channels where user has opt-in (or no explicit opt-out)."""
        all_channels = [
            NotificationChannelEnum.IN_APP,
            NotificationChannelEnum.PUSH,
            NotificationChannelEnum.EMAIL,
            NotificationChannelEnum.SMS,
        ]
        return [
            ch for ch in all_channels
            if preference_crud.is_enabled(db, user_id=user_id, category=category, channel=ch)
        ]

    def _dispatch(self, db: Session, *, user: User, notif: Notification, payload: NotificationPayload) -> dict:
        """Route to the correct provider based on channel."""
        channel = notif.channel

        if channel == NotificationChannelEnum.IN_APP:
            # In-app is just the DB row — no external call
            return {}

        if channel == NotificationChannelEnum.PUSH:
            tokens = [dt.token for dt in device_token_crud.get_active_for_user(db, user_id=user.id)]
            if not tokens:
                logger.info(f"[PUSH] No active device tokens for user {user.id}")
                return {"skipped": "no_tokens"}
            return _send_push(tokens, payload.title, payload.body, payload.action_url)

        if channel == NotificationChannelEnum.EMAIL:
            if not user.email:
                return {"skipped": "no_email"}
            return _send_email(user.email, payload.title, payload.body, payload.action_url)

        if channel == NotificationChannelEnum.SMS:
            if not user.phone:
                return {"skipped": "no_phone"}
            return _send_sms(user.phone, payload.body)

        return {"skipped": f"unknown_channel_{channel}"}


# ---------------------------------------------------------------------------
# NOTIFICATION HISTORY SERVICE  (what the API router uses)
# ---------------------------------------------------------------------------

class NotificationHistoryService:

    def list_notifications(
        self, db: Session, *,
        user_id: UUID,
        category: Optional[str] = None,
        channel:  Optional[str] = None,
        status:   Optional[str] = None,
        skip: int = 0,
        limit: int = 30,
    ) -> dict:
        notifications = notification_crud.list_for_user(
            db, user_id=user_id, category=category,
            channel=channel, status=status, skip=skip, limit=limit,
        )
        total   = notification_crud.count_for_user(db, user_id=user_id, category=category, status=status)
        unread  = notification_crud.unread_count(db, user_id=user_id)
        return {
            "notifications": notifications,
            "total":         total,
            "unread_count":  unread,
            "skip":          skip,
            "limit":         limit,
        }

    def mark_read(self, db: Session, *, user_id: UUID, notification_id: UUID):
        notif = notification_crud.mark_read(db, notification_id=notification_id, user_id=user_id)
        if not notif:
            raise NotFoundException("Notification")
        db.commit()
        return notif

    def mark_all_read(self, db: Session, *, user_id: UUID, category: Optional[str] = None) -> int:
        count = notification_crud.mark_all_read(db, user_id=user_id, category=category)
        db.commit()
        return count


# ---------------------------------------------------------------------------
# PREFERENCE SERVICE
# ---------------------------------------------------------------------------

class PreferenceService:

    def get_preferences(self, db: Session, *, user_id: UUID) -> list:
        prefs = preference_crud.get_all_for_user(db, user_id=user_id)
        return [
            {"category": p.category, "channel": p.channel, "enabled": p.enabled}
            for p in prefs
        ]

    def toggle(self, db: Session, *, user_id: UUID, payload: PreferenceToggle):
        pref = preference_crud.upsert(
            db,
            user_id=user_id,
            category=payload.category,
            channel=payload.channel,
            enabled=payload.enabled,
        )
        db.commit()
        return {"category": pref.category, "channel": pref.channel, "enabled": pref.enabled}


# ---------------------------------------------------------------------------
# DEVICE TOKEN SERVICE
# ---------------------------------------------------------------------------

class DeviceTokenService:

    def register(self, db: Session, *, user_id: UUID, token: str, platform: str,
                 device_name: str = None, app_version: str = None):
        dt = device_token_crud.upsert(
            db, user_id=user_id, token=token, platform=platform,
            device_name=device_name, app_version=app_version,
        )
        db.commit()
        db.refresh(dt)
        return dt

    def unregister(self, db: Session, *, token: str) -> bool:
        removed = device_token_crud.deactivate(db, token=token)
        if removed:
            db.commit()
        return removed


# ---------------------------------------------------------------------------
# SINGLETONS
# ---------------------------------------------------------------------------

notification_service      = NotificationService()
notification_history_svc  = NotificationHistoryService()
preference_service        = PreferenceService()
device_token_service      = DeviceTokenService()