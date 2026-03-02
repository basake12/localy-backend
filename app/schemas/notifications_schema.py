from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime
from uuid import UUID


# ============================================
# NOTIFICATION — READ
# ============================================

class NotificationOut(BaseModel):
    id:            UUID
    channel:       str
    category:      str
    title:         str
    body:          str
    action_url:    Optional[str] = None
    icon_url:      Optional[str] = None
    status:        str
    sent_at:       Optional[str] = None
    read_at:       Optional[str] = None
    created_at:    datetime

    class Config:
        from_attributes = True


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
    category: str
    channel:  str
    enabled:  bool


# ============================================
# DEVICE TOKEN
# ============================================

class DeviceTokenCreate(BaseModel):
    token:       str  = Field(..., min_length=10)
    platform:    str  = Field(..., enum=["ios", "android", "web"])
    device_name: Optional[str] = None
    app_version: Optional[str] = None


class DeviceTokenOut(BaseModel):
    id:          UUID
    token:       str
    platform:    str
    device_name: Optional[str] = None
    is_active:   bool
    created_at:  datetime

    class Config:
        from_attributes = True


# ============================================
# INTERNAL — used by NotificationService (not exposed)
# ============================================

class NotificationPayload(BaseModel):
    """
    What other modules pass to notification_service.send().
    The service fans this out across channels automatically.
    """
    user_id:    UUID
    category:   str               # NotificationCategoryEnum value
    title:      str
    body:       str
    action_url: Optional[str]     = None
    icon_url:   Optional[str]     = None
    # Force specific channels (if None → all enabled channels for user+category)
    channels:   Optional[List[str]] = None