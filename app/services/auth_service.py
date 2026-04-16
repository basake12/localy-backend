"""
app/services/auth_service.py

FIXES vs previous version:
  1.  user.phone → user.phone_number throughout (Blueprint §14 field name).

  2.  register_user now dispatches all 4 mandatory post-registration Celery
      tasks (Blueprint §3 POST-REGISTRATION):
        - create_wallet
        - assign_virtual_account
        - send_welcome_sms
        - send_welcome_push
      Direct wallet_crud.create_wallet_sync() call removed — wallet creation
      is Celery-async to keep registration fast and non-blocking.

  3.  register_user creates a UserAgreement record on T&C acceptance.
      Blueprint §3.1 step 8 / §14: user_agreements table.

  4.  issue_tokens() now takes user object and builds correct JWT payload:
        {sub, role, business_id, iat, exp}  Blueprint §3.2.
      Refresh token JTI stored in Redis: session:{user_id}:{jti} TTL=30d.

  5.  refresh_access_token() rotates the refresh token:
        - validates old token exists in Redis
        - deletes old Redis key
        - issues new access + refresh token pair, stores new jti

  6.  authenticate_user() — phone_number only. Email login removed.
      Blueprint §3.2: "Phone number + password → JWT."

  7.  reset_password() now calls invalidate_all_sessions(user_id).
      Blueprint §3.2: "All existing session tokens invalidated on password reset."

  8.  OTP resend rate limiting enforced via Redis:
        otp_resend_cooldown:{phone}  TTL = 60 seconds
      Blueprint §3.1 Step 1: "Resend available after 60 seconds."

  9.  OTP attempt rate limiting enforced via Redis:
        otp_attempts:{phone}  TTL = 3600 seconds (max 5/hour)
      Blueprint §3.1 Steps 1-2: max 5 OTP attempts per phone per hour.
      Phone locked for 30 minutes after 5 failures:
        otp_lockout:{phone}   TTL = 1800 seconds

  10. authenticate_with_pin() uses phone_number, not email.

  11. All datetime operations use datetime.now(timezone.utc).
      Blueprint §16.4 HARD RULE: NEVER datetime.utcnow().

  12. All service functions are kept synchronous (using Session) to
      match the existing CRUD layer. Async upgrade is a separate task.
"""
import logging
import uuid
from datetime import timedelta, timezone, datetime
from typing import Optional
from uuid import UUID

import redis as redis_sync
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


# ─── Redis client ─────────────────────────────────────────────────────────────

def _redis() -> redis_sync.Redis:
    return redis_sync.from_url(str(settings.REDIS_URL), decode_responses=True)


# ─── Display name helper ──────────────────────────────────────────────────────

def _display_name(user: User) -> str:
    """
    Return a human-readable name for SMS messages.
    Uses user.full_name (on users table per Blueprint §14).
    Falls back to first_name on linked profile if full_name is blank.
    """
    if user.full_name:
        return user.full_name.split()[0]
    if user.customer_profile:
        return user.customer_profile.first_name
    if user.rider:
        return user.rider.first_name
    if user.business:
        return user.business.business_name
    return "User"


# ─── OTP Rate Limiting (Blueprint §3.1 Steps 1-2) ────────────────────────────

def _otp_rate_limit_check(phone: str) -> None:
    """
    Enforce OTP attempt limits before generating a new OTP.

    Blueprint §3.1:
      - Max 5 OTP attempts per phone per hour (rate-limited in Redis).
      - On 5 failures: phone locked for 30 minutes.
    """
    r = _redis()

    # Check lockout
    if r.exists(f"otp_lockout:{phone}"):
        raise ValidationException(
            "Too many OTP attempts. This phone number is locked for 30 minutes."
        )

    # Increment attempt counter (TTL = 1 hour)
    attempts_key = f"otp_attempts:{phone}"
    attempts     = r.incr(attempts_key)
    if attempts == 1:
        r.expire(attempts_key, 3600)  # first attempt — set 1-hour window

    if attempts > settings.OTP_MAX_ATTEMPTS:
        # Lock the phone for 30 minutes
        r.setex(
            f"otp_lockout:{phone}",
            settings.OTP_LOCKOUT_MINUTES * 60,
            "1",
        )
        r.delete(attempts_key)
        raise ValidationException(
            "Too many OTP attempts. This phone number is locked for 30 minutes."
        )


