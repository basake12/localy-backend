"""
app/routers/admin_auth.py

Blueprint §3.2:
  "Admin tokens are issued by a separate endpoint and carry an 'admin' role
   claim — they are never accepted by mobile API endpoints."

Blueprint §13.3:
  "Separate JWT auth from mobile app. Not shared with mobile tokens.
   Admin token claim: { role: 'admin', admin_id: uuid }"

Blueprint §16.4 HARD RULE: datetime.now(timezone.utc) everywhere.

Hosted at: admin.localy.ng (separate subdomain)
Endpoint:  POST /api/v1/admin/auth/login

NOTE: There is NO self-registration endpoint for admins.
Admin accounts are created only via migration scripts or by a super_admin
through the admin panel's "Support Agents" management interface (§11.6).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.admin_security import (
    verify_admin_password,
    create_admin_access_token,
    create_admin_refresh_token,
    decode_admin_token,
    AdminTokenError,
)
from app.models.admin_model import AdminUser

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request / Response schemas (inline — admin auth is simple) ────────────────

class AdminLoginRequest(BaseModel):
    email:    EmailStr
    password: str = Field(..., min_length=8)


class AdminLoginResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    admin_id:      str
    admin_role:    str
    full_name:     str


class AdminRefreshRequest(BaseModel):
    refresh_token: str


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=AdminLoginResponse,
    summary="Admin login — issues admin JWT (Blueprint §3.2 + §13.3)",
)
def admin_login(
    body: AdminLoginRequest,
    db:   Session = Depends(get_db),
) -> dict:
    """
    Blueprint §3.2: "Admin tokens are issued by a separate endpoint."
    Blueprint §13.3: "Admin token claim: { role: 'admin', admin_id: uuid }"

    Uses JWT_ADMIN_SECRET_KEY — separate from mobile JWT_SECRET_KEY.
    Returns admin access token (1hr) and refresh token (8hr).
    """
    admin = db.query(AdminUser).filter(
        AdminUser.email == body.email,
        AdminUser.is_active.is_(True),
    ).first()

    if not admin or not verify_admin_password(body.password, admin.password_hash):
        # Intentionally vague — do not reveal whether email exists
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error":   "invalid_credentials",
                "message": "Invalid email or password.",
            },
        )

    # Update last login timestamp (Blueprint §16.4)
    admin.last_login_at = datetime.now(timezone.utc)
    db.commit()

    access_token  = create_admin_access_token(admin.id, admin.role)
    refresh_token = create_admin_refresh_token(admin.id)

    logger.info("Admin login: admin_id=%s role=%s", admin.id, admin.role)

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "admin_id":      str(admin.id),
        "admin_role":    admin.role,
        "full_name":     admin.full_name,
    }


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post(
    "/refresh",
    summary="Refresh admin access token",
)
def admin_refresh_token(
    body: AdminRefreshRequest,
    db:   Session = Depends(get_db),
) -> dict:
    """Rotate admin access token using a valid admin refresh token."""
    try:
        payload = decode_admin_token(body.refresh_token)
    except AdminTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_refresh_token", "message": "Invalid or expired refresh token."},
        )

    if payload.get("role") != "admin_refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "not_a_refresh_token"},
        )

    from uuid import UUID
    admin_id = UUID(payload["admin_id"])
    admin = db.query(AdminUser).filter(
        AdminUser.id == admin_id,
        AdminUser.is_active.is_(True),
    ).first()

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "admin_not_found"},
        )

    return {
        "access_token":  create_admin_access_token(admin.id, admin.role),
        "refresh_token": create_admin_refresh_token(admin.id),
        "token_type":    "bearer",
    }