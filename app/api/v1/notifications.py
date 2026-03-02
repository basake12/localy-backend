from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_active_user
from app.models.user_model import User
from app.schemas.notifications_schema import (
    NotificationOut,
    NotificationListOut,
    PreferencesOut,
    PreferenceToggle,
    DeviceTokenCreate,
    DeviceTokenOut,
)
from app.services.notification_service import (
    notification_history_svc,
    preference_service,
    device_token_service,
)

router = APIRouter()


# ============================================
# NOTIFICATION HISTORY
# ============================================

@router.get("", response_model=NotificationListOut)
def list_notifications(
    category: str | None = Query(None),
    channel:  str | None = Query(None, enum=["in_app", "push", "email", "sms"]),
    status:   str | None = Query(None, enum=["pending", "sent", "delivered", "read", "failed"]),
    skip:     int        = Query(0,  ge=0),
    limit:    int        = Query(30, ge=1, le=100),
    db:       Session    = Depends(get_db),
    user:     User       = Depends(get_current_active_user),
):
    """Paginated notification history. Defaults to in-app bell feed."""
    if channel is None:
        channel = "in_app"   # default view is the notification bell
    return notification_history_svc.list_notifications(
        db, user_id=user.id,
        category=category, channel=channel,
        status=status, skip=skip, limit=limit,
    )


@router.put("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: UUID,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """Mark a single notification as read."""
    return notification_history_svc.mark_read(db, user_id=user.id, notification_id=notification_id)


@router.put("/read-all", status_code=200)
def mark_all_read(
    category: str | None = Query(None),
    db:       Session    = Depends(get_db),
    user:     User       = Depends(get_current_active_user),
):
    """Mark all in-app notifications as read. Optionally filter by category."""
    count = notification_history_svc.mark_all_read(db, user_id=user.id, category=category)
    return {"success": True, "data": {"marked_read": count}}


# ============================================
# PREFERENCES
# ============================================

@router.get("/preferences", response_model=PreferencesOut)
def get_preferences(
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """Get all notification opt-in/out preferences for the current user."""
    prefs = preference_service.get_preferences(db, user_id=user.id)
    return {"preferences": prefs}


@router.put("/preferences", status_code=200)
def toggle_preference(
    payload: PreferenceToggle,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_active_user),
):
    """Toggle a single (category, channel) preference on or off."""
    result = preference_service.toggle(db, user_id=user.id, payload=payload)
    return {"success": True, "data": result}


# ============================================
# DEVICE TOKENS  (push registration)
# ============================================

@router.post("/device-tokens", response_model=DeviceTokenOut, status_code=201)
def register_device_token(
    payload: DeviceTokenCreate,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_active_user),
):
    """
    Register (or refresh) a push notification device token.
    Call on app foreground after obtaining FCM / APNs token.
    Idempotent — updates existing row if token already registered.
    """
    return device_token_service.register(
        db,
        user_id=user.id,
        token=payload.token,
        platform=payload.platform,
        device_name=payload.device_name,
        app_version=payload.app_version,
    )


@router.delete("/device-tokens/{token}", status_code=204)
def unregister_device_token(
    token:  str,
    db:     Session = Depends(get_db),
    user:   User    = Depends(get_current_active_user),
):
    """Deactivate a device token (e.g. on logout)."""
    device_token_service.unregister(db, token=token)