"""
app/services/auth_service.py

FIXES:
  BUG-R2 FIX: _redis() replaced with get_redis() / redis_bp from app.core.cache.
    Old code created a new ConnectionPool on every OTP send/verify call.
    Each registration, login, OTP resend, and password reset opened a brand new
    Redis connection. Under load this exhausts the Redis connection limit.
    Fix: all OTP operations go through redis_bp (LocalyRedisKeys) which uses
    the shared pool from cache.py.

  BUG-R8 FIX: All OTP key patterns and TTLs from Blueprint §16.3 now come from
    redis_bp methods — no hardcoded key strings.

  BUG-R1 FIX: invalidate_all_sessions now uses SCAN via redis_bp (not KEYS).

All other fixes from previous version retained:
  1.  register_user dispatches 4 mandatory post-registration Celery tasks.
  2.  issue_tokens builds correct Blueprint §3.2 JWT payload.
  3.  refresh_access_token rotates the refresh token.
  4.  authenticate_user: phone only. Blueprint §3.2.
  5.  reset_password invalidates all sessions. Blueprint §3.2.
  6.  All datetime: datetime.now(timezone.utc). Blueprint §16.4 HARD RULE.
"""
import logging
import uuid
from datetime import timedelta, timezone, datetime
from typing import Optional
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_otp,
    hash_password,
    hash_pin,
    invalidate_all_sessions,
    is_refresh_token_valid,
    revoke_refresh_token,
    store_refresh_token,
    TokenDecodeError,
    validate_password_strength,
    validate_pin,
    verify_pin,
)
from app.core.sms import sms_service
from app.core.exceptions import (
    InvalidCredentialsException,
    NotFoundException,
    ValidationException,
)
from app.crud.user_crud import user_crud
from app.models.user_model import User
from app.schemas.auth_schema import RegisterRequest

log = logging.getLogger(__name__)


# ─── OTP Rate Limiting (BUG-R2 + BUG-R8 FIX) ────────────────────────────────
#
# All OTP Redis operations now go through redis_bp (LocalyRedisKeys) from
# app.core.cache, which uses the shared ConnectionPool.
# Key patterns and TTLs are defined in cache.py as named constants.

def _otp_rate_limit_check(phone: str) -> None:
    """
    Enforce OTP attempt limits.
    Blueprint §3.1: max 5 OTP attempts per phone per hour.
    On 5 failures: phone locked for 30 minutes.
    BUG-R2 FIX: uses shared pool via redis_bp.
    """
    from app.core.cache import redis_bp

    if redis_bp.is_otp_locked(phone):
        raise ValidationException(
            "Too many OTP attempts. This phone number is locked for 30 minutes."
        )

    attempts = redis_bp.increment_otp_attempts(phone)

    if attempts > settings.OTP_MAX_ATTEMPTS:
        redis_bp.set_otp_lockout(phone)
        redis_bp.clear_otp_attempts(phone)
        raise ValidationException(
            "Too many OTP attempts. This phone number is locked for 30 minutes."
        )


def _otp_resend_check(phone: str) -> None:
    """
    Enforce 60-second cooldown between OTP sends.
    Blueprint §3.1 Step 1: 'Resend available after 60 seconds.'
    BUG-R2 FIX: uses shared pool via redis_bp.
    """
    from app.core.cache import redis_bp

    if redis_bp.is_otp_resend_blocked(phone):
        raise ValidationException(
            "Please wait 60 seconds before requesting another OTP."
        )
    redis_bp.set_otp_resend_cooldown(phone)


def _store_otp(phone: str, otp: str) -> None:
    """
    Store OTP in Redis. TTL = 5 minutes per Blueprint §3.1.
    BUG-R2 FIX: uses shared pool via redis_bp.
    """
    from app.core.cache import redis_bp
    redis_bp.store_otp(phone, otp)


def _verify_and_clear_otp(phone: str, otp: str) -> bool:
    """
    Verify OTP from Redis and delete it on success.
    Blueprint §3.1 Step 2: 'On success: Redis key deleted.'
    BUG-R2 FIX: uses shared pool via redis_bp.
    """
    from app.core.cache import redis_bp
    stored = redis_bp.get_otp(phone)
    if not stored or stored != otp:
        return False
    redis_bp.delete_otp(phone)
    return True


# ─── Token helpers ────────────────────────────────────────────────────────────

def _build_role(user: User) -> str:
    return user.role.value if hasattr(user.role, "value") else str(user.role)


def _build_business_id(user: User) -> Optional[str]:
    if user.business:
        return str(user.business.id)
    return None


def issue_tokens(user: User) -> dict:
    """
    Issue a new access + refresh token pair.
    Blueprint §3.2 JWT payload: {sub, role, business_id, iat, exp}
    Refresh token stored in Redis: session:{user_id}:{jti}  TTL=30 days.
    BUG-R2 FIX: store_refresh_token uses shared pool.
    """
    role        = _build_role(user)
    business_id = _build_business_id(user)
    user_id     = str(user.id)

    access_token = create_access_token(
        subject=user_id,
        role=role,
        business_id=business_id,
    )
    refresh_token, jti = create_refresh_token(subject=user_id, role=role)

    store_refresh_token(user_id, jti)

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
    }


