"""
Authentication router for Localy.

Blueprint v2.0 changes:
- OAuth endpoints removed (/google, /apple)
- PIN endpoints added
- Biometric endpoints added
- Registration updated for date_of_birth and terms_accepted

FIX: enable_biometric now enforces Blueprint rule:
  "Biometric only available after PIN setup, never as a replacement"
  A 403 is raised if the user has not set a PIN yet.

FIX: verify_pin_endpoint — previously raised InvalidCredentialsException("Incorrect PIN")
  which crashed with TypeError because InvalidCredentialsException.__init__ took no args.
  Fixed in exceptions.py; the raise here now works correctly and is handled by
  main.py's localy_exception_handler → clean 401 response.

FIX: change_pin_endpoint — auth_service.change_pin() raises InvalidCredentialsException
  internally when the old PIN is wrong. Previously unhandled → 500. Now that
  InvalidCredentialsException accepts a detail arg, FastAPI's exception handler
  catches it and returns a proper 401.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.constants import UserType, UserStatus
from app.core.database import get_db
from app.core.exceptions import (
    AlreadyExistsException,
    InvalidCredentialsException,
    NotFoundException,
    ValidationException,
)
from app.crud.user_crud import user_crud
from app.dependencies import get_current_user, get_current_user_optional
from app.models.user_model import User
from app.schemas.auth_schema import (
    AdminRegisterRequest,
    BusinessRegisterRequest,
    CustomerRegisterRequest,
    ForgotPasswordRequest,
    LoginRequest,
    PinLoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    ResendOTPRequest,
    ResetPasswordRequest,
    RiderRegisterRequest,
    SetupPinRequest,
    VerifyPinRequest,
    ChangePinRequest,
    EnableBiometricRequest,
    DisableBiometricRequest,
    VerifyEmailRequest,
    VerifyPhoneRequest,
    VerifyResetOTPRequest,
)
from app.services.auth_service import auth_service
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()


# ─── Serialisation helpers ────────────────────────────────────────────────────

def _profile_data(user: User) -> Dict[str, Any]:
    """Serialise user + type-specific profile."""
    data: Dict[str, Any] = {
        "id":                str(user.id),
        "email":             user.email,
        "phone":             user.phone,
        "user_type":         user.user_type.value,
        "status":            user.status.value,
        "is_email_verified": user.is_email_verified,
        "is_phone_verified": user.is_phone_verified,
        "pin_set":           user.pin_hash is not None,
        "biometric_enabled": user.biometric_enabled,
        "created_at":        user.created_at.isoformat() if user.created_at else None,
    }

    if user.user_type == UserType.CUSTOMER and user.customer_profile:
        p = user.customer_profile
        data["customer_profile"] = {
            "id":               str(p.id),
            "first_name":       p.first_name,
            "last_name":        p.last_name,
            "date_of_birth":    p.date_of_birth.isoformat() if p.date_of_birth else None,
            "profile_picture":  p.profile_picture,
            "bio":              p.bio,
            "local_government": p.local_government,
            "state":            p.state,
            "country":          p.country or "Nigeria",
        }
    elif user.user_type == UserType.BUSINESS and user.business:
        b = user.business
        data["business_id"] = str(b.id)
        data["business_profile"] = {
            "business_name":      b.business_name,
            "category":           b.category.value if b.category else None,
            "subcategory":        b.subcategory,
            "logo":               b.logo,
            "verification_badge": b.verification_badge.value if b.verification_badge else "none",
            "average_rating":     float(b.average_rating) if b.average_rating else 0.0,
        }
    elif user.user_type == UserType.RIDER and user.rider:
        r = user.rider
        data["rider_id"] = str(r.id)
        data["rider_profile"] = {
            "first_name":     r.first_name,
            "last_name":      r.last_name,
            "vehicle_type":   r.vehicle_type,
            "is_verified":    r.is_verified,
            "is_online":      r.is_online,
            "average_rating": float(r.average_rating) if r.average_rating else 0.0,
        }
    elif user.user_type == UserType.ADMIN and user.admin:
        data["admin_profile"] = {
            "full_name": user.admin.full_name,
            "role":      user.admin.role,
        }

    return data


# ─── Unauthenticated OTP verify schemas ──────────────────────────────────────

class VerifyEmailUnauthRequest(BaseModel):
    """Supports both authenticated (JWT) and unauthenticated (identifier) verify."""
    otp:        str
    identifier: Optional[str] = None  # email — required when not authenticated


class VerifyPhoneUnauthRequest(BaseModel):
    otp:        str
    identifier: Optional[str] = None  # phone — required when not authenticated


class ResendOTPUnauthRequest(BaseModel):
    channel:    str = "both"
    identifier: Optional[str] = None  # email or phone for unauthenticated resend


# ─── Registration ─────────────────────────────────────────────────────────────

@router.post("/register/customer", status_code=status.HTTP_201_CREATED)
def register_customer(
    *,
    db: Session = Depends(get_db),
    user_in: CustomerRegisterRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    reg = RegisterRequest(
        user_type=UserType.CUSTOMER,
        email=user_in.email,
        phone=user_in.phone,
        password=user_in.password,
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        date_of_birth=user_in.date_of_birth,
        referral_code=user_in.referral_code,
        otp_channel=user_in.otp_channel,
        terms_accepted=user_in.terms_accepted,
        terms_version="v1.0",
    )
    user, channel = auth_service.register_user(
        db, reg, background_tasks, otp_channel=user_in.otp_channel or "both"
    )
    return {
        "success": True,
        "data": {
            "user_id":     str(user.id),
            "email":       user.email,
            "phone":       user.phone,
            "user_type":   "customer",
            "otp_channel": channel,
            "message":     "Registration successful. Please verify your account.",
        },
    }


@router.post("/register/business", status_code=status.HTTP_201_CREATED)
def register_business(
    *,
    db: Session = Depends(get_db),
    user_in: BusinessRegisterRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    reg = RegisterRequest(
        user_type=UserType.BUSINESS,
        email=user_in.email,
        phone=user_in.phone,
        password=user_in.password,
        business_name=user_in.business_name,
        business_category=user_in.business_category,
        business_subcategory=user_in.business_subcategory,
        address=user_in.address,
        city=user_in.city,
        local_government=user_in.local_government,
        state=user_in.state,
        latitude=user_in.latitude,
        longitude=user_in.longitude,
        description=user_in.description,
        website=user_in.website,
        instagram=user_in.instagram,
        facebook=user_in.facebook,
        whatsapp=user_in.whatsapp,
        opening_hours=user_in.opening_hours,
        otp_channel=user_in.otp_channel,
        terms_accepted=user_in.terms_accepted,
        terms_version="v1.0",
    )
    user, channel = auth_service.register_user(
        db, reg, background_tasks, otp_channel=user_in.otp_channel or "both"
    )
    return {
        "success": True,
        "data": {
            "user_id":       str(user.id),
            "email":         user.email,
            "phone":         user.phone,
            "user_type":     "business",
            "business_name": user_in.business_name,
            "otp_channel":   channel,
            "message":       "Business registration successful. Please verify your account.",
        },
    }


@router.post("/register/rider", status_code=status.HTTP_201_CREATED)
def register_rider(
    *,
    db: Session = Depends(get_db),
    user_in: RiderRegisterRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    reg = RegisterRequest(
        user_type=UserType.RIDER,
        email=user_in.email,
        phone=user_in.phone,
        password=user_in.password,
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        vehicle_type=user_in.vehicle_type,
        vehicle_plate_number=user_in.vehicle_plate_number,
        vehicle_color=user_in.vehicle_color,
        vehicle_model=user_in.vehicle_model,
        otp_channel=user_in.otp_channel,
        terms_accepted=user_in.terms_accepted,
        terms_version="v1.0",
    )
    user, channel = auth_service.register_user(
        db, reg, background_tasks, otp_channel=user_in.otp_channel or "both"
    )
    return {
        "success": True,
        "data": {
            "user_id":     str(user.id),
            "email":       user.email,
            "phone":       user.phone,
            "user_type":   "rider",
            "otp_channel": channel,
            "message":     "Rider registration successful. Please verify your account.",
        },
    }


# ─── Login ────────────────────────────────────────────────────────────────────

@router.post("/login")
def login(
    *,
    db: Session = Depends(get_db),
    body: LoginRequest,
) -> dict:
    user = auth_service.authenticate_user(
        db, identifier=body.identifier, password=body.password
    )
    return {
        "success": True,
        "data": {
            **auth_service.issue_tokens(str(user.id)),
            "user":    _profile_data(user),
            "message": "Login successful.",
        },
    }


# ─── PIN Management (Blueprint v2.0) ──────────────────────────────────────────

@router.post("/setup-pin")
def setup_pin(
    *,
    db: Session = Depends(get_db),
    body: SetupPinRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Set up 4-digit PIN during onboarding.

    Blueprint: "Set 4-digit transaction PIN (mandatory — enables wallet and payments)"
    """
    auth_service.setup_pin(db, user=current_user, pin=body.pin)
    return {
        "success": True,
        "data": {"message": "PIN set successfully."},
    }


