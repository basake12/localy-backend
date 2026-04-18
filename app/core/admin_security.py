"""
app/core/admin_security.py

Blueprint §3.2:
  "Admin tokens are issued by a separate endpoint and carry an 'admin' role
   claim — they are NEVER accepted by mobile API endpoints."
  "Token: JWT_ADMIN_SECRET_KEY — separate from JWT_SECRET_KEY."

Blueprint §13.3:
  "Separate JWT auth from mobile app. Not shared with mobile tokens.
   Admin token claim: { role: 'admin', admin_id: uuid }"

Blueprint §16.4 HARD RULE: datetime.now(timezone.utc) always.

This module is the ONLY place that uses JWT_ADMIN_SECRET_KEY.
Mobile security (JWT_SECRET_KEY) is in app/core/security.py.
The two secrets must never be swapped or shared.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

logger = logging.getLogger(__name__)

# Admin-specific bcrypt context (same algo, separate instance for clarity)
admin_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Token lifetimes (admin sessions are shorter for security)
_ADMIN_ACCESS_TOKEN_TTL  = timedelta(minutes=60)   # 1 hour
_ADMIN_REFRESH_TOKEN_TTL = timedelta(hours=8)       # 8 hours (working day)


class AdminTokenError(Exception):
    """Raised when admin JWT decode fails."""


def hash_admin_password(password: str) -> str:
    """Hash admin password with bcrypt."""
    return admin_pwd_context.hash(password)


def verify_admin_password(plain: str, hashed: str) -> bool:
    """Verify admin password against bcrypt hash."""
    return admin_pwd_context.verify(plain, hashed)


def create_admin_access_token(admin_id: UUID, admin_role: str) -> str:
    """
    Issue a signed admin JWT access token.
    Blueprint §13.3 claim structure:
      { role: "admin", admin_id: uuid, admin_role: "super_admin|admin|support_agent",
        iat, exp }

    Uses JWT_ADMIN_SECRET_KEY — NEVER JWT_SECRET_KEY.
    """
    now = datetime.now(timezone.utc)  # Blueprint §16.4

    payload = {
        "role":       "admin",          # distinguishes admin tokens from mobile tokens
        "admin_id":   str(admin_id),
        "admin_role": admin_role,        # super_admin | admin | support_agent
        "iat":        now,
        "exp":        now + _ADMIN_ACCESS_TOKEN_TTL,
    }

    return jwt.encode(
        payload,
        settings.JWT_ADMIN_SECRET_KEY,
        algorithm="HS256",
    )


def create_admin_refresh_token(admin_id: UUID) -> str:
    """Issue admin refresh token (8-hour TTL)."""
    now = datetime.now(timezone.utc)  # Blueprint §16.4

    payload = {
        "role":     "admin_refresh",
        "admin_id": str(admin_id),
        "iat":      now,
        "exp":      now + _ADMIN_REFRESH_TOKEN_TTL,
    }

    return jwt.encode(
        payload,
        settings.JWT_ADMIN_SECRET_KEY,
        algorithm="HS256",
    )


def decode_admin_token(token: str) -> dict:
    """
    Decode and validate an admin JWT.
    Raises AdminTokenError if the token is invalid, expired, or not an admin token.

    Blueprint §3.2: "Admin tokens are issued by a separate endpoint and carry
    an 'admin' role claim — they are never accepted by mobile API endpoints."

    The 'role': 'admin' claim check ensures a mobile JWT (role: 'customer' etc.)
    cannot be used to authenticate as admin even if signed with the same secret.
    Using a SEPARATE secret (JWT_ADMIN_SECRET_KEY) is the primary barrier;
    this claim check is a secondary defence-in-depth layer.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_ADMIN_SECRET_KEY,
            algorithms=["HS256"],
        )
    except JWTError as exc:
        raise AdminTokenError(f"Invalid admin token: {exc}") from exc

    # Ensure this is genuinely an admin token (not a mobile JWT)
    if payload.get("role") not in ("admin", "admin_refresh"):
        raise AdminTokenError("Token is not an admin token")

    admin_id_str = payload.get("admin_id")
    if not admin_id_str:
        raise AdminTokenError("admin_id claim missing from token")

    try:
        UUID(admin_id_str)  # validate UUID format
    except ValueError as exc:
        raise AdminTokenError("admin_id claim is not a valid UUID") from exc

    return payload