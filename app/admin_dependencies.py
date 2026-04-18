"""
app/admin_dependencies.py

Admin-only authentication dependency.

Blueprint §2 / §13.3 HARD RULE:
  Admin tokens use a SEPARATE secret key (JWT_ADMIN_SECRET_KEY) and are
  NEVER accepted by mobile API endpoints.

  We validate the JWT claims only (role == "admin", admin_id present).
  No ORM model import here — avoids duplicate SQLAlchemy table registration
  since admin_users is already mapped via the main models package.

Admin token payload: { "sub": "<admin_id>", "role": "admin" }
"""
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import jwt
from jwt.exceptions import InvalidTokenError

security = HTTPBearer()


def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency: require a valid admin JWT signed with JWT_ADMIN_SECRET_KEY.

    Blueprint §13.3: admin tokens carry { role: "admin", admin_id: uuid }.
    Returns the decoded payload dict. Raises 401/403 on any failure.
    No DB query — JWT claims are the source of truth for admin identity.
    """
    from app.config import settings

    secret = settings.JWT_ADMIN_SECRET_KEY
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "admin_auth_disabled",
                "message": "Admin authentication is not configured on this server.",
            },
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=[settings.ALGORITHM],
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_admin_token", "message": "Invalid or expired admin token."},
        )

    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_required", "message": "Admin access required."},
        )

    admin_id_str = payload.get("admin_id") or payload.get("sub")
    if not admin_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_admin_token", "message": "Token missing admin_id."},
        )

    try:
        UUID(admin_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_admin_token", "message": "Invalid admin_id format."},
        )

    return payload