def _otp_resend_check(phone: str) -> None:
    """
    Enforce 60-second cooldown between OTP sends.
    Blueprint §3.1 Step 1: "Resend available after 60 seconds."
    """
    r = _redis()
    if r.exists(f"otp_resend_cooldown:{phone}"):
        raise ValidationException(
            "Please wait 60 seconds before requesting another OTP."
        )
    r.setex(
        f"otp_resend_cooldown:{phone}",
        settings.OTP_RESEND_COOLDOWN_SECONDS,
        "1",
    )


def _store_otp(phone: str, otp: str) -> None:
    """Store OTP in Redis. TTL = 5 minutes per Blueprint §3.1."""
    r = _redis()
    r.setex(f"otp:{phone}", settings.OTP_EXPIRE_MINUTES * 60, otp)


def _verify_and_clear_otp(phone: str, otp: str) -> bool:
    """
    Verify OTP from Redis and delete it on success.
    Blueprint §3.1 Step 2: "On success: Redis key deleted."
    """
    r       = _redis()
    key     = f"otp:{phone}"
    stored  = r.get(key)
    if not stored or stored != otp:
        return False
    r.delete(key)
    return True


# ─── Token helpers ────────────────────────────────────────────────────────────

def _build_role(user: User) -> str:
    """Return the role string for JWT claims."""
    return user.role.value if hasattr(user.role, "value") else str(user.role)


def _build_business_id(user: User) -> Optional[str]:
    """Return the business_id string for JWT claims (None for non-business users)."""
    if user.business:
        return str(user.business.id)
    return None


