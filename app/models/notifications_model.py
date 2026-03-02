from sqlalchemy import (
    Column, String, Boolean, Text, Integer,
    ForeignKey, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class NotificationChannelEnum(str, enum.Enum):
    IN_APP  = "in_app"
    PUSH    = "push"       # FCM / APNs
    EMAIL   = "email"
    SMS     = "sms"


class NotificationStatusEnum(str, enum.Enum):
    PENDING   = "pending"
    SENT      = "sent"
    DELIVERED = "delivered"
    READ      = "read"
    FAILED    = "failed"


class NotificationCategoryEnum(str, enum.Enum):
    # Commerce
    BOOKING     = "booking"
    ORDER       = "order"
    DELIVERY    = "delivery"
    PAYMENT     = "payment"
    # Social / trust
    REVIEW      = "review"
    MESSAGE     = "message"
    # Marketing
    PROMOTION   = "promotion"
    # System
    SYSTEM      = "system"
    SECURITY    = "security"       # password change, 2FA
    REMINDER    = "reminder"


# ============================================
# NOTIFICATION MODEL
# ============================================

class Notification(BaseModel):
    """
    One row per (user, channel, event).
    A single logical event (e.g. "order delivered") fans out to multiple
    rows — one per active channel — via NotificationService.send().
    """

    __tablename__ = "notifications"

    # --- Target ---
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Routing ---
    channel  = Column(String(20), nullable=False)   # NotificationChannelEnum
    category = Column(String(30), nullable=False, index=True)  # NotificationCategoryEnum

    # --- Content ---
    title   = Column(String(200), nullable=False)
    body    = Column(Text, nullable=False)
    # Deep-link / action URL (mobile or web)
    action_url = Column(Text, nullable=True)
    # Icon / image URL (for push / in-app)
    icon_url   = Column(Text, nullable=True)

    # --- Delivery state ---
    status     = Column(String(20), default=NotificationStatusEnum.PENDING)
    sent_at    = Column(Text, nullable=True)      # ISO timestamp when dispatched
    delivered_at = Column(Text, nullable=True)
    read_at    = Column(Text, nullable=True)

    # --- Provider meta (FCM message_id, email SES id, SMS sid, etc.) ---
    provider_meta = Column(JSONB, default=dict)

    # --- Relationships ---
    user = relationship("User", back_populates="notifications")

    __table_args__ = (
        Index("ix_notif_user_category_status", "user_id", "category", "status"),
        Index("ix_notif_user_status",          "user_id", "status"),
    )


# ============================================
# USER NOTIFICATION PREFERENCES
# ============================================

class NotificationPreference(BaseModel):
    """
    Per-user, per-category, per-channel opt-in/out.
    If no row exists the global default (enabled) applies.
    """

    __tablename__ = "notification_preferences"

    user_id  = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category = Column(String(30), nullable=False)   # NotificationCategoryEnum
    channel  = Column(String(20), nullable=False)   # NotificationChannelEnum
    enabled  = Column(Boolean, default=True)

    # Relationships
    user = relationship("User", back_populates="notification_preferences")

    __table_args__ = (
        Index("ix_notif_pref_ucc", "user_id", "category", "channel", unique=True),
    )


# ============================================
# DEVICE TOKEN  (push notification registration)
# ============================================

class DeviceToken(BaseModel):
    """
    FCM / APNs token per device.
    A user can have multiple devices.
    """

    __tablename__ = "device_tokens"

    user_id       = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token         = Column(Text, unique=True, nullable=False, index=True)
    platform      = Column(String(20), nullable=False)   # "ios" | "android" | "web"
    device_name   = Column(String(100), nullable=True)
    is_active     = Column(Boolean, default=True)
    app_version   = Column(String(30), nullable=True)

    # Relationships
    user = relationship("User", back_populates="device_tokens")