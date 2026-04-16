"""
app/core/security.py

FIXES vs previous version:
  1.  [HARD RULE] pin_context changed from argon2 → bcrypt.
      Blueprint §3.1 step 6 / §3.3: "PIN is hashed with bcrypt before storage."
      argon2 is kept for passwords (secure against brute-force with known hashes).
      bcrypt is used for PINs (4-digit, so the cost factor controls exposure).

  2.  create_access_token now requires `role` and `business_id` arguments.
      Blueprint §3.2 JWT payload:
        {sub: user_id, role: "customer|business|rider",
         business_id: uuid|null, iat: timestamp, exp: timestamp}

  3.  create_refresh_token now includes `jti` (JWT ID) — a unique token
      identifier used as the Redis key suffix:
        session:{user_id}:{jti}  TTL = 30 days
      Required for:
        - Token rotation on every use (Blueprint §3.2)
        - All-token invalidation on password reset (Blueprint §3.2)

  4.  store_refresh_token() and revoke_refresh_token() Redis helpers added
      to implement the session:{user_id}:{token_id} storage pattern.

  5.  invalidate_all_sessions(user_id) added — scans and deletes all
      session:{user_id}:* keys from Redis on password reset.

  6.  require_user_type decorator updated to use `user.role` (not user_type).

  7.  All datetime operations use datetime.now(timezone.utc) — NEVER utcnow().
      Blueprint §16.4 HARD RULE.
"""
import asyncio
import logging
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable, Optional, Union

import redis as redis_sync
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

log = logging.getLogger(__name__)

# ─── Password Hashing (argon2 — brute-force resistant) ────────────────────────

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash plain-text password using argon2."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against its argon2 hash."""
    return pwd_context.verify(plain_password, hashed_password)


def validate_password_strength(password: str) -> bool:
    """
    Return True if password meets minimum requirements:
      ≥ 8 chars, one uppercase, one lowercase, one digit.
    """
    min_len = getattr(settings, "PASSWORD_MIN_LENGTH", 8)
    if len(password) < min_len:
        return False
    return (
        any(c.isupper() for c in password)
        and any(c.islower() for c in password)
        and any(c.isdigit() for c in password)
    )


# ─── PIN Hashing (bcrypt — Blueprint §3.1 step 6 / §3.3) ─────────────────────

# Blueprint: "PIN is hashed with bcrypt before storage. Never stored in plaintext."
# argon2 is intentionally NOT used here — the blueprint explicitly says bcrypt.
pin_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_pin(pin: str) -> str:
    """
    Hash a 4-digit PIN using bcrypt.
    Blueprint §3.1 step 6: stored in users.pin_hash (TEXT NOT NULL).
    """
    if not validate_pin(pin):
        raise ValueError("PIN must be exactly 4 digits")
    return pin_context.hash(pin)


def verify_pin(plain_pin: str, hashed_pin: str) -> bool:
    """Verify plain 4-digit PIN against its bcrypt hash."""
    return pin_context.verify(plain_pin, hashed_pin)


def validate_pin(pin: str) -> bool:
    """Return True if PIN is exactly 4 numeric digits."""
    return isinstance(pin, str) and len(pin) == 4 and pin.isdigit()


# ─── UTC helper ───────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    """Timezone-aware UTC now. Blueprint §16.4 HARD RULE: NEVER datetime.utcnow()."""
    return datetime.now(timezone.utc)


# ─── JWT ──────────────────────────────────────────────────────────────────────

