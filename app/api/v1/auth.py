"""
app/routers/auth.py

FIXES vs previous version:
  1.  _profile_data: user.phone → user.phone_number (Blueprint §14).
  2.  _profile_data: user.user_type → user.role (Blueprint §14).
  3.  _profile_data: user.biometric_enabled → user.biometric_flag (Blueprint §14).
  4.  _profile_data: user.status serialised via is_active/is_banned booleans
      (Blueprint §14 — model no longer has a status Enum).
  5.  _profile_data: local_government removed from customer_profile response
      (Blueprint HARD RULE: no LGA anywhere).
  6.  register_business: local_government parameter removed (HARD RULE).
  7.  Registration: PIN now mandatory in the request body — collected during
      registration (RegisterRequest.pin). No separate /setup-pin step needed
      during onboarding. Blueprint §3.1 step 6: "MANDATORY. Cannot be skipped."
  8.  /verify-email endpoint REMOVED — Blueprint is phone-only registration.
      Only /verify-phone remains.
  9.  /forgot-password: phone only (Blueprint §3.2 — no email option).
  10. /verify-reset-otp: phone only.
  11. account status set via user.is_active = True (not user.status = ACTIVE).
  12. AdminRegisterRequest endpoint REMOVED — Blueprint §2 HARD RULE.
  13. /enable-biometric enforces PIN-first check (existing code — kept).
  14. issue_tokens() now takes the User object (not just user_id string).
  15. Blueprint §3.2: /refresh now rotates the refresh token in Redis.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_db
from app.core.exceptions import (
    InvalidCredentialsException,
    ValidationException,
)
from app.crud.user_crud import user_crud
from app.dependencies import get_current_user, get_current_user_optional, require_pin_set
from app.models.user_model import User
from app.schemas.auth_schema import (
    BusinessRegisterRequest,
    ChangePinRequest,
    CustomerRegisterRequest,
    DisableBiometricRequest,
    EnableBiometricRequest,
    ForgotPasswordRequest,
    LoginRequest,
    PinLoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    ResendOTPRequest,
    ResetPasswordRequest,
    RiderRegisterRequest,
    SetupPinRequest,
    VerifyPhoneRequest,
    VerifyPinRequest,
    VerifyResetOTPRequest,
)
from app.services.auth_service import auth_service
from pydantic import BaseModel

log    = logging.getLogger(__name__)
router = APIRouter()


# ─── Serialisation helpers ────────────────────────────────────────────────────

def _status_str(user: User) -> str:
    """
    Compute status string from is_active + is_banned booleans.
    Blueprint §14: two booleans, no status Enum.
    """
    if user.is_banned:
        return "banned"
    if not user.is_active:
        return "inactive"
    if not user.is_phone_verified:
        return "pending_verification"
    return "active"


def _profile_data(user: User) -> Dict[str, Any]:
    """Serialise user + type-specific profile."""
    data: Dict[str, Any] = {
        "id":                str(user.id),
        # Blueprint §14: phone_number (not phone)
        "phone_number":      user.phone_number,
        "email":             user.email,
        # Blueprint §14: role (not user_type)
        "role":              user.role.value if hasattr(user.role, "value") else str(user.role),
        "status":            _status_str(user),
        "is_phone_verified": user.is_phone_verified,
        "pin_set":           user.pin_hash is not None,
        # Blueprint §14: biometric_flag (not biometric_enabled)
        "biometric_flag":    user.biometric_flag,
        "referral_code":     user.referral_code,
        "created_at":        user.created_at.isoformat() if user.created_at else None,
    }

    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)

    if role_val == "customer" and user.customer_profile:
        p = user.customer_profile
        data["customer_profile"] = {
            "id":              str(p.id),
            "first_name":      p.first_name,
            "last_name":       p.last_name,
            "date_of_birth":   p.date_of_birth.isoformat() if p.date_of_birth else None,
            "profile_picture": p.profile_picture,
            "bio":             p.bio,
            # REMOVED: local_government — Blueprint HARD RULE: no LGA anywhere.
            "state":           p.state,
            "country":         p.country or "Nigeria",
        }

    elif role_val == "business" and user.business:
        b = user.business
        data["business_id"]      = str(b.id)
        data["business_profile"] = {
            "business_name":        b.business_name,
            "category":             b.category if isinstance(b.category, str) else (b.category.value if b.category else None),
            "subcategory":          b.subcategory,
            "logo":                 b.logo,
            "is_verified":          b.is_verified,
            "subscription_tier":    b.subscription_tier,
            "average_rating":       float(b.average_rating) if b.average_rating else 0.0,
        }

    elif role_val == "rider" and user.rider:
        r = user.rider
        data["rider_id"]      = str(r.id)
        data["rider_profile"] = {
            "first_name":     r.first_name,
            "last_name":      r.last_name,
            "vehicle_type":   r.vehicle_type,
            "is_verified":    r.is_verified,
            "is_online":      r.is_online,
            "average_rating": float(r.average_rating) if r.average_rating else 0.0,
        }

    return data


# ─── Inline schemas (small local models) ─────────────────────────────────────

class VerifyPhoneUnauthRequest(BaseModel):
    otp:        str
    identifier: Optional[str] = None   # phone — required when not authenticated


class ResendOTPUnauthRequest(BaseModel):
    identifier: Optional[str] = None


# ─── Registration ─────────────────────────────────────────────────────────────

@router.post("/register/customer", status_code=status.HTTP_201_CREATED)
def register_customer(
    *,
    db:               Session        = Depends(get_db),
    user_in:          CustomerRegisterRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Register a new customer.

    Blueprint §3.1 steps 1-8:
      - PIN is mandatory (included in request body).
      - OTP sent via Termii SMS immediately after creation.
      - 4 Celery tasks dispatched asynchronously.
      - T&C acceptance logged.
    """
    reg = RegisterRequest(
        role="customer",
        email=user_in.email,
        phone=user_in.phone,
        password=user_in.password,
        pin=user_in.pin,           # Blueprint §3.1 step 6: MANDATORY
        full_name=f"{user_in.first_name} {user_in.last_name}",
        date_of_birth=user_in.date_of_birth,
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        referral_code=user_in.referral_code,
        otp_channel="phone",
        terms_accepted=user_in.terms_accepted,
    )
    user = auth_service.register_user(db, reg, background_tasks)
    return {
        "success": True,
        "data": {
            "user_id":   str(user.id),
            "phone_number": user.phone_number,
            "role":      "customer",
            "message":   "Registration successful. Please verify your phone number via the SMS code sent.",
        },
    }


