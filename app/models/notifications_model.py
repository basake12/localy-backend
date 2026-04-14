from sqlalchemy import (
    Column, String, Boolean, Text,
    ForeignKey, Index, DateTime
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
from datetime import datetime, timezone

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class NotificationChannelEnum(str, enum.Enum):
    IN_APP = "in_app"
    PUSH   = "push"    # FCM / APNs
    EMAIL  = "email"
    SMS    = "sms"


class NotificationStatusEnum(str, enum.Enum):
    PENDING   = "pending"
    SENT      = "sent"
    DELIVERED = "delivered"
    READ      = "read"
    FAILED    = "failed"


class NotificationCategoryEnum(str, enum.Enum):
    # Commerce
    BOOKING  = "booking"
    ORDER    = "order"
    DELIVERY = "delivery"
    PAYMENT  = "payment"
    # Social / trust
    REVIEW   = "review"
    MESSAGE  = "message"
    # Marketing
    PROMOTION = "promotion"
    # System
    SYSTEM   = "system"
    SECURITY = "security"    # password change, 2FA
    REMINDER = "reminder"


# ============================================
# NOTIFICATION MODEL
# ============================================

class Notification(BaseModel):
    """
    One row per (user, channel, event).
    A single logical event fans out to multiple rows — one per active
    channel — via NotificationService.send().
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
    channel  = Column(String(20), nullable=False)                  # NotificationChannelEnum
    category = Column(String(30), nullable=False, index=True)      # NotificationCategoryEnum

    # --- Content ---
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    action_url = Column(Text, nullable=True)   # deep-link for mobile or web
    icon_url   = Column(Text, nullable=True)   # icon / image URL for push / in-app

    # --- Delivery state ---
    status       = Column(String(20), default=NotificationStatusEnum.PENDING, nullable=False)
    # Use proper DateTime columns so PostgreSQL can index and sort efficiently
    sent_at      = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    read_at      = Column(DateTime(timezone=True), nullable=True)

    # --- Provider meta (FCM message_id, SES id, Termii sid, etc.) ---
    provider_meta = Column(JSONB, default=dict)

    # --- Relationships ---
    user = relationship("User", back_populates="notifications")

    __table_args__ = (
        Index("ix_notif_user_category_status", "user_id", "category", "status"),
        Index("ix_notif_user_status",          "user_id", "status"),
        Index("ix_notif_user_channel_status",  "user_id", "channel", "status"),
    )


# ============================================
# USER NOTIFICATION PREFERENCES
# ============================================

class NotificationPreference(BaseModel):
    """
    Per-user, per-category, per-channel opt-in/out.
    If no row exists the global default (enabled=True) applies.
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
    enabled  = Column(Boolean, default=True, nullable=False)

    user = relationship("User", back_populates="notification_preferences")

    __table_args__ = (
        # Unique per user + category + channel combination
        Index("ix_notif_pref_ucc", "user_id", "category", "channel", unique=True),
    )


# ============================================
# DEVICE TOKEN  (push notification registration)
# ============================================

class DeviceToken(BaseModel):
    """
    FCM / APNs token per device.
    A user can have multiple active devices.
    """

    __tablename__ = "device_tokens"

    user_id     = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token       = Column(Text, unique=True, nullable=False, index=True)
    platform    = Column(String(20), nullable=False)   # "ios" | "android" | "web"
    device_name = Column(String(100), nullable=True)
    is_active   = Column(Boolean, default=True, nullable=False)
    app_version = Column(String(30), nullable=True)

    user = relationship("User", back_populates="device_tokens")