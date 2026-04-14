"""
Authentication service for Localy.

Blueprint v2.0 changes:
- OAuth services removed
- PIN setup/verify/change services added
- Biometric enable/disable services added
"""

import logging
from datetime import timedelta
from typing import Optional
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_otp,
    hash_password,
    validate_password_strength,
    decode_token,
)
from app.core.email import email_service
from app.core.sms import sms_service
from app.core.exceptions import (
    InvalidCredentialsException,
    NotFoundException,
    ValidationException,
    AlreadyExistsException,
)
from app.core.constants import UserType, UserStatus
from app.crud.user_crud import user_crud
from app.crud.wallet_crud import wallet_crud
from app.models.user_model import User
from app.schemas.auth_schema import RegisterRequest

log = logging.getLogger(__name__)


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _get_display_name(user: User) -> str:
    if user.customer_profile:
        return user.customer_profile.first_name
    if user.rider:
        return user.rider.first_name
    if user.business:
        return user.business.business_name
    if user.admin:
        return user.admin.full_name
    return user.email.split("@")[0]


def _dispatch_otp(
    user: User,
    otp: str,
    channel: str,
    background_tasks: BackgroundTasks,
) -> None:
    name = _get_display_name(user)
    if channel in ("email", "both") and user.email:
        background_tasks.add_task(
            email_service.send_email_otp, user.email, name, otp
        )
    if channel in ("phone", "both") and user.phone:
        background_tasks.add_task(sms_service.send_otp, user.phone, otp)


# ─── Token helpers ────────────────────────────────────────────────────────────

def issue_tokens(user_id: str) -> dict:
    return {
        "access_token":  create_access_token(subject=user_id),
        "refresh_token": create_refresh_token(subject=user_id),
        "token_type":    "bearer",
    }


def refresh_access_token(refresh_token: str) -> dict:
    """Validate the refresh token and return a new access + refresh token pair."""
    try:
        payload = decode_token(refresh_token)
    except Exception:
        raise ValidationException("Invalid or expired refresh token")

    if payload.get("type") != "refresh":
        raise ValidationException("Token is not a refresh token")

    user_id = payload.get("sub")
    if not user_id:
        raise ValidationException("Malformed token")

    return {
        "access_token":  create_access_token(subject=user_id),
        "refresh_token": create_refresh_token(subject=user_id),
        "token_type":    "bearer",
    }


# ─── Registration ─────────────────────────────────────────────────────────────

def register_user(
    db: Session,
    reg: RegisterRequest,
    background_tasks: BackgroundTasks,
    otp_channel: str = "both",
) -> tuple[User, str]:
    user = user_crud.create_user(db, obj_in=reg)

    # Create wallet for user
    wallet_crud.create_wallet_sync(db, user_id=user.id)

    otp     = user.phone_verification_otp
    channel = otp_channel or "both"
    _dispatch_otp(user, otp, channel, background_tasks)

    log.info("User registered: id=%s type=%s", user.id, user.user_type)
    return user, channel


# ─── Login ────────────────────────────────────────────────────────────────────

def authenticate_user(db: Session, identifier: str, password: str) -> User:
    import re
    if re.match(r"[^@]+@[^@]+\.[^@]+", identifier):
        user = user_crud.authenticate(db, email=identifier.lower(), password=password)
    else:
        user = user_crud.authenticate(db, phone=identifier, password=password)

    if not user:
        raise InvalidCredentialsException()

    user = user_crud.get_with_profile(db, user_id=user.id)
    user_crud.update_last_login(db, user=user)
    return user


# ─── PIN Management (Blueprint v2.0) ──────────────────────────────────────────

def setup_pin(db: Session, user: User, pin: str) -> None:
    """
    Set up 4-digit PIN for user.
    
    Blueprint: "Set 4-digit transaction PIN (mandatory — enables wallet and payments)"
    """
    user_crud.set_pin(db, user=user, pin=pin)
    log.info("PIN set for user_id=%s", user.id)


def verify_pin(db: Session, user: User, pin: str) -> bool:
    """
    Verify PIN for wallet transactions or PIN login.
    
    Blueprint: "PIN is required for all wallet transactions, withdrawals, 
    and payments above ₦5,000"
    
    Returns True if PIN is correct.
    Raises ValidationException if account is locked or PIN is wrong.
    """
    return user_crud.verify_pin_auth(db, user=user, pin=pin)


def change_pin(db: Session, user: User, old_pin: str, new_pin: str) -> None:
    """Change user's PIN — requires old PIN for security."""
    user_crud.change_pin(db, user=user, old_pin=old_pin, new_pin=new_pin)
    log.info("PIN changed for user_id=%s", user.id)


def authenticate_with_pin(db: Session, identifier: str, pin: str) -> User:
    """
    PIN login for quick access.
    
    Blueprint: "PIN login (quick access after first session)"
    """
    import re
    if re.match(r"[^@]+@[^@]+\.[^@]+", identifier):
        user = user_crud.get_by_email(db, email=identifier.lower())
    else:
        user = user_crud.get_by_phone(db, phone=identifier)

    if not user:
        raise InvalidCredentialsException("Invalid credentials")

    # Verify PIN (handles lockout logic)
    if not user_crud.verify_pin_auth(db, user=user, pin=pin):
        raise InvalidCredentialsException("Invalid PIN")

    user = user_crud.get_with_profile(db, user_id=user.id)
    user_crud.update_last_login(db, user=user)
    return user


