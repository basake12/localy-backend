from __future__ import annotations

from typing import Literal, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID

from app.models.notifications_model import (
    NotificationCategoryEnum,
    NotificationChannelEnum,
)

# ============================================
# NOTIFICATION — READ
# ============================================

class NotificationOut(BaseModel):
    id:           UUID
    channel:      str
    category:     str
    title:        str
    body:         str
    action_url:   Optional[str]      = None
    icon_url:     Optional[str]      = None
    status:       str
    sent_at:      Optional[datetime] = None   # proper datetime, not str
    read_at:      Optional[datetime] = None
    created_at:   datetime

    model_config = {"from_attributes": True}


class NotificationListOut(BaseModel):
    notifications: List[NotificationOut]
    total:         int
    unread_count:  int
    skip:          int
    limit:         int


# ============================================
# NOTIFICATION PREFERENCES
# ============================================

class PreferenceItem(BaseModel):
    category: str
    channel:  str
    enabled:  bool


class PreferencesOut(BaseModel):
    """Full preference map for the current user."""
    preferences: List[PreferenceItem]


class PreferenceToggle(BaseModel):
    """Toggle a single (category, channel) pair."""
    category: str = Field(..., description="NotificationCategoryEnum value")
    channel:  str = Field(..., description="NotificationChannelEnum value")
    enabled:  bool

    @classmethod
    def validate_category(cls, v: str) -> str:
        valid = {e.value for e in NotificationCategoryEnum}
        if v not in valid:
            raise ValueError(f"category must be one of {sorted(valid)}")
        return v

    @classmethod
    def validate_channel(cls, v: str) -> str:
        valid = {e.value for e in NotificationChannelEnum}
        if v not in valid:
            raise ValueError(f"channel must be one of {sorted(valid)}")
        return v


# ============================================
# DEVICE TOKEN
# ============================================

class DeviceTokenCreate(BaseModel):
    token:       str                                    = Field(..., min_length=10)
    # Literal replaces the deprecated Field(enum=[...]) pattern from Pydantic v1
    platform:    Literal["ios", "android", "web"]
    device_name: Optional[str]                          = None
    app_version: Optional[str]                          = None


class DeviceTokenOut(BaseModel):
    id:          UUID
    token:       str
    platform:    str
    device_name: Optional[str] = None
    is_active:   bool
    created_at:  datetime

    model_config = {"from_attributes": True}


# ============================================
# INTERNAL — used by NotificationService (not exposed via API)
# ============================================

class NotificationPayload(BaseModel):
    """
    What other modules pass to notification_service.send().
    The service fans this out across channels automatically.
    """
    user_id:    UUID
    category:   str               # NotificationCategoryEnum value
    title:      str               = Field(..., max_length=200)
    body:       str
    action_url: Optional[str]     = None
    icon_url:   Optional[str]     = None
    # Force specific channels (None → all enabled channels for user+category)
    channels:   Optional[List[str]] = None