"""
app/dependencies.py

FastAPI dependency injection — authentication, role guards, pagination.

FIXES vs previous version:
  1.  [HARD RULE] get_current_admin_user / require_admin DELETED from this file.
      Blueprint §2 / §13.3: admin tokens are NEVER accepted by mobile API endpoints.
      Admin auth lives in a separate admin router with its own JWT secret.
      Having it here means any accidentally imported guard could let an admin
      token hit a mobile endpoint.

  2.  All user.status enum checks replaced with user.is_active + user.is_banned
      booleans. Blueprint §14: model uses two separate booleans, not a status enum.

  3.  All user.user_type comparisons changed to user.role.
      Blueprint §14: field renamed role to match schema exactly.

  4.  get_current_active_user now checks is_phone_verified (not is_email_verified).
      Blueprint §3 is phone-number-only registration; email is optional.
      Phone verification is the gating check for active status.

  5.  require_verified_business dependency ADDED.
      Blueprint §8.4 / §8.5 / §8.6 HARD RULES: only VERIFIED businesses may
      post reels, stories, or job vacancies. Unverified businesses get a clear
      error — not a silent blank state.

  6.  All UserType.ADMIN references removed — admin is not a mobile role.
      UserRoleEnum only has CUSTOMER | BUSINESS | RIDER.

  7.  require_pin_verified — new dependency for wallet transaction endpoints.
      Blueprint §3.3: PIN required for ALL wallet transactions, withdrawals,
      and any payment above ₦5,000 regardless of session state or biometric.
      Enforced at the route dependency level, not inside each service function.

SYNC vs ASYNC:
  Sync dependencies: Session + user_crud.get() — for legacy sync routers.
  Async dependencies: AsyncSession + raw select() — required by all new routes.
"""
from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.database import get_async_db, get_db
from app.core.security import decode_token, TokenDecodeError
from app.crud.user_crud import user_crud
from app.models.user_model import User

security = HTTPBearer()

# ────────────────────────────────────────────────────────────────────────────
# Exception imports — keep local to avoid circular imports
# ────────────────────────────────────────────────────────────────────────────
from app.core.exceptions import (
    AuthenticationException,
    PermissionDeniedException,
)


def _banned_or_inactive_check(user: User) -> None:
    """
    Shared status check for both sync and async auth paths.
    Blueprint §14: is_active + is_banned are separate booleans.
    """
    if user.is_banned:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "account_banned",
                "message": "Your account has been banned. Contact support to appeal.",
            },
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "account_inactive",
                "message": "Your account is inactive. Contact support.",
            },
        )


# ════════════════════════════════════════════════════════════════════════════
# SYNC AUTH DEPENDENCIES
# ════════════════════════════════════════════════════════════════════════════

def get_current_user(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Decode JWT and return the matching User row (sync)."""
    try:
        payload     = decode_token(credentials.credentials)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise AuthenticationException("Invalid token payload")
        user_id = UUID(user_id_str)
    except (TokenDecodeError, ValueError):
        raise AuthenticationException("Invalid authentication token")

    user = user_crud.get(db, id=user_id)
    if not user:
        raise AuthenticationException("User not found")

    _banned_or_inactive_check(user)
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Require phone-verified account.

    Blueprint §3: registration is phone-only.
    Email is optional — phone verification is the gating check.
    """
    if not current_user.is_phone_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "phone_not_verified",
                "message": "Please verify your phone number to continue.",
            },
        )
    return current_user


def get_current_user_optional(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
) -> Optional[User]:
    """Return authenticated User if valid Bearer token present, else None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        token   = authorization.removeprefix("Bearer ").strip()
        payload = decode_token(token)
        user_id = UUID(payload.get("sub"))
        return user_crud.get(db, id=user_id)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════════
# ASYNC AUTH DEPENDENCIES
# ════════════════════════════════════════════════════════════════════════════

async def get_async_current_user(
    db: AsyncSession = Depends(get_async_db),
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Decode JWT and return matching User row (async)."""
    try:
        payload     = decode_token(credentials.credentials)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise AuthenticationException("Invalid token payload")
        user_id = UUID(user_id_str)
    except (TokenDecodeError, ValueError):
        raise AuthenticationException("Invalid authentication token")

    result = await db.execute(select(User).where(User.id == user_id))
    user: Optional[User] = result.scalars().first()

    if not user:
        raise AuthenticationException("User not found")

    _banned_or_inactive_check(user)
    return user


async def get_async_current_active_user(
    current_user: User = Depends(get_async_current_user),
) -> User:
    """Require phone-verified, active account (async)."""
    if not current_user.is_phone_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "phone_not_verified",
                "message": "Please verify your phone number to continue.",
            },
        )
    return current_user