def refresh_access_token(refresh_token_str: str) -> dict:
    """
    Validate refresh token, rotate it, return a new token pair.
    Blueprint §3.2: 'Token rotation — new refresh token issued on every use.'
    Old jti deleted from Redis, new jti stored.
    BUG-R2 FIX: all Redis ops use shared pool via security.py helpers.
    """
    try:
        payload = decode_token(refresh_token_str)
    except TokenDecodeError:
        raise ValidationException("Invalid or expired refresh token")

    if payload.get("type") != "refresh":
        raise ValidationException("Token is not a refresh token")

    user_id = payload.get("sub")
    jti     = payload.get("jti")
    role    = payload.get("role")

    if not user_id or not jti or not role:
        raise ValidationException("Malformed refresh token")

    if not is_refresh_token_valid(user_id, jti):
        raise ValidationException("Refresh token has been revoked or already used")

    revoke_refresh_token(user_id, jti)

    new_access, new_jti = create_access_token(subject=user_id, role=role, business_id=None), None
    new_access  = create_access_token(subject=user_id, role=role, business_id=None)
    new_refresh, new_jti = create_refresh_token(subject=user_id, role=role)
    store_refresh_token(user_id, new_jti)

    return {
        "access_token":  new_access,
        "refresh_token": new_refresh,
        "token_type":    "bearer",
    }


# ─── Registration ─────────────────────────────────────────────────────────────

def register_user(
    db: Session,
    reg: RegisterRequest,
    background_tasks: BackgroundTasks,
) -> User:
    """
    Complete multi-step registration.

    OTP rate-limit checked BEFORE user creation — if phone is locked,
    we don't create any DB record.

    Post-registration Celery tasks (Blueprint §3 POST-REGISTRATION):
      - create_wallet
      - assign_virtual_account
      - send_welcome_sms
      - send_welcome_push
    """
    _otp_rate_limit_check(reg.phone)
    _otp_resend_check(reg.phone)

    user = user_crud.create_user(db, obj_in=reg)

    otp = generate_otp(6)
    _store_otp(user.phone_number, otp)

    background_tasks.add_task(
        sms_service.send_otp,
        user.phone_number,
        otp,
        settings.OTP_EXPIRE_MINUTES,
    )

    from app.tasks.wallet_tasks import create_wallet, assign_virtual_account
    from app.tasks.notification_tasks import send_welcome_sms, send_welcome_push

    create_wallet.delay(str(user.id))
    assign_virtual_account.delay(str(user.id))
    send_welcome_sms.delay(str(user.id))
    send_welcome_push.delay(str(user.id))

    log.info(
        "User registered: id=%s role=%s | OTP sent to phone=%s",
        user.id, user.role, user.phone_number,
    )
    return user


# ─── OTP Verification ─────────────────────────────────────────────────────────

def verify_otp(db: Session, user: User, otp: str) -> None:
    """Blueprint §3.1 Step 2: verify Redis OTP, mark phone verified."""
    if not _verify_and_clear_otp(user.phone_number, otp):
        raise ValidationException("Invalid or expired OTP")
    user_crud.mark_phone_verified(db, user=user)


def resend_otp(db: Session, user: User, background_tasks: BackgroundTasks) -> None:
    """Resend OTP with 60-second cooldown and hourly limit. Blueprint §3.1 Step 1."""
    _otp_rate_limit_check(user.phone_number)
    _otp_resend_check(user.phone_number)

    otp = generate_otp(6)
    _store_otp(user.phone_number, otp)

    background_tasks.add_task(
        sms_service.send_otp,
        user.phone_number,
        otp,
        settings.OTP_EXPIRE_MINUTES,
    )


# ─── Login ────────────────────────────────────────────────────────────────────

def authenticate_user(db: Session, phone: str, password: str) -> User:
    """Phone + password login only. Blueprint §3.2."""
    user = user_crud.authenticate(db, phone=phone, password=password)
    if not user:
        raise InvalidCredentialsException()
    user = user_crud.get_with_profile(db, user_id=user.id)
    user_crud.update_last_login(db, user=user)
    return user


# ─── PIN Management ───────────────────────────────────────────────────────────

def setup_pin(db: Session, user: User, pin: str) -> None:
    """Set / update PIN from security settings. Blueprint §3.1 step 6 / §3.3."""
    if not validate_pin(pin):
        raise ValidationException("PIN must be exactly 4 digits")
    user_crud.set_pin(db, user=user, pin=pin)


def verify_pin_for_transaction(db: Session, user: User, pin: str) -> bool:
    """Blueprint §3.3: PIN required for ALL wallet transactions and withdrawals."""
    return user_crud.verify_pin_auth(db, user=user, pin=pin)