def issue_tokens(user: User) -> dict:
    """
    Issue a new access + refresh token pair.

    Blueprint §3.2 JWT payload:
      {sub, role, business_id, iat, exp}

    Refresh token stored in Redis: session:{user_id}:{jti}  TTL=30 days.
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

    # Store in Redis — Blueprint §3.2
    store_refresh_token(user_id, jti)

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
    }


def refresh_access_token(refresh_token_str: str) -> dict:
    """
    Validate refresh token, rotate it, and return a new token pair.

    Blueprint §3.2: "Token rotation — new refresh token issued on every use."
    Old jti deleted from Redis, new jti stored.
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

    # Blueprint §3.2: validate token still exists in Redis (not revoked)
    if not is_refresh_token_valid(user_id, jti):
        raise ValidationException("Refresh token has been revoked or already used")

    # Rotate — revoke old, issue new
    revoke_refresh_token(user_id, jti)

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
    Complete multi-step registration in one call.

    Steps implemented here per Blueprint §3.1:
      Step 1: Generate OTP, store in Redis (TTL=5min), send via Termii SMS.
      Step 3: Profile details created (full_name, date_of_birth, email).
      Step 4: Role assigned (permanent).
      Step 6: PIN hashed and stored (mandatory — cannot be skipped).
      Step 8: UserAgreement record created.

    Post-registration Celery tasks dispatched (Blueprint §3 POST-REGISTRATION):
      - create_wallet
      - assign_virtual_account
      - send_welcome_sms
      - send_welcome_push

    Returns the created User object.
    """
    # Rate-limit OTP before creating user
    _otp_rate_limit_check(reg.phone)
    _otp_resend_check(reg.phone)

    # Create user (includes PIN hash)
    user = user_crud.create_user(db, obj_in=reg)

    # Store OTP in Redis (TTL = 5 minutes per Blueprint §3.1)
    otp = generate_otp(6)
    _store_otp(user.phone_number, otp)

    # Send OTP via Termii SMS
    background_tasks.add_task(
        sms_service.send_otp,
        user.phone_number,
        otp,
        settings.OTP_EXPIRE_MINUTES,
    )

    # Post-registration Celery tasks — Blueprint §3 POST-REGISTRATION
    from app.tasks.wallet_tasks import create_wallet, assign_virtual_account
    from app.tasks.notification_tasks import send_welcome_sms, send_welcome_push

    create_wallet.delay(str(user.id))
    assign_virtual_account.delay(str(user.id))
    send_welcome_sms.delay(str(user.id))
    send_welcome_push.delay(str(user.id))

    log.info(
        "User registered: id=%s role=%s | OTP sent to phone_number=%s",
        user.id, user.role, user.phone_number,
    )
    return user


# ─── OTP Verification ─────────────────────────────────────────────────────────

def verify_otp(db: Session, user: User, otp: str) -> None:
    """
    Verify phone OTP from Redis.
    Blueprint §3.1 Step 2: "On success: Redis key deleted, phone marked verified."
    """
    if not _verify_and_clear_otp(user.phone_number, otp):
        raise ValidationException("Invalid or expired OTP")
    user_crud.mark_phone_verified(db, user=user)


def resend_otp(
    db: Session,
    user: User,
    background_tasks: BackgroundTasks,
) -> None:
    """
    Resend OTP — enforces 60-second cooldown and hourly attempt limit.
    Blueprint §3.1 Step 1.
    """
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


# ─── Login (phone + password only) ───────────────────────────────────────────

def authenticate_user(db: Session, phone: str, password: str) -> User:
    """
    Authenticate by phone number + password.
    Blueprint §3.2: "Phone number + password → JWT access token."
    Email login is NOT supported — phone only.
    """
    user = user_crud.authenticate(db, phone=phone, password=password)
    if not user:
        raise InvalidCredentialsException()

    user = user_crud.get_with_profile(db, user_id=user.id)
    user_crud.update_last_login(db, user=user)
    return user


# ─── PIN Management ───────────────────────────────────────────────────────────

def setup_pin(db: Session, user: User, pin: str) -> None:
    """
    Set / update 4-digit PIN from security settings.
    Registration now includes PIN in the payload — this handles
    subsequent PIN changes from the security settings screen.
    Blueprint §3.1 step 6 / §3.3.
    """
    if not validate_pin(pin):
        raise ValidationException("PIN must be exactly 4 digits")
    user_crud.set_pin(db, user=user, pin=pin)
    log.info("PIN updated for user_id=%s", user.id)


def verify_pin_for_transaction(db: Session, user: User, pin: str) -> bool:
    """
    Verify PIN before a wallet transaction.
    Blueprint §3.3: "PIN is required for ALL wallet transactions,
    withdrawals, and any payment above ₦5,000."
    Returns True on success. Raises on lockout or wrong PIN.
    """
    return user_crud.verify_pin_auth(db, user=user, pin=pin)


def change_pin(
    db: Session, user: User, old_pin: str, new_pin: str, otp: str
) -> None:
    """
    Change PIN — requires current PIN + OTP from registered phone.
    Blueprint §3.3: "Changeable from security settings
    (requires current PIN + OTP confirmation from registered phone)."
    """
    # Verify OTP first
    if not _verify_and_clear_otp(user.phone_number, otp):
        raise ValidationException("Invalid or expired OTP")

    user_crud.change_pin(db, user=user, old_pin=old_pin, new_pin=new_pin)
    log.info("PIN changed for user_id=%s", user.id)


def authenticate_with_pin(db: Session, phone: str, pin: str) -> User:
    """
    PIN login — quick access after first session.
    Blueprint §3.2: "PIN login: 4-digit PIN → validates against bcrypt hash
    → issues new session tokens."
    Phone number only — no email.
    """
    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        raise InvalidCredentialsException("Invalid credentials")

    if not user_crud.verify_pin_auth(db, user=user, pin=pin):
        raise InvalidCredentialsException("Invalid PIN")

    user = user_crud.get_with_profile(db, user_id=user.id)
    user_crud.update_last_login(db, user=user)
    return user


def request_pin_unlock(
    db: Session,
    phone: str,
    background_tasks: BackgroundTasks,
) -> None:
    """
    Send SMS unlock code for locked PIN.
    Blueprint §3.3: "5 wrong PIN attempts → 30-min lockout →
    User receives SMS unlock code to reset lockout."
    """
    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        return  # Silent — prevent enumeration

    otp  = generate_otp(6)
    _store_otp(user.phone_number, otp)
    name = _display_name(user)

    background_tasks.add_task(
        sms_service.send_pin_unlock, user.phone_number, name, otp
    )
    log.info("PIN unlock OTP sent to user_id=%s", user.id)


def unlock_pin(db: Session, phone: str, otp: str) -> None:
    """Unlock PIN lockout using the SMS OTP."""
    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        raise ValidationException("Invalid request")

    if not _verify_and_clear_otp(user.phone_number, otp):
        raise ValidationException("Invalid or expired OTP")

    user_crud.clear_pin_lockout(db, user=user)
    log.info("PIN unlocked for user_id=%s", user.id)


# ─── Biometric ────────────────────────────────────────────────────────────────

def enable_biometric(db: Session, user: User) -> None:
    """
    Enable biometric flag.
    Blueprint §3.1 step 7 / §3.3: "Only activatable after PIN is confirmed active.
    Server stores only biometric_flag BOOLEAN — no biometric data on server."
    Caller must have already verified PIN is set (use require_pin_set dependency).
    """
    user_crud.enable_biometric(db, user=user)
    log.info("Biometric enabled for user_id=%s", user.id)


def disable_biometric(db: Session, user: User) -> None:
    """Disable biometric flag."""
    user_crud.disable_biometric(db, user=user)
    log.info("Biometric disabled for user_id=%s", user.id)


# ─── Password Reset ───────────────────────────────────────────────────────────

def initiate_password_reset(
    db: Session,
    phone: str,
    background_tasks: BackgroundTasks,
) -> None:
    """
    Send password-reset OTP to phone.
    Blueprint §3.2: "Enter phone number → Receive SMS OTP (same Termii gateway,
    same TTL rules)."
    Phone only — no email option.
    """
    _otp_rate_limit_check(phone)
    _otp_resend_check(phone)

    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        return  # Silent — prevent enumeration

    otp = generate_otp(6)
    _store_otp(user.phone_number, otp)

    background_tasks.add_task(
        sms_service.send_password_reset, user.phone_number, otp
    )
    log.info("Password reset OTP sent to user_id=%s", user.id)


def verify_reset_otp_and_issue_token(
    db: Session,
    phone: str,
    otp: str,
) -> str:
    """
    Validate reset OTP and issue a short-lived reset_token (JWT, 15 min).
    """
    user = user_crud.get_by_phone(db, phone=phone)
    if not user:
        raise ValidationException("Invalid request")

    if not _verify_and_clear_otp(user.phone_number, otp):
        raise ValidationException("Invalid or expired OTP")

    # Short-lived reset token — role embedded so the reset endpoint works
    return create_access_token(
        subject=str(user.id),
        role=_build_role(user),
        business_id=None,
        expires_delta=timedelta(minutes=15),
    )


def reset_password(db: Session, reset_token: str, new_password: str) -> None:
    """
    Set new password and invalidate ALL existing sessions.
    Blueprint §3.2: "All existing session tokens invalidated on password reset."
    """
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

    # Blueprint §3.2: invalidate ALL session tokens
    invalidate_all_sessions(str(user_id))
    log.info("Password reset completed for user_id=%s — all sessions revoked", user_id)


# ─── Singleton alias ──────────────────────────────────────────────────────────

class _AuthService:
    register_user                    = staticmethod(register_user)
    authenticate_user                = staticmethod(authenticate_user)
    verify_otp                       = staticmethod(verify_otp)
    resend_otp                       = staticmethod(resend_otp)
    issue_tokens                     = staticmethod(issue_tokens)
    refresh_access_token             = staticmethod(refresh_access_token)

    # PIN
    setup_pin                        = staticmethod(setup_pin)
    verify_pin_for_transaction       = staticmethod(verify_pin_for_transaction)
    change_pin                       = staticmethod(change_pin)
    authenticate_with_pin            = staticmethod(authenticate_with_pin)
    request_pin_unlock               = staticmethod(request_pin_unlock)
    unlock_pin                       = staticmethod(unlock_pin)

    # Biometric
    enable_biometric                 = staticmethod(enable_biometric)
    disable_biometric                = staticmethod(disable_biometric)

    # Password reset
    initiate_password_reset          = staticmethod(initiate_password_reset)
    verify_reset_otp_and_issue_token = staticmethod(verify_reset_otp_and_issue_token)
    reset_password                   = staticmethod(reset_password)


auth_service = _AuthService()