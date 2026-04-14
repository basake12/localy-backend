"""
app/dependencies.py

FastAPI dependency injection — authentication, role guards, pagination.

AUTH FLOW
---------
  JWT token  →  get_current_user (sync) / get_async_current_user (async)
             →  get_current_active_user  (email verified)
             →  get_current_fully_verified_user  (email + phone verified)

ROLE GUARDS
-----------
  Each user type has two names for convenience:
    get_current_<role>_user  — descriptive, used internally
    get_current_<role>       — short alias, used in router Depends()

  Both names are identical in behaviour — use whichever reads more clearly
  at the call site.

SYNC vs ASYNC
-------------
  Sync dependencies use Session + user_crud.get().
  Async dependencies use AsyncSession + raw select() and are required by
  any endpoint whose service layer is async (e.g. wallet, payments,
  businesses).

ADDED:
  - get_async_current_user_optional — async equivalent of get_current_user_optional.
    Returns None for unauthenticated requests. Safe for public endpoints.
  - get_async_current_business_user / require_async_business — async role
    guard for business-only endpoints that use AsyncSession.
  - get_async_current_customer_user / require_async_customer — async customer guard.
  - get_async_current_rider_user / require_async_rider — async rider guard.
"""

from typing import Optional
from fastapi import Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.core.database import get_db, get_async_db
from app.core.security import decode_token
from app.core.exceptions import (
    AuthenticationException,
    PermissionDeniedException,
    AccountSuspendedException,
    AccountBannedException,
    EmailNotVerifiedException,
    PhoneNotVerifiedException,
    InvalidUserTypeException,
)
from app.core.constants import UserType, UserStatus
from app.crud.user_crud import user_crud
from app.models.user_model import User

security = HTTPBearer()


# ================================================================
# SYNC AUTH DEPENDENCIES
# Default for all routers. Uses Session from get_db.
# ================================================================