def change_pin(db: Session, user: User, old_pin: str, new_pin: str, otp: str) -> None:
    """Change PIN — requires current PIN + OTP. Blueprint §3.3."""
    if not _verify_and_clear_otp(user.phone_number, otp):
        raise ValidationException("Invalid or expired OTP")
    user_crud.change_pin(db, user=user, old_pin=old_pin, new_pin=new_pin)


def authenticate_with_pin(db: Session, phone: str, pin: str) -> User:
    """PIN login. Blueprint §3.2."""
    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        raise InvalidCredentialsException("Invalid credentials")
    if not user_crud.verify_pin_auth(db, user=user, pin=pin):
        raise InvalidCredentialsException("Invalid PIN")
    user = user_crud.get_with_profile(db, user_id=user.id)
    user_crud.update_last_login(db, user=user)
    return user


def request_pin_unlock(db: Session, phone: str, background_tasks: BackgroundTasks) -> None:
    """Send SMS unlock code for locked PIN. Blueprint §3.3."""
    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        return  # Silent — prevent enumeration

    otp  = generate_otp(6)
    _store_otp(user.phone_number, otp)
    name = user.full_name.split()[0] if user.full_name else "User"

    background_tasks.add_task(
        sms_service.send_pin_unlock, user.phone_number, name, otp
    )


def unlock_pin(db: Session, phone: str, otp: str) -> None:
    """Unlock PIN lockout using SMS OTP."""
    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        raise ValidationException("Invalid request")

    if not _verify_and_clear_otp(user.phone_number, otp):
        raise ValidationException("Invalid or expired OTP")

    user_crud.clear_pin_lockout(db, user=user)

    # Also clear Redis-level lockout
    from app.core.cache import redis_bp
    redis_bp.clear_pin_lockout(str(user.id))


# ─── Biometric ────────────────────────────────────────────────────────────────

def enable_biometric(db: Session, user: User) -> None:
    """Blueprint §3.1 step 7: only after PIN is set. Blueprint §3.3: never replaces PIN."""
    user_crud.enable_biometric(db, user=user)


def disable_biometric(db: Session, user: User) -> None:
    user_crud.disable_biometric(db, user=user)


# ─── Password Reset ───────────────────────────────────────────────────────────

def initiate_password_reset(
    db: Session, phone: str, background_tasks: BackgroundTasks
) -> None:
    """Send password-reset OTP to phone. Blueprint §3.2: phone only."""
    _otp_rate_limit_check(phone)
    _otp_resend_check(phone)

    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        return  # Silent

    otp = generate_otp(6)
    _store_otp(user.phone_number, otp)

    background_tasks.add_task(
        sms_service.send_password_reset, user.phone_number, otp
    )


def verify_reset_otp_and_issue_token(db: Session, phone: str, otp: str) -> str:
    """Validate reset OTP and issue a short-lived reset_token (JWT, 15 min)."""
    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        raise ValidationException("Invalid request")

    if not _verify_and_clear_otp(user.phone_number, otp):
        raise ValidationException("Invalid or expired OTP")

    return create_access_token(
        subject=str(user.id),
        role=_build_role(user),
        business_id=None,
        expires_delta=timedelta(minutes=15),
    )


def reset_password(db: Session, reset_token: str, new_password: str) -> None:
    """Set new password and invalidate ALL existing sessions. Blueprint §3.2."""
    try:
        payload = decode_token(reset_token)
        user_id = UUID(payload["sub"])
    except (TokenDecodeError, KeyError, ValueError):
        raise ValidationException("Invalid or expired reset token")

    if not validate_password_strength(new_password):
        raise ValidationException(
            "Password must be at least 8 characters with uppercase, lowercase, and a digit."
        )

    user = user_crud.get(db, id=user_id)
    if not user:
        raise NotFoundException("User")

    user_crud.reset_password(db, user=user, new_password=new_password)
    invalidate_all_sessions(str(user_id))


# ─── Singleton ────────────────────────────────────────────────────────────────

class _AuthService:
    register_user                    = staticmethod(register_user)
    authenticate_user                = staticmethod(authenticate_user)
    verify_otp                       = staticmethod(verify_otp)
    resend_otp                       = staticmethod(resend_otp)
    issue_tokens                     = staticmethod(issue_tokens)
    refresh_access_token             = staticmethod(refresh_access_token)
    setup_pin                        = staticmethod(setup_pin)
    verify_pin_for_transaction       = staticmethod(verify_pin_for_transaction)
    change_pin                       = staticmethod(change_pin)
    authenticate_with_pin            = staticmethod(authenticate_with_pin)
    request_pin_unlock               = staticmethod(request_pin_unlock)
    unlock_pin                       = staticmethod(unlock_pin)
    enable_biometric                 = staticmethod(enable_biometric)
    disable_biometric                = staticmethod(disable_biometric)
    initiate_password_reset          = staticmethod(initiate_password_reset)
    verify_reset_otp_and_issue_token = staticmethod(verify_reset_otp_and_issue_token)
    reset_password                   = staticmethod(reset_password)


auth_service = _AuthService()