@router.post("/register/business", status_code=status.HTTP_201_CREATED)
def register_business(
    *,
    db:               Session                   = Depends(get_db),
    user_in:          BusinessRegisterRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Register a new business.

    Blueprint §3.1 step 5a: address geocoded to PostGIS point server-side.
    Blueprint §2 HARD RULE: category immutable post-registration.
    """
    reg = RegisterRequest(
        role="business",
        email=user_in.email,
        phone=user_in.phone,
        password=user_in.password,
        pin=user_in.pin,
        full_name=user_in.business_name,   # owner full_name set separately in profile
        business_name=user_in.business_name,
        business_category=user_in.business_category.value,
        business_subcategory=user_in.business_subcategory,
        address=user_in.address,
        city=user_in.city,
        # REMOVED: local_government — Blueprint HARD RULE: no LGA anywhere.
        state=user_in.state,
        latitude=user_in.latitude,
        longitude=user_in.longitude,
        description=user_in.description,
        website=user_in.website,
        instagram=user_in.instagram,
        facebook=user_in.facebook,
        whatsapp=user_in.whatsapp,
        opening_hours=user_in.opening_hours,
        otp_channel="phone",
        terms_accepted=user_in.terms_accepted,
    )
    user = auth_service.register_user(db, reg, background_tasks)
    return {
        "success": True,
        "data": {
            "user_id":       str(user.id),
            "phone_number":  user.phone_number,
            "role":          "business",
            "business_name": user_in.business_name,
            "message":       "Business registration successful. Please verify your phone number. Your listing will be reviewed by our team.",
        },
    }


@router.post("/register/rider", status_code=status.HTTP_201_CREATED)
def register_rider(
    *,
    db:               Session              = Depends(get_db),
    user_in:          RiderRegisterRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Register a new rider.
    Blueprint §3.1 step 5b: vehicle type + gov ID upload.
    """
    reg = RegisterRequest(
        role="rider",
        email=user_in.email,
        phone=user_in.phone,
        password=user_in.password,
        pin=user_in.pin,
        full_name=f"{user_in.first_name} {user_in.last_name}",
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        vehicle_type=user_in.vehicle_type,
        vehicle_plate_number=user_in.vehicle_plate_number,
        vehicle_color=user_in.vehicle_color,
        vehicle_model=user_in.vehicle_model,
        gov_id_url=user_in.gov_id_url,
        otp_channel="phone",
        terms_accepted=user_in.terms_accepted,
    )
    user = auth_service.register_user(db, reg, background_tasks)
    return {
        "success": True,
        "data": {
            "user_id":      str(user.id),
            "phone_number": user.phone_number,
            "role":         "rider",
            "message":      "Rider registration successful. Please verify your phone number. Your documents will be reviewed.",
        },
    }


# REMOVED: /register/admin
# Blueprint §2 HARD RULE: "Admin cannot register through mobile app
# or self-provision an account."


# ─── Login ────────────────────────────────────────────────────────────────────

@router.post("/login")
def login(
    *,
    db:   Session      = Depends(get_db),
    body: LoginRequest,
) -> dict:
    """
    Phone number + password login.
    Blueprint §3.2: issues access token (15 min) + refresh token (30 days).
    Refresh token stored in Redis: session:{user_id}:{jti}.
    """
    phone = body.resolved_phone or body.identifier
    user  = auth_service.authenticate_user(db, phone=phone, password=body.password)
    return {
        "success": True,
        "data": {
            **auth_service.issue_tokens(user),
            "user":    _profile_data(user),
            "message": "Login successful.",
        },
    }


# ─── PIN Management ───────────────────────────────────────────────────────────

@router.post("/verify-pin")
def verify_pin_endpoint(
    *,
    db:           Session = Depends(get_db),
    body:         VerifyPinRequest,
    current_user: User    = Depends(get_current_user),
) -> dict:
    """
    Verify PIN for wallet transaction confirmation.
    Blueprint §3.3: "PIN is required for ALL wallet transactions,
    withdrawals, and any payment above ₦5,000."
    """
    is_valid = auth_service.verify_pin_for_transaction(
        db, user=current_user, pin=body.pin
    )
    if not is_valid:
        raise InvalidCredentialsException("Incorrect PIN.")
    return {
        "success": True,
        "data": {"message": "PIN verified."},
    }


@router.post("/change-pin")
def change_pin_endpoint(
    *,
    db:           Session        = Depends(get_db),
    body:         ChangePinRequest,
    current_user: User           = Depends(get_current_user),
) -> dict:
    """
    Change existing PIN.
    Blueprint §3.3: requires current PIN + OTP from registered phone.
    """
    auth_service.change_pin(
        db,
        user=current_user,
        old_pin=body.old_pin,
        new_pin=body.new_pin,
        otp=body.otp,
    )
    return {
        "success": True,
        "data": {"message": "PIN changed successfully."},
    }


@router.post("/pin-login")
def pin_login(
    *,
    db:   Session          = Depends(get_db),
    body: PinLoginRequest,
) -> dict:
    """
    Quick PIN login after first session.
    Blueprint §3.2.
    """
    user = auth_service.authenticate_with_pin(
        db, phone=body.identifier, pin=body.pin
    )
    return {
        "success": True,
        "data": {
            **auth_service.issue_tokens(user),
            "user":    _profile_data(user),
            "message": "Login successful.",
        },
    }


@router.post("/request-pin-unlock")
def request_pin_unlock_endpoint(
    *,
    db:               Session        = Depends(get_db),
    body:             dict,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Request SMS unlock code for locked PIN.
    Blueprint §3.3: "5 wrong PIN attempts → 30-min lockout → SMS unlock code."
    """
    phone = body.get("phone")
    if not phone:
        raise ValidationException("Phone number required")

    auth_service.request_pin_unlock(
        db, phone=phone, background_tasks=background_tasks
    )
    return {
        "success": True,
        "data": {"message": "If that phone number is registered, an unlock code has been sent via SMS."},
    }


@router.post("/unlock-pin")
def unlock_pin_endpoint(
    *,
    db:   Session = Depends(get_db),
    body: dict,
) -> dict:
    """Unlock PIN using SMS OTP."""
    phone = body.get("phone")
    otp   = body.get("otp")
    if not phone or not otp:
        raise ValidationException("Phone and OTP required")

    auth_service.unlock_pin(db, phone=phone, otp=otp)
    return {
        "success": True,
        "data": {"message": "PIN unlocked. You may now log in."},
    }


# ─── Biometric ────────────────────────────────────────────────────────────────

@router.post("/enable-biometric")
def enable_biometric_endpoint(
    *,
    db:           Session               = Depends(get_db),
    body:         EnableBiometricRequest,
    # require_pin_set ensures PIN exists before biometric can be enabled
    current_user: User                  = Depends(require_pin_set),
) -> dict:
    """
    Enable biometric authentication.
    Blueprint §3.1 step 7: "Only presented AFTER PIN is confirmed active.
    It cannot be configured as a primary login method."
    require_pin_set dependency enforces PIN-first rule at route level.
    """
    auth_service.enable_biometric(db, user=current_user)
    return {
        "success": True,
        "data": {"message": "Biometric authentication enabled."},
    }


@router.post("/disable-biometric")
def disable_biometric_endpoint(
    *,
    db:           Session               = Depends(get_db),
    body:         DisableBiometricRequest,
    current_user: User                  = Depends(get_current_user),
) -> dict:
    auth_service.disable_biometric(db, user=current_user)
    return {
        "success": True,
        "data": {"message": "Biometric authentication disabled."},
    }


# ─── Token Refresh ────────────────────────────────────────────────────────────

@router.post("/refresh")
def refresh(*, body: RefreshTokenRequest) -> dict:
    """
    Rotate refresh token.
    Blueprint §3.2: "New refresh token issued on every use."
    Old Redis key deleted, new one stored.
    """
    tokens = auth_service.refresh_access_token(body.refresh_token)
    return {"success": True, "data": tokens}


# ─── Phone OTP Verification ───────────────────────────────────────────────────
# NOTE: /verify-email endpoint REMOVED.
# Blueprint §3 is phone-only registration — no email OTP step.

@router.post("/verify-phone")
def verify_phone(
    *,
    db:           Session = Depends(get_db),
    body:         VerifyPhoneUnauthRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> dict:
    """
    Verify phone OTP.
    Blueprint §3.1 Step 2: "On success: Redis key deleted, phone marked as
    verified in session."
    """
    user = current_user
    if user is None:
        if not body.identifier:
            raise ValidationException("Provide either a Bearer token or a phone number")
        user = user_crud.get_by_phone(db, phone=body.identifier)
        if not user:
            raise ValidationException("Account not found")

    auth_service.verify_otp(db, user=user, otp=body.otp)

    # Activate account after successful phone verification
    if not user.is_active:
        user.is_active = True
        db.commit()
        db.refresh(user)

    user_with_profile = user_crud.get_with_profile(db, user_id=user.id)
    return {
        "success": True,
        "data": {
            **auth_service.issue_tokens(user_with_profile),
            "user":    _profile_data(user_with_profile),
            "message": "Phone verified successfully.",
        },
    }


@router.post("/resend-otp")
def resend_otp(
    *,
    db:               Session = Depends(get_db),
    body:             ResendOTPUnauthRequest,
    current_user:     Optional[User] = Depends(get_current_user_optional),
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend OTP.
    Blueprint §3.1 Step 1: "Resend available after 60 seconds."
    """
    user = current_user
    if user is None:
        if not body.identifier:
            raise ValidationException("Provide either a Bearer token or a phone number")
        user = user_crud.get_by_phone(db, phone=body.identifier)
        if not user:
            raise ValidationException("Account not found")

    auth_service.resend_otp(db, user=user, background_tasks=background_tasks)
    return {
        "success": True,
        "data": {"message": "OTP resent via SMS."},
    }


# ─── Forgot / Reset Password ──────────────────────────────────────────────────

@router.post("/forgot-password")
def forgot_password(
    *,
    db:               Session               = Depends(get_db),
    body:             ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Send password reset OTP to phone.
    Blueprint §3.2: "Enter phone number → Receive SMS OTP (same Termii gateway)."
    Phone only — no email option.
    """
    auth_service.initiate_password_reset(
        db, phone=body.phone, background_tasks=background_tasks
    )
    return {
        "success": True,
        "data": {"message": "If that phone number is registered, a reset code has been sent via SMS."},
    }


@router.post("/verify-reset-otp")
def verify_reset_otp(
    *,
    db:   Session                 = Depends(get_db),
    body: VerifyResetOTPRequest,
) -> dict:
    reset_token = auth_service.verify_reset_otp_and_issue_token(
        db, phone=body.phone, otp=body.otp
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
    db:   Session             = Depends(get_db),
    body: ResetPasswordRequest,
) -> dict:
    """
    Set new password and invalidate all existing sessions.
    Blueprint §3.2: "All existing session tokens invalidated on password reset."
    """
    auth_service.reset_password(
        db, reset_token=body.reset_token, new_password=body.new_password
    )
    return {
        "success": True,
        "data": {"message": "Password reset successfully. Please log in again."},
    }


# ─── Current User ─────────────────────────────────────────────────────────────

@router.get("/me")
def get_me(
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
) -> dict:
    user = user_crud.get_with_profile(db, user_id=current_user.id)
    return {"success": True, "data": _profile_data(user)}


# ─── Dev Only ─────────────────────────────────────────────────────────────────

@router.post("/dev/verify-all")
def dev_verify_all(
    *,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
) -> dict:
    """Development helper — marks user as fully verified. Blocked in production."""
    if settings.APP_ENV == "production":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    current_user.is_phone_verified = True
    current_user.is_active         = True
    db.commit()
    db.refresh(current_user)

    return {
        "success": True,
        "data": {
            **auth_service.issue_tokens(current_user),
            "user":    _profile_data(current_user),
            "message": "Verified and activated (dev only).",
        },
    }