def create_access_token(
    subject: Union[str, Any],
    role: str,
    business_id: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token.

    Blueprint §3.2 payload:
      {
        sub:         user_id (str UUID),
        role:        "customer" | "business" | "rider",
        business_id: str UUID | null,
        iat:         issued-at timestamp,
        exp:         expiry timestamp,
        type:        "access"
      }

    Args:
        subject:     User UUID as string.
        role:        User role ("customer", "business", "rider").
        business_id: Business UUID as string, or None for non-business users.
        expires_delta: Override default 15-minute expiry.

    Returns:
        Signed JWT string.
    """
    now    = _utcnow()
    expire = now + (
        expires_delta
        if expires_delta is not None
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
    """
    Create a signed JWT refresh token with a unique jti.

    Blueprint §3.2:
      - Stored in Redis: session:{user_id}:{jti}  TTL = 30 days
      - Rotated on every use (new jti on each refresh)
      - All tokens invalidated on password reset

    Returns:
        (token_str, jti) — caller must store jti in Redis.
    """
    now    = _utcnow()
    jti    = str(uuid.uuid4())
    expire = now + (
        expires_delta
        if expires_delta is not None
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
    """
    Decode and validate a JWT.

    Returns decoded payload dict.
    Raises TokenDecodeError on invalid/expired/malformed token.
    Framework-agnostic — callers translate to HTTPException(401).
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
    """Safely decode token and return user_id (sub claim). Returns None on failure."""
    try:
        payload = decode_token(token)
        return payload.get("sub")
    except TokenDecodeError:
        return None


# ─── Redis Session Management ─────────────────────────────────────────────────
# Blueprint §3.2: refresh tokens stored in Redis as session:{user_id}:{token_id}

def _get_redis() -> redis_sync.Redis:
    """Get a synchronous Redis client from settings URL."""
    return redis_sync.from_url(
        str(settings.REDIS_URL),
        decode_responses=True,
    )


def store_refresh_token(user_id: str, jti: str) -> None:
    """
    Store refresh token JTI in Redis.
    Blueprint §3.2: key = session:{user_id}:{jti}  TTL = 30 days
    """
    r   = _get_redis()
    key = f"session:{user_id}:{jti}"
    ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400  # days → seconds
    r.setex(key, ttl, "1")


def is_refresh_token_valid(user_id: str, jti: str) -> bool:
    """
    Check if a refresh token JTI is still in Redis (not rotated or revoked).
    Blueprint §3.2: token rotation — old token deleted, new one stored.
    """
    r   = _get_redis()
    key = f"session:{user_id}:{jti}"
    return r.exists(key) == 1


def revoke_refresh_token(user_id: str, jti: str) -> None:
    """Delete a single refresh token from Redis (rotation step)."""
    r   = _get_redis()
    r.delete(f"session:{user_id}:{jti}")


def invalidate_all_sessions(user_id: str) -> None:
    """
    Delete ALL refresh tokens for a user from Redis.
    Blueprint §3.2: "All existing session tokens invalidated on password reset."
    Uses SCAN to avoid blocking the Redis server on large key sets.
    """
    r       = _get_redis()
    pattern = f"session:{user_id}:*"
    cursor  = 0
    while True:
        cursor, keys = r.scan(cursor=cursor, match=pattern, count=100)
        if keys:
            r.delete(*keys)
        if cursor == 0:
            break


# ─── OTP Generation ───────────────────────────────────────────────────────────

def generate_otp(length: int = 6) -> str:
    """
    Generate a cryptographically secure numeric OTP.
    Blueprint §3.1 Step 1: "OTP is 6-digit."
    """
    return "".join(secrets.choice(string.digits) for _ in range(length))


def generate_verification_token(length: int = 32) -> str:
    """Generate a secure URL-safe random token."""
    return secrets.token_urlsafe(length)


# ─── Permission Checks ────────────────────────────────────────────────────────

def check_user_role(role: str, allowed_roles: list[str]) -> bool:
    """Return True if role is in allowed_roles."""
    return role in allowed_roles


def require_user_type(*allowed_types: str) -> Callable:
    """
    Decorator that enforces user role on sync and async service functions.
    The first positional arg (or 'user' kwarg) must have a .role attribute.

    NOTE: For FastAPI endpoints, use the get_current_*_user dependencies instead.
    """
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


# ─── API Key Generation ───────────────────────────────────────────────────────

def generate_api_key() -> str:
    """Generate a secure API key prefixed 'lc_' for business integrations."""
    return f"lc_{secrets.token_urlsafe(32)}"


def verify_api_key(api_key: str) -> bool:
    """Return True if api_key has valid Localy format."""
    return api_key.startswith("lc_") and len(api_key) > 35