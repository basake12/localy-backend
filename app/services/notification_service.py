"""
notification_service.py

Central dispatch for all app notifications.

Other modules call:
    notification_service.send(db, payload=NotificationPayload(...))

The service:
  1. Resolves which channels are enabled for the user+category.
  2. Persists an in_app row (always, for notification bell).
  3. Enqueues push / email / sms via Celery tasks — never dispatches
     synchronously so the calling request is never stalled by provider latency.
"""

import logging
from typing import Optional, List
from sqlalchemy.orm import Session
from uuid import UUID

from app.crud.notifications_crud import notification_crud, preference_crud, device_token_crud
from app.models.notifications_model import (
    Notification,
    NotificationChannelEnum,
    NotificationStatusEnum,
)
from app.models.user_model import User
from app.schemas.notifications_schema import NotificationPayload, PreferenceToggle
from app.core.exceptions import NotFoundException

# Import Celery tasks at module level — not inside the send() loop.
# Lazy imports inside a request handler re-initialise the Celery app
# on every call, creating a new broker connection and causing the
# "No hostname supplied" warning even when the worker is running.
from app.tasks.notification_tasks import (
    dispatch_push_task,
    dispatch_email_task,
    dispatch_sms_task,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NOTIFICATION SERVICE
# ---------------------------------------------------------------------------

class NotificationService:

    def send(self, db: Session, *, payload: NotificationPayload) -> List[Notification]:
        """
        Fan-out a notification event to all enabled channels.
        Always creates an in_app row for the notification bell.
        Push / email / SMS are dispatched to Celery tasks (non-blocking).
        """
        user = db.query(User).filter(User.id == payload.user_id).first()
        if not user:
            logger.warning("Notification target user %s not found — skipped", payload.user_id)
            return []

        channels = payload.channels or self._resolve_channels(
            db, user_id=payload.user_id, category=payload.category
        )
        # Always include in_app
        if NotificationChannelEnum.IN_APP not in channels:
            channels = list(channels) + [NotificationChannelEnum.IN_APP]

        created: List[Notification] = []

        for channel in channels:
            notif = notification_crud.create(db, data={
                "user_id":    payload.user_id,
                "channel":    channel,
                "category":   payload.category,
                "title":      payload.title,
                "body":       payload.body,
                "action_url": payload.action_url,
                "icon_url":   payload.icon_url,
                "status":     NotificationStatusEnum.PENDING,
            })
            created.append(notif)

            if channel == NotificationChannelEnum.IN_APP:
                # In-app is just the DB row — mark sent immediately
                notification_crud.update_status(
                    db, notification=notif, status=NotificationStatusEnum.SENT
                )

            elif channel == NotificationChannelEnum.PUSH:
                # Enqueue Celery task — non-blocking
                tokens = [dt.token for dt in device_token_crud.get_active_for_user(db, user_id=user.id)]
                if tokens:
                    dispatch_push_task.delay(
                        notification_id=str(notif.id),
                        tokens=tokens,
                        title=payload.title,
                        body=payload.body,
                        action_url=payload.action_url,
                    )

            elif channel == NotificationChannelEnum.EMAIL:
                if user.email:
                    dispatch_email_task.delay(
                        notification_id=str(notif.id),
                        to_email=user.email,
                        subject=payload.title,
                        body=payload.body,
                        action_url=payload.action_url,
                    )

            elif channel == NotificationChannelEnum.SMS:
                if user.phone_number:
                    dispatch_sms_task.delay(
                        notification_id=str(notif.id),
                        phone=user.phone_number,
                        body=payload.body,
                    )

        db.commit()
        return created

    def _resolve_channels(
        self, db: Session, *, user_id: UUID, category: str
    ) -> List[str]:
        """Return channels where the user has not opted out."""
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


# ---------------------------------------------------------------------------
# NOTIFICATION HISTORY SERVICE
# ---------------------------------------------------------------------------

class NotificationHistoryService:

    def list_notifications(
        self, db: Session, *,
        user_id:  UUID,
        category: Optional[str] = None,
        channel:  Optional[str] = None,
        status:   Optional[str] = None,
        skip:  int = 0,
        limit: int = 30,
    ) -> dict:
        notifications = notification_crud.list_for_user(
            db, user_id=user_id, category=category,
            channel=channel, status=status, skip=skip, limit=limit,
        )
        total  = notification_crud.count_for_user(db, user_id=user_id, category=category, status=status)
        unread = notification_crud.unread_count(db, user_id=user_id)
        return {
            "notifications": notifications,
            "total":         total,
            "unread_count":  unread,
            "skip":          skip,
            "limit":         limit,
        }

    def mark_read(self, db: Session, *, user_id: UUID, notification_id: UUID) -> Notification:
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

    def toggle(self, db: Session, *, user_id: UUID, payload: PreferenceToggle) -> dict:
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

    def register(
        self, db: Session, *,
        user_id: UUID, token: str, platform: str,
        device_name: Optional[str] = None,
        app_version: Optional[str] = None,
    ):
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

notification_service     = NotificationService()
notification_history_svc = NotificationHistoryService()
preference_service       = PreferenceService()
device_token_service     = DeviceTokenService()