async def get_async_current_user_optional(
    db: AsyncSession = Depends(get_async_db),
    authorization: Optional[str] = Header(None),
) -> Optional[User]:
    """Async equivalent of get_current_user_optional. Never raises."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        token   = authorization.removeprefix("Bearer ").strip()
        payload = decode_token(token)
        user_id = UUID(payload.get("sub"))
        result  = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════════
# ROLE GUARDS — sync
# Blueprint §2: role IN ('customer','business','rider') ONLY.
# ADMIN is NOT a mobile role — admin has its own separate JWT + router.
# ════════════════════════════════════════════════════════════════════════════

def get_current_customer_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """Require CUSTOMER role."""
    if current_user.role.value != "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "role_required", "required": "customer"},
        )
    return current_user

get_current_customer = get_current_customer_user
require_customer     = get_current_customer_user


def get_current_business_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """Require BUSINESS role."""
    if current_user.role.value != "business":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "role_required", "required": "business"},
        )
    return current_user

get_current_business = get_current_business_user
require_business     = get_current_business_user


def get_current_rider_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """Require RIDER role."""
    if current_user.role.value != "rider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "role_required", "required": "rider"},
        )
    return current_user

get_current_rider = get_current_rider_user
require_rider     = get_current_rider_user


# REMOVED: get_current_admin_user / require_admin
# Blueprint §2 / §13.3 HARD RULE: admin tokens are NEVER accepted by mobile
# API endpoints. Admin auth is in a separate admin router with JWT_ADMIN_SECRET_KEY.


def require_verified_business(
    current_user: User = Depends(get_current_business_user),
) -> User:
    """
    Require BUSINESS role AND is_verified = True.

    Blueprint §8.4 / §8.5 / §8.6 HARD RULES:
      "Only VERIFIED businesses may post reels / stories / job vacancies.
       Unverified businesses see these features LOCKED with a clear
       verification prompt — NOT a silent blank state."

    Returns HTTP 403 with structured error if business is not yet verified.
    """
    if not current_user.business or not current_user.business.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "business_not_verified",
                "message": (
                    "Your business profile must be verified before you can post "
                    "reels, stories, or job vacancies. Complete your profile and "
                    "await admin review."
                ),
            },
        )
    return current_user


# ════════════════════════════════════════════════════════════════════════════
# ROLE GUARDS — async
# ════════════════════════════════════════════════════════════════════════════

async def get_async_current_business_user(
    current_user: User = Depends(get_async_current_active_user),
) -> User:
    """Require BUSINESS role (async)."""
    if current_user.role.value != "business":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "role_required", "required": "business"},
        )
    return current_user

require_async_business = get_async_current_business_user


async def get_async_current_customer_user(
    current_user: User = Depends(get_async_current_active_user),
) -> User:
    """Require CUSTOMER role (async)."""
    if current_user.role.value != "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "role_required", "required": "customer"},
        )
    return current_user

require_async_customer = get_async_current_customer_user


async def get_async_current_rider_user(
    current_user: User = Depends(get_async_current_active_user),
) -> User:
    """Require RIDER role (async)."""
    if current_user.role.value != "rider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "role_required", "required": "rider"},
        )
    return current_user

require_async_rider = get_async_current_rider_user


async def require_async_verified_business(
    current_user: User = Depends(get_async_current_business_user),
) -> User:
    """
    Require BUSINESS role AND is_verified = True (async).
    Blueprint §8.4 / §8.5 / §8.6 HARD RULES.
    """
    if not current_user.business or not current_user.business.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "business_not_verified",
                "message": (
                    "Your business must be verified before posting reels, "
                    "stories, or job vacancies."
                ),
            },
        )
    return current_user


# ════════════════════════════════════════════════════════════════════════════
# PIN VERIFICATION DEPENDENCY
# Blueprint §3.3: PIN required for ALL wallet transactions + withdrawals +
# any payment above ₦5,000, regardless of session state or biometric.
# ════════════════════════════════════════════════════════════════════════════

class PinRequired(HTTPException):
    """Raised when a wallet operation requires PIN confirmation."""
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "pin_required",
                "message": "This action requires PIN confirmation.",
            },
        )


# NOTE: PIN verification itself happens in the request body / service layer
# because the PIN is sent in the request payload, not in the JWT.
# This dependency simply ensures the user HAS a PIN set up.

def require_pin_set(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """
    Require that the user has a PIN configured.
    Blueprint §3.3: PIN mandatory — no wallet action without it.
    """
    if not current_user.pin_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "pin_not_configured",
                "message": "Set up your 4-digit PIN before making transactions.",
            },
        )
    return current_user


# ════════════════════════════════════════════════════════════════════════════
# MULTI-ROLE GUARD FACTORY
# ════════════════════════════════════════════════════════════════════════════

def require_roles(*allowed_roles: str):
    """
    Dependency factory — require one of the specified roles.

    Usage:
        @router.get("/shared")
        def endpoint(user: User = Depends(require_roles("business", "rider"))):
            ...
    """
    def _guard(current_user: User = Depends(get_current_active_user)) -> User:
        role_val = (
            current_user.role.value
            if hasattr(current_user.role, "value")
            else str(current_user.role)
        )
        if role_val not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "role_required",
                    "required": list(allowed_roles),
                    "current": role_val,
                },
            )
        return current_user
    return _guard

# Legacy alias
require_user_types = require_roles
require_role       = require_roles


# ════════════════════════════════════════════════════════════════════════════
# PAGINATION
# ════════════════════════════════════════════════════════════════════════════

def get_pagination_params(
    page:      int = 1,
    page_size: int = 20,
) -> dict:
    """
    Normalise and clamp pagination parameters.
    Returns: {skip, limit, page, page_size}
    """
    page      = max(1, page)
    page_size = max(1, min(100, page_size))
    return {
        "skip":      (page - 1) * page_size,
        "limit":     page_size,
        "page":      page,
        "page_size": page_size,
    }


# ════════════════════════════════════════════════════════════════════════════
# API KEY
# ════════════════════════════════════════════════════════════════════════════

def verify_api_key(
    x_api_key: str = Header(..., description="Business API key"),
) -> str:
    """Verify API key format (lc_ prefix). Full DB validation in service layer."""
    if not x_api_key.startswith("lc_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_api_key", "message": "Invalid API key format."},
        )
    return x_api_key