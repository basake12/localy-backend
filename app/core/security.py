"""
Core security utilities for Localy.

- Password hashing   → argon2 (no 72-byte limit, more secure than bcrypt)
- PIN hashing        → bcrypt (for 4-digit PINs)
- JWT management     → python-jose
- OTP generation     → secrets
- Permission checks  → check_user_type / require_user_type
"""
import asyncio
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable, Optional, Union

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

log = logging.getLogger(__name__)

# ─── Password Hashing ─────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash plain-text password using argon2."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against its argon2 hash."""
    return pwd_context.verify(plain_password, hashed_password)


def validate_password_strength(password: str) -> bool:
    """
    Return True if the password meets minimum requirements:
      - ≥ 8 characters (configurable via settings.PASSWORD_MIN_LENGTH)
      - At least one uppercase letter
      - At least one lowercase letter
      - At least one digit
    """
    min_len = getattr(settings, "PASSWORD_MIN_LENGTH", 8)
    if len(password) < min_len:
        return False
    return (
        any(c.isupper() for c in password)
        and any(c.islower() for c in password)
        and any(c.isdigit() for c in password)
    )


# ─── PIN Hashing & Validation (Blueprint v2.0) ────────────────────────────────

# Separate context for PINs using bcrypt (fast, sufficient for 4-digit PINs)
pin_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_pin(pin: str) -> str:
    """
    Hash a 4-digit PIN using bcrypt.
    
    Blueprint requirement: PINs are 4 digits, stored as bcrypt hash.
    """
    if not validate_pin(pin):
        raise ValueError("PIN must be exactly 4 digits")
    return pin_context.hash(pin)


def verify_pin(plain_pin: str, hashed_pin: str) -> bool:
    """Verify plain 4-digit PIN against its bcrypt hash."""
    return pin_context.verify(plain_pin, hashed_pin)


def validate_pin(pin: str) -> bool:
    """
    Return True if PIN is exactly 4 digits.
    
    Blueprint requirement: "Set 4-digit transaction PIN"
    """
    return len(pin) == 4 and pin.isdigit()


# ─── JWT ──────────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    """Timezone-aware UTC now — Python 3.11+ compatible."""
    return datetime.now(timezone.utc)


def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token.

    Args:
        subject:       User ID (str/UUID).
        expires_delta: Override default expiry.

    Returns:
        Encoded JWT string.
    """
    expire = _utcnow() + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": str(subject),
        "exp": expire,
        "iat": _utcnow(),
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT refresh token.

    Refresh tokens carry ``"type": "refresh"`` so they can be
    distinguished from access tokens during validation.
    """
    expire = _utcnow() + (
        expires_delta
        if expires_delta is not None
        else timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )
    payload = {
        "sub": str(subject),
        "exp": expire,
        "iat": _utcnow(),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


class TokenDecodeError(Exception):
    """Raised when a JWT cannot be decoded or validated."""


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT.

    Returns:
        Decoded payload dict.

    Raises:
        TokenDecodeError: If the token is invalid, expired, or malformed.

    Note:
        This function deliberately does NOT raise FastAPI's HTTPException so
        that security core utilities remain framework-agnostic.
        Callers (e.g. dependency functions) are responsible for translating
        TokenDecodeError → HTTPException(401).
    """
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError as exc:
        raise TokenDecodeError(str(exc)) from exc


def verify_token(token: str) -> Optional[str]:
    """
    Safely decode token and return the user_id (``sub`` claim).

    Returns None instead of raising — use where authentication is optional.
    """
    try:
        payload = decode_token(token)
        return payload.get("sub")
    except TokenDecodeError:
        return None


# ─── OTP Generation ───────────────────────────────────────────────────────────

def generate_otp(length: int = 6) -> str:
    """Generate a cryptographically secure numeric OTP."""
    return "".join(secrets.choice(string.digits) for _ in range(length))


def generate_verification_token(length: int = 32) -> str:
    """Generate a secure URL-safe random token (for email verification links)."""
    return secrets.token_urlsafe(length)


# ─── Permission Checks ────────────────────────────────────────────────────────

def check_user_type(user_type: str, allowed_types: list[str]) -> bool:
    """Return True if user_type is in allowed_types."""
    return user_type in allowed_types


def require_user_type(*allowed_types: str) -> Callable:
    """
    Decorator that enforces user type on both sync and async functions.

    The first positional argument (or ``user`` keyword arg) must have a
    ``.user_type`` attribute.

    Usage (non-FastAPI utility / service functions):

        @require_user_type("admin", "business")
        def some_service_fn(user, ...):
            ...

        @require_user_type("customer")
        async def some_async_service_fn(user, ...):
            ...

    For FastAPI endpoints use the ``get_current_*_user`` dependencies instead.

    Raises:
        PermissionError: If user.user_type is not in allowed_types.
            (Callers convert this to HTTPException(403) at the boundary layer.)
    """
    def _resolve_user(args, kwargs):
        return kwargs.get("user") or (args[0] if args else None)

    def _check(user) -> None:
        if user is None or not hasattr(user, "user_type"):
            raise PermissionError("Permission denied")
        user_type_val = (
            user.user_type.value
            if hasattr(user.user_type, "value")
            else str(user.user_type)
        )
        if user_type_val not in allowed_types:
            raise PermissionError(
                f"Access restricted to: {', '.join(allowed_types)}"
            )

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                _check(_resolve_user(args, kwargs))
                return await func(*args, **kwargs)
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                _check(_resolve_user(args, kwargs))
                return func(*args, **kwargs)
            return sync_wrapper

    return decorator


# ─── API Key Generation ───────────────────────────────────────────────────────

def generate_api_key() -> str:
    """Generate a secure API key prefixed with ``lc_`` for business integrations."""
    return f"lc_{secrets.token_urlsafe(32)}"


def verify_api_key(api_key: str) -> bool:
    """Return True if api_key has valid Localy format."""
    return api_key.startswith("lc_") and len(api_key) > 35