"""
app/core/admin_deps.py

FastAPI dependency injection for admin authentication.

Blueprint §2.2 HARD RULE:
  "Admin exists only as a web application. There is no admin panel inside
   the mobile app."

Blueprint §3.2:
  "Admin tokens are issued by a separate endpoint and carry an 'admin' role
   claim — they are NEVER accepted by mobile API endpoints."

CRITICAL: require_admin MUST NEVER be imported into any mobile router.
Mobile routers live in app/routers/. Admin routers live in app/routers/admin/.
Import discipline is the last line of defence against cross-contamination.

Blueprint §13.3:
  "Separate JWT auth from mobile app. Not shared with mobile tokens.
   Admin token claim: { role: 'admin', admin_id: uuid }"
"""

from uuid import UUID
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.admin_security import decode_admin_token, AdminTokenError
from app.models.admin_model import AdminUser

admin_bearer = HTTPBearer()


def _get_admin_from_token(
    credentials: HTTPAuthorizationCredentials = Depends(admin_bearer),
    db: Session = Depends(get_db),
) -> AdminUser:
    """
    Core admin authentication dependency.
    Decodes the admin JWT using JWT_ADMIN_SECRET_KEY and returns the AdminUser.
    Raises HTTP 401 if token is invalid, expired, or not an admin token.
    Raises HTTP 403 if admin account is inactive.
    """
    try:
        payload = decode_admin_token(credentials.credentials)
    except AdminTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error":   "invalid_admin_token",
                "message": str(exc),
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    admin_id_str = payload.get("admin_id")
    try:
        admin_id = UUID(admin_id_str)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_admin_token", "message": "Malformed admin_id claim"},
        )

    admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "admin_not_found", "message": "Admin account not found"},
        )

    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_inactive", "message": "Admin account is deactivated"},
        )

    return admin


def require_admin(
    admin: AdminUser = Depends(_get_admin_from_token),
) -> AdminUser:
    """
    Standard admin gate. Allows all active admin roles:
      super_admin | admin | support_agent

    Blueprint §2.2: NEVER import this into mobile routers.
    Use this as the Depends() on every /admin/* endpoint.
    """
    return admin


def require_super_admin(
    admin: AdminUser = Depends(_get_admin_from_token),
) -> AdminUser:
    """
    Elevated admin gate for destructive or financial operations:
      - Platform fee rate changes
      - Wallet manual credit/debit
      - Permanent user deletion
      - Ban operations

    Only super_admin role is permitted.
    """
    if admin.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error":   "insufficient_admin_role",
                "message": "This action requires super_admin role.",
                "required": "super_admin",
                "current":  admin.role,
            },
        )
    return admin


def require_admin_or_support(
    admin: AdminUser = Depends(_get_admin_from_token),
) -> AdminUser:
    """
    Gate for support-accessible endpoints:
      super_admin | admin | support_agent

    Blueprint §10.3: support agents can view tickets and respond,
    but cannot modify user accounts or financial records.
    """
    return admin  # all active admin roles are permitted here