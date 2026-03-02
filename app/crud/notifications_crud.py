from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func
from uuid import UUID
from datetime import datetime

from app.models.notifications_model import (
    Notification,
    NotificationPreference,
    DeviceToken,
    NotificationStatusEnum,
    NotificationChannelEnum,
)


# ============================================
# NOTIFICATION CRUD
# ============================================

class CRUDNotification:

    def create(self, db: Session, *, data: dict) -> Notification:
        notif = Notification(**data)
        db.add(notif)
        db.flush()
        return notif

    def get(self, db: Session, *, notification_id: UUID) -> Optional[Notification]:
        return db.query(Notification).filter(Notification.id == notification_id).first()

    def list_for_user(
        self, db: Session, *,
        user_id: UUID,
        category: Optional[str] = None,
        channel:  Optional[str] = None,
        status:   Optional[str] = None,
        skip: int = 0,
        limit: int = 30,
    ) -> List[Notification]:
        q = db.query(Notification).filter(Notification.user_id == user_id)
        if category:
            q = q.filter(Notification.category == category)
        if channel:
            q = q.filter(Notification.channel == channel)
        if status:
            q = q.filter(Notification.status == status)
        return q.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()

    def count_for_user(
        self, db: Session, *,
        user_id: UUID,
        category: Optional[str] = None,
        status:   Optional[str] = None,
    ) -> int:
        q = db.query(func.count(Notification.id)).filter(Notification.user_id == user_id)
        if category:
            q = q.filter(Notification.category == category)
        if status:
            q = q.filter(Notification.status == status)
        return q.scalar() or 0

    def unread_count(self, db: Session, *, user_id: UUID) -> int:
        """In-app unread = status IN (pending, sent, delivered)"""
        return (
            db.query(func.count(Notification.id))
            .filter(
                Notification.user_id == user_id,
                Notification.channel == NotificationChannelEnum.IN_APP,
                Notification.status.in_([
                    NotificationStatusEnum.PENDING,
                    NotificationStatusEnum.SENT,
                    NotificationStatusEnum.DELIVERED,
                ]),
            )
            .scalar()
        ) or 0

    def mark_read(self, db: Session, *, notification_id: UUID, user_id: UUID) -> Optional[Notification]:
        notif = (
            db.query(Notification)
            .filter(Notification.id == notification_id, Notification.user_id == user_id)
            .first()
        )
        if notif:
            notif.status  = NotificationStatusEnum.READ
            notif.read_at = datetime.utcnow().isoformat()
            db.flush()
        return notif

    def mark_all_read(self, db: Session, *, user_id: UUID, category: Optional[str] = None) -> int:
        q = (
            db.query(Notification)
            .filter(
                Notification.user_id == user_id,
                Notification.channel == NotificationChannelEnum.IN_APP,
                Notification.status.in_([
                    NotificationStatusEnum.PENDING,
                    NotificationStatusEnum.SENT,
                    NotificationStatusEnum.DELIVERED,
                ]),
            )
        )
        if category:
            q = q.filter(Notification.category == category)

        now = datetime.utcnow().isoformat()
        updated = q.update({"status": NotificationStatusEnum.READ, "read_at": now}, synchronize_session="fetch")
        db.flush()
        return updated

    def update_status(self, db: Session, *, notification: Notification, status: str, meta: dict = None):
        notification.status = status
        if status == NotificationStatusEnum.SENT:
            notification.sent_at = datetime.utcnow().isoformat()
        if meta:
            notification.provider_meta = meta
        db.flush()


# ============================================
# PREFERENCE CRUD
# ============================================

class CRUDNotificationPreference:

    def get_all_for_user(self, db: Session, *, user_id: UUID) -> List[NotificationPreference]:
        return (
            db.query(NotificationPreference)
            .filter(NotificationPreference.user_id == user_id)
            .all()
        )

    def get(self, db: Session, *, user_id: UUID, category: str, channel: str) -> Optional[NotificationPreference]:
        return (
            db.query(NotificationPreference)
            .filter(
                NotificationPreference.user_id  == user_id,
                NotificationPreference.category == category,
                NotificationPreference.channel  == channel,
            )
            .first()
        )

    def upsert(self, db: Session, *, user_id: UUID, category: str, channel: str, enabled: bool) -> NotificationPreference:
        pref = self.get(db, user_id=user_id, category=category, channel=channel)
        if pref:
            pref.enabled = enabled
        else:
            pref = NotificationPreference(
                user_id=user_id, category=category, channel=channel, enabled=enabled
            )
            db.add(pref)
        db.flush()
        db.refresh(pref)
        return pref

    def is_enabled(self, db: Session, *, user_id: UUID, category: str, channel: str) -> bool:
        """Returns the user's opt-in status; defaults to True if no explicit preference."""
        pref = self.get(db, user_id=user_id, category=category, channel=channel)
        return pref.enabled if pref else True


# ============================================
# DEVICE TOKEN CRUD
# ============================================

class CRUDDeviceToken:

    def get_by_token(self, db: Session, *, token: str) -> Optional[DeviceToken]:
        return db.query(DeviceToken).filter(DeviceToken.token == token).first()

    def get_active_for_user(self, db: Session, *, user_id: UUID) -> List[DeviceToken]:
        return (
            db.query(DeviceToken)
            .filter(DeviceToken.user_id == user_id, DeviceToken.is_active.is_(True))
            .all()
        )

    def upsert(self, db: Session, *, user_id: UUID, token: str, platform: str,
               device_name: str = None, app_version: str = None) -> DeviceToken:
        existing = self.get_by_token(db, token=token)
        if existing:
            existing.user_id     = user_id
            existing.is_active   = True
            existing.device_name = device_name or existing.device_name
            existing.app_version = app_version or existing.app_version
            db.flush()
            db.refresh(existing)
            return existing

        dt = DeviceToken(
            user_id=user_id, token=token, platform=platform,
            device_name=device_name, app_version=app_version, is_active=True,
        )
        db.add(dt)
        db.flush()
        db.refresh(dt)
        return dt

    def deactivate(self, db: Session, *, token: str) -> bool:
        dt = self.get_by_token(db, token=token)
        if dt:
            dt.is_active = False
            db.flush()
            return True
        return False


# ============================================
# SINGLETONS
# ============================================

notification_crud = CRUDNotification()
preference_crud   = CRUDNotificationPreference()
device_token_crud = CRUDDeviceToken()