def request_pin_unlock(
    db: Session,
    identifier: str,
    background_tasks: BackgroundTasks,
) -> None:
    """
    Send SMS unlock code for locked PIN.
    
    Blueprint: "5 wrong PIN attempts → 30-minute lockout → SMS unlock code"
    """
    import re
    if re.match(r"[^@]+@[^@]+\.[^@]+", identifier):
        user = user_crud.get_by_email(db, email=identifier.lower())
    else:
        user = user_crud.get_by_phone(db, phone=identifier)

    if not user:
        # Silent failure to prevent enumeration
        return

    otp = user_crud.set_password_reset_otp(db, user=user)
    name = _get_display_name(user)
    
    background_tasks.add_task(
        sms_service.send_pin_unlock, user.phone, name, otp
    )
    log.info("PIN unlock code sent to user_id=%s", user.id)


def unlock_pin_with_otp(db: Session, identifier: str, otp: str) -> None:
    """Unlock PIN using SMS OTP."""
    import re
    if re.match(r"[^@]+@[^@]+\.[^@]+", identifier):
        user = user_crud.get_by_email(db, email=identifier.lower())
    else:
        user = user_crud.get_by_phone(db, phone=identifier)

    if not user:
        raise ValidationException("Invalid request")

    user_crud.unlock_pin_with_otp(db, user=user, otp=otp)
    log.info("PIN unlocked for user_id=%s", user.id)


# ─── Biometric (Blueprint v2.0) ───────────────────────────────────────────────

def enable_biometric(db: Session, user: User) -> None:
    """
    Enable biometric authentication.
    
    Blueprint: "Optional: enable biometric authentication (Face ID / fingerprint) 
    after PIN is set"
    """
    user_crud.enable_biometric(db, user=user)
    log.info("Biometric enabled for user_id=%s", user.id)


def disable_biometric(db: Session, user: User) -> None:
    """Disable biometric authentication."""
    user_crud.disable_biometric(db, user=user)
    log.info("Biometric disabled for user_id=%s", user.id)


# ─── OTP Verification ─────────────────────────────────────────────────────────

def verify_otp(db: Session, user: User, otp: str, channel: str) -> None:
    success = user_crud.verify_otp_code(db, user=user, otp=otp, channel=channel)
    if not success:
        raise ValidationException("Invalid or expired OTP")


def resend_otp(
    db: Session,
    user: User,
    channel: str,
    background_tasks: BackgroundTasks,
) -> None:
    otp = user_crud.regenerate_otp(db, user=user)
    _dispatch_otp(user, otp, channel, background_tasks)


# ─── Password Reset ───────────────────────────────────────────────────────────

def initiate_password_reset(
    db: Session,
    email: Optional[str],
    phone: Optional[str],
    channel: str,
    background_tasks: BackgroundTasks,
) -> None:
    user: Optional[User] = None
    if email:
        user = user_crud.get_by_email(db, email=email)
    elif phone:
        user = user_crud.get_by_phone(db, phone=phone)

    if not user:
        return  # Silent — prevents enumeration

    otp              = user_crud.set_password_reset_otp(db, user=user)
    name             = _get_display_name(user)
    resolved_channel = channel or ("email" if email else "phone")

    if resolved_channel in ("email", "both") and user.email:
        background_tasks.add_task(
            email_service.send_password_reset_otp, user.email, name, otp
        )
    if resolved_channel in ("phone", "both") and user.phone:
        background_tasks.add_task(sms_service.send_password_reset, user.phone, otp)


def verify_reset_otp_and_issue_token(
    db: Session,
    email: Optional[str],
    phone: Optional[str],
    otp: str,
) -> str:
    user: Optional[User] = None
    if email:
        user = user_crud.get_by_email(db, email=email)
    elif phone:
        user = user_crud.get_by_phone(db, phone=phone)

    if not user:
        raise ValidationException("Invalid request")

    if not user_crud.check_password_reset_otp(db, user=user, otp=otp):
        raise ValidationException("Invalid or expired OTP")

    return create_access_token(
        subject=str(user.id),
        expires_delta=timedelta(minutes=15),
    )


def reset_password(db: Session, reset_token: str, new_password: str) -> None:
    try:
        payload = decode_token(reset_token)
        user_id = UUID(payload["sub"])
    except Exception:
        raise ValidationException("Invalid or expired reset token")

    if not validate_password_strength(new_password):
        raise ValidationException(
            "Password must be at least 8 characters and include uppercase, "
            "lowercase, and a digit."
        )

    user = user_crud.get(db, id=user_id)
    if not user:
        raise NotFoundException("User")

    user_crud.reset_password(db, user=user, new_password=new_password)
    log.info("Password reset completed for user_id=%s", user_id)


# ─── Singleton alias ──────────────────────────────────────────────────────────

class _AuthService:
    register_user                    = staticmethod(register_user)
    authenticate_user                = staticmethod(authenticate_user)
    verify_otp                       = staticmethod(verify_otp)
    resend_otp                       = staticmethod(resend_otp)
    initiate_password_reset          = staticmethod(initiate_password_reset)
    verify_reset_otp_and_issue_token = staticmethod(verify_reset_otp_and_issue_token)
    reset_password                   = staticmethod(reset_password)
    issue_tokens                     = staticmethod(issue_tokens)
    refresh_access_token             = staticmethod(refresh_access_token)
    
    # PIN management
    setup_pin                        = staticmethod(setup_pin)
    verify_pin                       = staticmethod(verify_pin)
    change_pin                       = staticmethod(change_pin)
    authenticate_with_pin            = staticmethod(authenticate_with_pin)
    request_pin_unlock               = staticmethod(request_pin_unlock)
    unlock_pin_with_otp              = staticmethod(unlock_pin_with_otp)
    
    # Biometric
    enable_biometric                 = staticmethod(enable_biometric)
    disable_biometric                = staticmethod(disable_biometric)


auth_service = _AuthService()