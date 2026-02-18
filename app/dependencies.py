from typing import Optional, List
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.database import get_db
from app.core.security import decode_token
from app.core.exceptions import (
    AuthenticationException,
    PermissionDeniedException,
    AccountSuspendedException,
    AccountBannedException,
    EmailNotVerifiedException,
    PhoneNotVerifiedException,
    InvalidUserTypeException
)
from app.core.constants import UserType, UserStatus
from app.crud.user import user_crud
from app.models.user import User

# Security scheme
security = HTTPBearer()


# ============================================
# AUTHENTICATION DEPENDENCIES
# ============================================

def get_current_user(
        db: Session = Depends(get_db),
        credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """
    Get current authenticated user from JWT token

    Args:
        db: Database session
        credentials: HTTP bearer credentials

    Returns:
        Current user instance

    Raises:
        AuthenticationException: If token invalid or user not found
    """
    token = credentials.credentials

    # Decode token
    try:
        payload = decode_token(token)
        user_id_str = payload.get("sub")
        if user_id_str is None:
            raise AuthenticationException("Invalid token payload")

        user_id = UUID(user_id_str)
    except Exception:
        raise AuthenticationException("Invalid authentication token")

    # Get user from database
    user = user_crud.get(db, id=user_id)
    if not user:
        raise AuthenticationException("User not found")

    # Check if user is active
    if user.status == UserStatus.SUSPENDED:
        raise AccountSuspendedException()

    if user.status == UserStatus.BANNED:
        raise AccountBannedException()

    return user


def get_current_active_user(
        current_user: User = Depends(get_current_user)
) -> User:
    """
    Get current active user.

    Requires email verification only. Phone verification is enforced
    separately via get_current_fully_verified_user for endpoints that
    need it — this prevents a chicken-and-egg situation where users
    cannot call /verify-phone because they have not verified their phone.

    Raises:
        EmailNotVerifiedException: If email not verified
        PermissionDeniedException: If account is suspended or banned
    """
    if not current_user.is_email_verified:
        raise EmailNotVerifiedException()

    if current_user.status not in (UserStatus.ACTIVE, UserStatus.PENDING_VERIFICATION):
        raise PermissionDeniedException("Account not active")

    return current_user


def get_current_fully_verified_user(
        current_user: User = Depends(get_current_active_user)
) -> User:
    """
    Get current user with both email AND phone verified.

    Use this for sensitive endpoints that require full verification
    (e.g. payments, orders). Most business/customer endpoints only
    need get_current_active_user.

    Raises:
        PhoneNotVerifiedException: If phone not verified
    """
    if not current_user.is_phone_verified:
        raise PhoneNotVerifiedException()

    return current_user


def get_current_user_optional(
        db: Session = Depends(get_db),
        authorization: Optional[str] = Header(None)
) -> Optional[User]:
    """
    Get current user if authenticated, None otherwise
    Useful for endpoints that work for both authenticated and anonymous users

    Args:
        db: Database session
        authorization: Authorization header

    Returns:
        User instance or None
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    try:
        token = authorization.replace("Bearer ", "")
        payload = decode_token(token)
        user_id = UUID(payload.get("sub"))
        return user_crud.get(db, id=user_id)
    except Exception:
        return None


# ============================================
# AUTHORIZATION DEPENDENCIES (User Type)
# ============================================

def get_current_customer_user(
        current_user: User = Depends(get_current_active_user)
) -> User:
    """Get current user, asserting they are a customer"""
    if current_user.user_type != UserType.CUSTOMER:
        raise InvalidUserTypeException([UserType.CUSTOMER])
    return current_user


def get_current_business_user(
        current_user: User = Depends(get_current_active_user)
) -> User:
    """Get current user, asserting they are a business"""
    if current_user.user_type != UserType.BUSINESS:
        raise InvalidUserTypeException([UserType.BUSINESS])
    return current_user


def get_current_rider_user(
        current_user: User = Depends(get_current_active_user)
) -> User:
    """Get current user, asserting they are a rider"""
    if current_user.user_type != UserType.RIDER:
        raise InvalidUserTypeException([UserType.RIDER])
    return current_user


def get_current_admin_user(
        current_user: User = Depends(get_current_active_user)
) -> User:
    """Get current user, asserting they are an admin"""
    if current_user.user_type != UserType.ADMIN:
        raise InvalidUserTypeException([UserType.ADMIN])
    return current_user


# Backwards-compatible aliases — prefer get_current_* in new code
require_customer = get_current_customer_user
require_business = get_current_business_user
require_rider = get_current_rider_user
require_admin = get_current_admin_user


def require_user_types(*allowed_types: UserType):
    """
    Create dependency to require specific user types

    Usage:
        @app.get("/business-or-rider-only")
        def endpoint(user: User = Depends(require_user_types(UserType.BUSINESS, UserType.RIDER))):
            pass
    """

    def _check_user_type(
            current_user: User = Depends(get_current_active_user)
    ) -> User:
        if current_user.user_type not in allowed_types:
            raise InvalidUserTypeException(list(allowed_types))
        return current_user

    return _check_user_type


# ============================================
# PAGINATION DEPENDENCY
# ============================================

def get_pagination_params(
        page: int = 1,
        page_size: int = 20
) -> dict:
    """
    Get pagination parameters

    Args:
        page: Page number (1-indexed)
        page_size: Items per page

    Returns:
        Dictionary with skip and limit
    """
    if page < 1:
        page = 1

    if page_size < 1:
        page_size = 20
    elif page_size > 100:
        page_size = 100

    skip = (page - 1) * page_size

    return {
        "skip": skip,
        "limit": page_size,
        "page": page,
        "page_size": page_size
    }


# ============================================
# API KEY AUTHENTICATION (for business integrations)
# ============================================

def verify_api_key(
        x_api_key: str = Header(..., description="Business API key")
) -> str:
    """
    Verify API key for business integrations

    Args:
        x_api_key: API key from header

    Returns:
        Validated API key

    Raises:
        AuthenticationException: If API key invalid
    """
    # TODO: Implement API key verification against database
    if not x_api_key.startswith("lc_"):
        raise AuthenticationException("Invalid API key format")

    return x_api_key