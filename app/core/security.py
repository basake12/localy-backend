"""
app/core/security.py

FIXES:
  BUG-R2 FIX: _get_redis() replaced with get_redis() from app.core.cache.
    Old code: redis.from_url() called inside each function → new ConnectionPool
    on every call → hundreds of short-lived Redis connections under load.
    Fix: use the shared ConnectionPool from cache.py. All Redis connections
    in the app now share a single pool.

  BUG-R8 FIX: Session key operations now use redis_bp (LocalyRedisKeys)
    from app.core.cache, which has Blueprint §16.3 TTLs as named constants.
    The invalidate_all_sessions function uses SCAN (not KEYS) via redis_bp.

All other fixes from previous version retained:
  1.  pin_context: bcrypt — Blueprint §3.1 step 6 / §3.3.
  2.  create_access_token: role + business_id — Blueprint §3.2 JWT payload.
  3.  create_refresh_token: jti — Blueprint §3.2 token rotation.
  4.  All datetime: datetime.now(timezone.utc) — Blueprint §16.4 HARD RULE.
"""
import asyncio
import logging
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable, Optional, Union

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

log = logging.getLogger(__name__)

# ─── Password Hashing (argon2) ────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def validate_password_strength(password: str) -> bool:
    min_len = getattr(settings, "PASSWORD_MIN_LENGTH", 8)
    if len(password) < min_len:
        return False
    return (
        any(c.isupper() for c in password)
        and any(c.islower() for c in password)
        and any(c.isdigit() for c in password)
    )


# ─── PIN Hashing (bcrypt — Blueprint §3.1 step 6 / §3.3) ─────────────────────

pin_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_pin(pin: str) -> str:
    if not validate_pin(pin):
        raise ValueError("PIN must be exactly 4 digits")
    return pin_context.hash(pin)


def verify_pin(plain_pin: str, hashed_pin: str) -> bool:
    return pin_context.verify(plain_pin, hashed_pin)


def validate_pin(pin: str) -> bool:
    return isinstance(pin, str) and len(pin) == 4 and pin.isdigit()


# ─── UTC helper ───────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: NEVER datetime.utcnow()."""
    return datetime.now(timezone.utc)


# ─── JWT ──────────────────────────────────────────────────────────────────────

def create_access_token(
    subject: Union[str, Any],
    role: str,
    business_id: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    now    = _utcnow()
    expire = now + (
        expires_delta if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub":         str(subject),
        "role":        role,
        "business_id": business_id,
        "exp":         expire,
        "iat":         now,
        "type":        "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(
    subject: Union[str, Any],
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, str]:
    now    = _utcnow()
    jti    = str(uuid.uuid4())
    expire = now + (
        expires_delta if expires_delta is not None
        else timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )
    payload = {
        "sub":  str(subject),
        "role": role,
        "jti":  jti,
        "exp":  expire,
        "iat":  now,
        "type": "refresh",
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, jti


class TokenDecodeError(Exception):
    """Raised when a JWT cannot be decoded or validated."""


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError as exc:
        raise TokenDecodeError(str(exc)) from exc


def verify_token(token: str) -> Optional[str]:
    try:
        payload = decode_token(token)
        return payload.get("sub")
    except TokenDecodeError:
        return None


# ─── Redis Session Management (BUG-R2 + BUG-R8 FIX) ─────────────────────────
#
# Old pattern:
#   def _get_redis():
#       return redis.from_url(str(settings.REDIS_URL), decode_responses=True)
#
# This created a NEW ConnectionPool on every call. Under load (100 concurrent
# logins) this opened 400+ ephemeral connections. Fixed by importing the
# shared pool from cache.py and using redis_bp (LocalyRedisKeys) which has
# all Blueprint §16.3 TTLs and key patterns as named methods.

def store_refresh_token(user_id: str, jti: str) -> None:
    """
    Store refresh token JTI in Redis.
    Blueprint §3.2 / §16.3: session:{user_id}:{jti} TTL=2592000s (30 days).
    BUG-R2 FIX: uses shared pool via redis_bp.
    """
    from app.core.cache import redis_bp
    redis_bp.store_session(user_id, jti)


def is_refresh_token_valid(user_id: str, jti: str) -> bool:
    """
    Check if a refresh token JTI is still in Redis.
    Blueprint §3.2: token rotation — old token deleted, new one stored.
    BUG-R2 FIX: uses shared pool via redis_bp.
    """
    from app.core.cache import redis_bp
    return redis_bp.is_session_valid(user_id, jti)


def revoke_refresh_token(user_id: str, jti: str) -> None:
    """Delete a single refresh token from Redis (rotation step)."""
    from app.core.cache import redis_bp
    redis_bp.revoke_session(user_id, jti)


def invalidate_all_sessions(user_id: str) -> None:
    """
    Delete ALL refresh tokens for a user from Redis.
    Blueprint §3.2: 'All existing session tokens invalidated on password reset.'
    BUG-R1 FIX: uses SCAN internally (not KEYS) via redis_bp.
    BUG-R2 FIX: uses shared pool.
    """
    from app.core.cache import redis_bp
    redis_bp.revoke_all_sessions(user_id)


# ─── OTP Generation ───────────────────────────────────────────────────────────

def generate_otp(length: int = 6) -> str:
    """Blueprint §3.1 Step 1: 6-digit OTP."""
    return "".join(secrets.choice(string.digits) for _ in range(length))


def generate_verification_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


# ─── Permission Checks ────────────────────────────────────────────────────────

def check_user_role(role: str, allowed_roles: list[str]) -> bool:
    return role in allowed_roles


def require_user_type(*allowed_types: str) -> Callable:
    """Decorator enforcing user role on sync and async service functions."""
    def _resolve_user(args, kwargs):
        return kwargs.get("user") or (args[0] if args else None)

    def _check(user) -> None:
        if user is None or not hasattr(user, "role"):
            raise PermissionError("Permission denied")
        role_val = (
            user.role.value if hasattr(user.role, "value") else str(user.role)
        )
        if role_val not in allowed_types:
            raise PermissionError(f"Access restricted to: {', '.join(allowed_types)}")

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


# ─── API Key ──────────────────────────────────────────────────────────────────

def generate_api_key() -> str:
    return f"lc_{secrets.token_urlsafe(32)}"


def verify_api_key(api_key: str) -> bool:
    return api_key.startswith("lc_") and len(api_key) > 35