def get_current_user(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Decode JWT and return the matching User row."""
    token = credentials.credentials
    try:
        payload = decode_token(token)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise AuthenticationException("Invalid token payload")
        user_id = UUID(user_id_str)
    except AuthenticationException:
        raise
    except Exception:
        raise AuthenticationException("Invalid authentication token")

    user = user_crud.get(db, id=user_id)
    if not user:
        raise AuthenticationException("User not found")
    if user.status == UserStatus.SUSPENDED:
        raise AccountSuspendedException()
    if user.status == UserStatus.BANNED:
        raise AccountBannedException()

    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Require an email-verified, non-suspended account.

    Phone verification is intentionally NOT required here to avoid
    a bootstrap problem where users cannot reach /verify-phone
    because their phone is not yet verified.
    Use get_current_fully_verified_user for payment/order endpoints.
    """
    if not current_user.is_email_verified:
        raise EmailNotVerifiedException()
    if current_user.status not in (UserStatus.ACTIVE, UserStatus.PENDING_VERIFICATION):
        raise PermissionDeniedException("Account is not active")
    return current_user


def get_current_fully_verified_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """Require both email AND phone verified. Use for payments and orders."""
    if not current_user.is_phone_verified:
        raise PhoneNotVerifiedException()
    return current_user


def get_current_user_optional(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
) -> Optional[User]:
    """
    Return the authenticated user if a valid Bearer token is present,
    otherwise return None. Never raises — safe for public endpoints
    that optionally personalise their response.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        token = authorization.removeprefix("Bearer ").strip()
        payload = decode_token(token)
        user_id = UUID(payload.get("sub"))
        return user_crud.get(db, id=user_id)
    except Exception:
        return None


# ================================================================
# ASYNC AUTH DEPENDENCIES
# Required by endpoints whose service layer uses AsyncSession
# (wallet, payments, businesses, notifications, etc.).
# Logic mirrors the sync variants above; user lookup uses
# raw select() because user_crud.get() is synchronous.
# ================================================================

async def get_async_current_user(
    db: AsyncSession = Depends(get_async_db),
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Decode JWT and return the matching User row (async)."""
    token = credentials.credentials
    try:
        payload = decode_token(token)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise AuthenticationException("Invalid token payload")
        user_id = UUID(user_id_str)
    except AuthenticationException:
        raise
    except Exception:
        raise AuthenticationException("Invalid authentication token")

    result = await db.execute(select(User).where(User.id == user_id))
    user: Optional[User] = result.scalars().first()

    if not user:
        raise AuthenticationException("User not found")
    if user.status == UserStatus.SUSPENDED:
        raise AccountSuspendedException()
    if user.status == UserStatus.BANNED:
        raise AccountBannedException()

    return user


async def get_async_current_active_user(
    current_user: User = Depends(get_async_current_user),
) -> User:
    """Require email-verified, active account (async)."""
    if not current_user.is_email_verified:
        raise EmailNotVerifiedException()
    if current_user.status not in (UserStatus.ACTIVE, UserStatus.PENDING_VERIFICATION):
        raise PermissionDeniedException("Account is not active")
    return current_user


async def get_async_fully_verified_user(
    current_user: User = Depends(get_async_current_active_user),
) -> User:
    """Require email + phone verified (async). Use for withdraw/transfer."""
    if not current_user.is_phone_verified:
        raise PhoneNotVerifiedException()
    return current_user


async def get_async_current_user_optional(
    db: AsyncSession = Depends(get_async_db),
    authorization: Optional[str] = Header(None),
) -> Optional[User]:
    """
    Async equivalent of get_current_user_optional.

    Returns the authenticated User if a valid Bearer token is present,
    otherwise returns None. Never raises — safe for public endpoints
    that optionally personalise their response (e.g. business profile pages).
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        token = authorization.removeprefix("Bearer ").strip()
        payload = decode_token(token)
        user_id = UUID(payload.get("sub"))
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()
    except Exception:
        return None


# ================================================================
# ROLE GUARDS  (sync)
# Each role exposes two names:
#   get_current_<role>_user  — verbose, self-documenting
#   get_current_<role>       — short alias for Depends() call sites
# Both are identical — pick whichever is clearer at the call site.
# ================================================================

def get_current_customer_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if current_user.user_type != UserType.CUSTOMER:
        raise InvalidUserTypeException([UserType.CUSTOMER])
    return current_user

get_current_customer = get_current_customer_user


def get_current_business_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if current_user.user_type != UserType.BUSINESS:
        raise InvalidUserTypeException([UserType.BUSINESS])
    return current_user

get_current_business = get_current_business_user


def get_current_rider_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if current_user.user_type != UserType.RIDER:
        raise InvalidUserTypeException([UserType.RIDER])
    return current_user

get_current_rider = get_current_rider_user


def get_current_admin_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if current_user.user_type != UserType.ADMIN:
        raise InvalidUserTypeException([UserType.ADMIN])
    return current_user

get_current_admin = get_current_admin_user
require_admin     = get_current_admin_user


# ================================================================
# ROLE GUARDS  (async)
# Async equivalents for routers that use AsyncSession throughout.
# Required by businesses, and any future module with async CRUD.
# ================================================================

async def get_async_current_business_user(
    current_user: User = Depends(get_async_current_active_user),
) -> User:
    """Require BUSINESS user type (async)."""
    if current_user.user_type != UserType.BUSINESS:
        raise InvalidUserTypeException([UserType.BUSINESS])
    return current_user

require_async_business = get_async_current_business_user


async def get_async_current_customer_user(
    current_user: User = Depends(get_async_current_active_user),
) -> User:
    """Require CUSTOMER user type (async)."""
    if current_user.user_type != UserType.CUSTOMER:
        raise InvalidUserTypeException([UserType.CUSTOMER])
    return current_user

require_async_customer = get_async_current_customer_user


async def get_async_current_rider_user(
    current_user: User = Depends(get_async_current_active_user),
) -> User:
    """Require RIDER user type (async)."""
    if current_user.user_type != UserType.RIDER:
        raise InvalidUserTypeException([UserType.RIDER])
    return current_user

require_async_rider = get_async_current_rider_user


# ================================================================
# LEGACY ALIASES
# Kept for backwards-compatibility with existing routers that
# use require_* names. All delegate to the canonical guards above.
# ================================================================

require_customer = get_current_customer_user
require_business = get_current_business_user
require_rider    = get_current_rider_user


# ================================================================
# MULTI-ROLE GUARD FACTORY
# ================================================================

def require_user_types(*allowed_types: UserType):
    """
    Dependency factory — require one of the specified user types.

    Usage:
        @router.get("/biz-or-rider")
        def endpoint(
            user: User = Depends(require_user_types(UserType.BUSINESS, UserType.RIDER))
        ): ...
    """
    def _guard(current_user: User = Depends(get_current_active_user)) -> User:
        if current_user.user_type not in allowed_types:
            raise InvalidUserTypeException(list(allowed_types))
        return current_user
    return _guard


require_role = require_user_types


# ================================================================
# PAGINATION
# ================================================================

def get_pagination_params(
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    Normalise and clamp pagination parameters.

    Returns a dict with keys: skip, limit, page, page_size.
    Page is clamped to >= 1; page_size is clamped to [1, 100].
    """
    page      = max(1, page)
    page_size = max(1, min(100, page_size))
    return {
        "skip":      (page - 1) * page_size,
        "limit":     page_size,
        "page":      page,
        "page_size": page_size,
    }


# ================================================================
# API KEY  (business integrations)
# ================================================================

def verify_api_key(
    x_api_key: str = Header(..., description="Business API key"),
) -> str:
    """
    Verify API key format. Raises 401 if the key does not start with 'lc_'.
    Full database validation should be added in the service layer.
    """
    if not x_api_key.startswith("lc_"):
        raise AuthenticationException("Invalid API key format")
    return x_api_key