@router.post("/verify-pin")
def verify_pin_endpoint(
    *,
    db: Session = Depends(get_db),
    body: VerifyPinRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Verify PIN for wallet transactions.

    Blueprint: "PIN is required for all wallet transactions, withdrawals,
    and payments above ₦5,000"
    """
    is_valid = auth_service.verify_pin(db, user=current_user, pin=body.pin)
    if not is_valid:
        # InvalidCredentialsException is a LocalyException (HTTPException subclass).
        # FastAPI's localy_exception_handler in main.py catches this and returns
        # a structured 401 — no try/except needed here.
        raise InvalidCredentialsException("Incorrect PIN.")

    return {
        "success": True,
        "data": {"message": "PIN verified successfully."},
    }


@router.post("/change-pin")
def change_pin_endpoint(
    *,
    db: Session = Depends(get_db),
    body: ChangePinRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Change existing PIN.

    auth_service.change_pin() raises InvalidCredentialsException("Incorrect PIN")
    internally when the old PIN is wrong. With the fixed exception constructor
    this propagates correctly and is handled by localy_exception_handler → 401.
    """
    auth_service.change_pin(
        db, user=current_user, old_pin=body.old_pin, new_pin=body.new_pin
    )
    return {
        "success": True,
        "data": {"message": "PIN changed successfully."},
    }


@router.post("/pin-login")
def pin_login(
    *,
    db: Session = Depends(get_db),
    body: PinLoginRequest,
) -> dict:
    """
    PIN login for quick access.

    Blueprint: "PIN login (quick access after first session)"
    """
    user = auth_service.authenticate_with_pin(
        db, identifier=body.identifier, pin=body.pin
    )
    return {
        "success": True,
        "data": {
            **auth_service.issue_tokens(str(user.id)),
            "user":    _profile_data(user),
            "message": "Login successful.",
        },
    }


@router.post("/request-pin-unlock")
def request_pin_unlock_endpoint(
    *,
    db: Session = Depends(get_db),
    body: dict,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Request SMS unlock code for locked PIN.

    Blueprint: "5 wrong PIN attempts → 30-minute lockout → SMS unlock code"
    """
    identifier = body.get("identifier")
    if not identifier:
        raise ValidationException("Identifier required")

    auth_service.request_pin_unlock(db, identifier=identifier, background_tasks=background_tasks)
    return {
        "success": True,
        "data": {"message": "If that account exists, an unlock code has been sent via SMS."},
    }


@router.post("/unlock-pin")
def unlock_pin_endpoint(
    *,
    db: Session = Depends(get_db),
    body: dict,
) -> dict:
    """Unlock PIN using SMS OTP."""
    identifier = body.get("identifier")
    otp = body.get("otp")

    if not identifier or not otp:
        raise ValidationException("Identifier and OTP required")

    auth_service.unlock_pin_with_otp(db, identifier=identifier, otp=otp)
    return {
        "success": True,
        "data": {"message": "PIN unlocked successfully. You may now log in."},
    }


# ─── Biometric (Blueprint v2.0) ───────────────────────────────────────────────

@router.post("/enable-biometric")
def enable_biometric_endpoint(
    *,
    db: Session = Depends(get_db),
    body: EnableBiometricRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Enable biometric authentication.

    Blueprint: "Optional: enable biometric authentication (Face ID / fingerprint)
    after PIN is set. Biometric only available after PIN setup, never as a
    replacement — always falls back to PIN."

    FIX: Enforce PIN-first rule. Biometric cannot be enabled without an
    existing PIN hash on the account.
    """
    if not current_user.pin_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must set up a PIN before enabling biometric authentication.",
        )

    auth_service.enable_biometric(db, user=current_user)
    return {
        "success": True,
        "data": {"message": "Biometric authentication enabled."},
    }


@router.post("/disable-biometric")
def disable_biometric_endpoint(
    *,
    db: Session = Depends(get_db),
    body: DisableBiometricRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Disable biometric authentication."""
    auth_service.disable_biometric(db, user=current_user)
    return {
        "success": True,
        "data": {"message": "Biometric authentication disabled."},
    }


# ─── Token Refresh ────────────────────────────────────────────────────────────

@router.post("/refresh")
def refresh(*, body: RefreshTokenRequest) -> dict:
    tokens = auth_service.refresh_access_token(body.refresh_token)
    return {"success": True, "data": tokens}


# ─── OTP Verification ─────────────────────────────────────────────────────────

@router.post("/verify-email")
def verify_email(
    *,
    db: Session = Depends(get_db),
    body: VerifyEmailUnauthRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> dict:
    """Verify email OTP — supports unauthenticated requests."""
    user = current_user
    if user is None:
        if not body.identifier:
            raise ValidationException("Provide either a Bearer token or an identifier")
        user = user_crud.get_by_email(db, email=body.identifier)
        if not user:
            user = user_crud.get_by_phone(db, phone=body.identifier)
        if not user:
            raise ValidationException("Account not found")

    auth_service.verify_otp(db, user=user, otp=body.otp, channel="email")

    if user.is_email_verified and user.is_phone_verified:
        if user.status == UserStatus.PENDING_VERIFICATION:
            user.status = UserStatus.ACTIVE
            db.commit()
            db.refresh(user)

    user_with_profile = user_crud.get_with_profile(db, user_id=user.id)
    tokens = auth_service.issue_tokens(str(user.id))
    return {
        "success": True,
        "data": {
            **tokens,
            "user":    _profile_data(user_with_profile),
            "message": "Email verified successfully.",
        },
    }


@router.post("/verify-phone")
def verify_phone(
    *,
    db: Session = Depends(get_db),
    body: VerifyPhoneUnauthRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> dict:
    """Verify phone OTP — supports unauthenticated requests."""
    user = current_user
    if user is None:
        if not body.identifier:
            raise ValidationException("Provide either a Bearer token or an identifier")
        user = user_crud.get_by_phone(db, phone=body.identifier)
        if not user:
            user = user_crud.get_by_email(db, email=body.identifier)
        if not user:
            raise ValidationException("Account not found")

    auth_service.verify_otp(db, user=user, otp=body.otp, channel="phone")

    if user.is_email_verified and user.is_phone_verified:
        if user.status == UserStatus.PENDING_VERIFICATION:
            user.status = UserStatus.ACTIVE
            db.commit()
            db.refresh(user)

    user_with_profile = user_crud.get_with_profile(db, user_id=user.id)
    tokens = auth_service.issue_tokens(str(user.id))
    return {
        "success": True,
        "data": {
            **tokens,
            "user":    _profile_data(user_with_profile),
            "message": "Phone verified successfully.",
        },
    }


@router.post("/resend-otp")
def resend_otp(
    *,
    db: Session = Depends(get_db),
    body: ResendOTPUnauthRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
    background_tasks: BackgroundTasks,
) -> dict:
    """Re-send OTP — works with or without auth token."""
    user = current_user
    if user is None:
        if not body.identifier:
            raise ValidationException("Provide either a Bearer token or an identifier")
        user = user_crud.get_by_email(db, email=body.identifier)
        if not user:
            user = user_crud.get_by_phone(db, phone=body.identifier)
        if not user:
            raise ValidationException("Account not found")

    channel = body.channel or "both"
    auth_service.resend_otp(db, user=user, channel=channel, background_tasks=background_tasks)
    return {"success": True, "data": {"message": f"OTP resent via {channel}."}}


# ─── Forgot / Reset Password ──────────────────────────────────────────────────

@router.post("/forgot-password")
def forgot_password(
    *,
    db: Session = Depends(get_db),
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    auth_service.initiate_password_reset(
        db,
        email=body.email,
        phone=body.phone,
        channel=body.channel or ("email" if body.email else "phone"),
        background_tasks=background_tasks,
    )
    return {
        "success": True,
        "data": {"message": "If that account exists, a reset OTP has been sent."},
    }


@router.post("/verify-reset-otp")
def verify_reset_otp(
    *,
    db: Session = Depends(get_db),
    body: VerifyResetOTPRequest,
) -> dict:
    reset_token = auth_service.verify_reset_otp_and_issue_token(
        db, email=body.email, phone=body.phone, otp=body.otp
    )
    return {
        "success": True,
        "data": {
            "reset_token": reset_token,
            "message":     "OTP verified. Use the reset_token to set a new password.",
        },
    }


@router.post("/reset-password")
def reset_password(
    *,
    db: Session = Depends(get_db),
    body: ResetPasswordRequest,
) -> dict:
    auth_service.reset_password(
        db, reset_token=body.reset_token, new_password=body.new_password
    )
    return {
        "success": True,
        "data": {"message": "Password reset successfully. Please log in."},
    }


# ─── Current User ─────────────────────────────────────────────────────────────

@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    user = user_crud.get_with_profile(db, user_id=current_user.id)
    return {"success": True, "data": _profile_data(user)}


# ─── Dev Only ─────────────────────────────────────────────────────────────────

@router.post("/dev/verify-all")
def dev_verify_all(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if settings.APP_ENV == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    current_user.is_email_verified = True
    current_user.is_phone_verified = True
    current_user.status = UserStatus.ACTIVE
    db.commit()
    db.refresh(current_user)

    tokens = auth_service.issue_tokens(str(current_user.id))
    return {
        "success": True,
        "data": {
            **tokens,
            "user":    _profile_data(current_user),
            "message": "Verified and activated.",
        },
    }