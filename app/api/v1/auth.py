"""
Authentication router for Localy.

Endpoints
─────────
POST /auth/register/customer
POST /auth/register/business
POST /auth/register/rider
POST /auth/register/admin       (admin-only)

POST /auth/login
GET  /auth/me

POST /auth/verify-email         (token OTP sent to email)
POST /auth/verify-phone         (OTP sent to phone)
POST /auth/resend-otp           (re-send to email OR phone)

POST /auth/forgot-password      (send reset OTP — email or phone)
POST /auth/verify-reset-otp     (validate OTP before allowing reset)
POST /auth/reset-password       (set new password using verified OTP token)

POST /auth/google               (Google ID token → JWT)
POST /auth/apple                (Apple identity token → JWT)

POST /auth/dev/verify-all       (dev-only: skip verification)
"""

from fastapi import APIRouter, Depends, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
from datetime import timedelta, datetime
import secrets

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_otp,
    hash_password,
    verify_password,
)
from app.core.exceptions import (
    InvalidCredentialsException,
    NotFoundException,
    ValidationException,
    AlreadyExistsException,
)
from app.core.constants import UserType, UserStatus
from app.config import settings
from app.schemas.auth_schema import (
    CustomerRegisterRequest,
    BusinessRegisterRequest,
    RiderRegisterRequest,
    AdminRegisterRequest,
    LoginRequest,
    VerifyEmailRequest,
    VerifyPhoneRequest,
    ForgotPasswordRequest,
    VerifyResetOTPRequest,
    ResetPasswordRequest,
    GoogleAuthRequest,
    AppleAuthRequest,
    ResendOTPRequest,
)
from app.schemas.common_schema import SuccessResponse
from app.crud.user_crud import user_crud
from app.crud.wallet_crud import wallet_crud
from app.dependencies import get_current_user, get_current_admin_user
from app.models.user_model import User
from app.core.email import email_service
from app.core.sms import sms_service
from app.core.oauth_service import google_oauth, apple_oauth
import secrets as _s
from app.schemas.auth_schema import RegisterRequest
from app.core.security import decode_token, validate_password_strength
from uuid import UUID


router = APIRouter()


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _profile_data(user: User) -> Dict[str, Any]:
    """Serialise user + type-specific profile into a flat dict."""
    data: Dict[str, Any] = {
        "id":                str(user.id),
        "email":             user.email,
        "phone":             user.phone,
        "user_type":         user.user_type.value,
        "status":            user.status.value,
        "is_email_verified": user.is_email_verified,
        "is_phone_verified": user.is_phone_verified,
        "created_at":        user.created_at.isoformat() if user.created_at else None,
    }

    if user.user_type == UserType.CUSTOMER and user.customer_profile:
        p = user.customer_profile
        data["profile"] = {
            "first_name":      p.first_name,
            "last_name":       p.last_name,
            "profile_picture": p.profile_picture,
        }

    elif user.user_type == UserType.BUSINESS and user.business:
        b = user.business
        data["profile"] = {
            "business_name":      b.business_name,
            "category":           b.category.value if b.category else None,
            "subcategory":        b.subcategory,
            "logo":               b.logo,
            "verification_badge": b.verification_badge.value if b.verification_badge else "none",
            "average_rating":     float(b.average_rating) if b.average_rating else 0.0,
        }

    elif user.user_type == UserType.RIDER and user.rider:
        r = user.rider
        data["profile"] = {
            "first_name":     r.first_name,
            "last_name":      r.last_name,
            "vehicle_type":   r.vehicle_type,
            "is_verified":    r.is_verified,
            "is_online":      r.is_online,
            "average_rating": float(r.average_rating) if r.average_rating else 0.0,
        }

    elif user.user_type == UserType.ADMIN and user.admin:
        data["profile"] = {
            "full_name": user.admin.full_name,
            "role":      user.admin.role,
        }

    return data


def _tokens(user: User) -> Dict[str, Any]:
    """Generate access + refresh tokens for a user."""
    return {
        "access_token":  create_access_token(subject=str(user.id)),
        "refresh_token": create_refresh_token(subject=str(user.id)),
        "token_type":    "bearer",
        "expires_in":    settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


def _get_user_name(user: User) -> str:
    """Best-effort display name for notification templates."""
    if user.customer_profile:
        return user.customer_profile.first_name
    if user.rider:
        return user.rider.first_name
    if user.business:
        return user.business.business_name
    if user.admin:
        return user.admin.full_name
    return user.email.split("@")[0]


def _send_email_otp(user: User, otp: str):
    """Fire email OTP (non-blocking — call from background task)."""
    name = _get_user_name(user)
    email_service.send_email_otp(user.email, name, otp)


def _send_phone_otp(user: User, otp: str):
    """Fire SMS OTP (non-blocking — call from background task)."""
    sms_service.send_otp(user.phone, otp)


# ─────────────────────────────────────────────
# REGISTRATION
# ─────────────────────────────────────────────

@router.post("/register/customer", status_code=status.HTTP_201_CREATED)
def register_customer(
    *,
    db: Session = Depends(get_db),
    user_in: CustomerRegisterRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """Register as a customer. Sends OTP via chosen channel (email | phone | both)."""


    reg = RegisterRequest(
        user_type=UserType.CUSTOMER,
        email=user_in.email,
        phone=user_in.phone,
        password=user_in.password,
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        otp_channel=user_in.otp_channel,
    )

    user = user_crud.create_user(db, obj_in=reg)
    wallet_crud.create_wallet(db, user_id=user.id)

    otp = user.phone_verification_otp  # generated in create_user

    channel = user_in.otp_channel or "both"
    if channel in ("email", "both"):
        background_tasks.add_task(_send_email_otp, user, otp)
    if channel in ("phone", "both"):
        background_tasks.add_task(_send_phone_otp, user, otp)

    return {
        "success": True,
        "data": {
            "user_id":   str(user.id),
            "email":     user.email,
            "phone":     user.phone,
            "user_type": "customer",
            "otp_channel": channel,
            "message": "Registration successful. Please verify your account using the OTP sent.",
        },
    }


@router.post("/register/business", status_code=status.HTTP_201_CREATED)
def register_business(
    *,
    db: Session = Depends(get_db),
    user_in: BusinessRegisterRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """Register as a business. Captures full business profile."""


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
    )

    user = user_crud.create_user(db, obj_in=reg)
    wallet_crud.create_wallet(db, user_id=user.id)

    otp = user.phone_verification_otp
    channel = user_in.otp_channel or "both"
    if channel in ("email", "both"):
        background_tasks.add_task(_send_email_otp, user, otp)
    if channel in ("phone", "both"):
        background_tasks.add_task(_send_phone_otp, user, otp)

    return {
        "success": True,
        "data": {
            "user_id":       str(user.id),
            "email":         user.email,
            "phone":         user.phone,
            "user_type":     "business",
            "business_name": user_in.business_name,
            "otp_channel":   channel,
            "message": "Business registration successful. Please verify your account.",
        },
    }


@router.post("/register/rider", status_code=status.HTTP_201_CREATED)
def register_rider(
    *,
    db: Session = Depends(get_db),
    user_in: RiderRegisterRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """Register as a delivery rider."""


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
    )

    user = user_crud.create_user(db, obj_in=reg)
    wallet_crud.create_wallet(db, user_id=user.id)

    otp = user.phone_verification_otp
    channel = user_in.otp_channel or "both"
    if channel in ("email", "both"):
        background_tasks.add_task(_send_email_otp, user, otp)
    if channel in ("phone", "both"):
        background_tasks.add_task(_send_phone_otp, user, otp)

    return {
        "success": True,
        "data": {
            "user_id":     str(user.id),
            "email":       user.email,
            "phone":       user.phone,
            "user_type":   "rider",
            "otp_channel": channel,
            "message": "Rider registration successful. Please verify your account.",
        },
    }


@router.post("/register/admin", status_code=status.HTTP_201_CREATED)
def register_admin(
    *,
    db: Session = Depends(get_db),
    user_in: AdminRegisterRequest,
    background_tasks: BackgroundTasks,
    _: User = Depends(get_current_admin_user),  # super-admin only
) -> dict:
    reg = RegisterRequest(
        user_type=UserType.ADMIN,
        email=user_in.email,
        phone=user_in.phone,
        password=user_in.password,
        full_name=user_in.full_name,
        role=user_in.role,
        otp_channel="email",
    )

    user = user_crud.create_user(db, obj_in=reg)
    wallet_crud.create_wallet(db, user_id=user.id)

    background_tasks.add_task(_send_email_otp, user, user.phone_verification_otp)

    return {
        "success": True,
        "data": {
            "user_id":   str(user.id),
            "email":     user.email,
            "user_type": "admin",
            "message":   "Admin account created. Verification email sent.",
        },
    }


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────

@router.post("/login")
def login(
    *,
    db: Session = Depends(get_db),
    credentials: LoginRequest,
) -> dict:
    """Authenticate with email + password. Returns JWT pair + profile."""
    user = user_crud.authenticate(
        db, email=credentials.email, password=credentials.password
    )
    if not user:
        raise InvalidCredentialsException()

    user = user_crud.get_with_profile(db, user_id=user.id)
    user_crud.update_last_login(db, user=user)

    return {
        "success": True,
        "data": {
            **_tokens(user),
            "user": _profile_data(user),
        },
    }


# ─────────────────────────────────────────────
# VERIFICATION
# ─────────────────────────────────────────────

@router.post("/verify-email")
def verify_email(
    *,
    db: Session = Depends(get_db),
    body: VerifyEmailRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Verify email using the OTP sent to the user's email address."""
    success = user_crud.verify_otp_code(
        db, user=current_user, otp=body.otp, channel="email"
    )
    if not success:
        raise ValidationException("Invalid or expired OTP")

    return {"success": True, "data": {"message": "Email verified successfully."}}


@router.post("/verify-phone")
def verify_phone(
    *,
    db: Session = Depends(get_db),
    body: VerifyPhoneRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Verify phone using the OTP sent via SMS."""
    success = user_crud.verify_otp_code(
        db, user=current_user, otp=body.otp, channel="phone"
    )
    if not success:
        raise ValidationException("Invalid or expired OTP")

    return {"success": True, "data": {"message": "Phone verified successfully."}}


@router.post("/resend-otp")
def resend_otp(
    *,
    db: Session = Depends(get_db),
    body: ResendOTPRequest,
    current_user: User = Depends(get_current_user),
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Re-generate and re-send OTP.

    body.channel: "email" | "phone" | "both"
    """
    otp = user_crud.regenerate_otp(db, user=current_user)
    channel = body.channel or "both"

    if channel in ("email", "both"):
        background_tasks.add_task(_send_email_otp, current_user, otp)
    if channel in ("phone", "both"):
        background_tasks.add_task(_send_phone_otp, current_user, otp)

    return {
        "success": True,
        "data": {"message": f"OTP resent via {channel}."},
    }


# ─────────────────────────────────────────────
# FORGOT / RESET PASSWORD
# ─────────────────────────────────────────────

@router.post("/forgot-password")
def forgot_password(
    *,
    db: Session = Depends(get_db),
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Initiate password reset.

    Accepts email OR phone. Sends a 6-digit OTP via the
    specified channel (email | phone | both).
    Always returns 200 to prevent email/phone enumeration.
    """
    user: Optional[User] = None

    if body.email:
        user = user_crud.get_by_email(db, email=body.email)
    elif body.phone:
        user = user_crud.get_by_phone(db, phone=body.phone)

    if user:
        otp = user_crud.set_password_reset_otp(db, user=user)
        channel = body.channel or ("email" if body.email else "phone")

        if channel in ("email", "both") and user.email:
            name = _get_user_name(user)
            background_tasks.add_task(
                email_service.send_password_reset_otp, user.email, name, otp
            )
        if channel in ("phone", "both") and user.phone:
            background_tasks.add_task(
                sms_service.send_password_reset, user.phone, otp
            )

    # Always return success to prevent enumeration attacks
    return {
        "success": True,
        "data": {
            "message": "If that account exists, a reset OTP has been sent."
        },
    }


@router.post("/verify-reset-otp")
def verify_reset_otp(
    *,
    db: Session = Depends(get_db),
    body: VerifyResetOTPRequest,
) -> dict:
    """
    Validate the password-reset OTP.
    Returns a short-lived reset_token to be used in /reset-password.
    """
    user: Optional[User] = None

    if body.email:
        user = user_crud.get_by_email(db, email=body.email)
    elif body.phone:
        user = user_crud.get_by_phone(db, phone=body.phone)

    if not user:
        raise ValidationException("Invalid request")

    if not user_crud.check_password_reset_otp(db, user=user, otp=body.otp):
        raise ValidationException("Invalid or expired OTP")

    # Issue a short-lived token (15 min) scoped to password reset
    reset_token = create_access_token(
        subject=str(user.id),
        expires_delta=timedelta(minutes=15),
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
    """
    Set a new password.
    Requires the reset_token from /verify-reset-otp.
    """
    try:
        payload  = decode_token(body.reset_token)
        user_id  = UUID(payload["sub"])
    except Exception:
        raise ValidationException("Invalid or expired reset token")

    user = user_crud.get(db, id=user_id)
    if not user:
        raise NotFoundException("User")

    if not validate_password_strength(body.new_password):
        raise ValidationException(
            "Password must be at least 8 characters and include uppercase, "
            "lowercase, and a digit."
        )

    user.password_hash = hash_password(body.new_password)
    # Clear any lingering reset OTP
    user.password_reset_otp     = None
    user.password_reset_expires = None
    db.commit()

    return {
        "success": True,
        "data": {"message": "Password reset successfully. Please log in."},
    }


# ─────────────────────────────────────────────
# GOOGLE OAUTH
# ─────────────────────────────────────────────

@router.post("/google")
async def auth_google(
    *,
    db: Session = Depends(get_db),
    body: GoogleAuthRequest,
) -> dict:
    """
    Authenticate via Google Sign-In.

    Client sends the Google ID token obtained from the google_sign_in package.
    Backend verifies it and either logs in the existing user or creates a new one.
    """
    user_info = await google_oauth.verify_id_token(body.id_token)
    if not user_info:
        raise InvalidCredentialsException("Invalid Google token")

    # Look for existing user
    user = user_crud.get_by_email(db, email=user_info.email)

    if not user:
        # Auto-register as customer (can be changed via profile later)

        reg = RegisterRequest(
            user_type=UserType.CUSTOMER,
            email=user_info.email,
            phone=body.phone or f"+000{_s.token_hex(4)}",  # placeholder — must be updated
            password=_s.token_urlsafe(24),                  # random password
            first_name=(user_info.name or "").split()[0] or "User",
            last_name=" ".join((user_info.name or "").split()[1:]) or "",
            oauth_provider="google",
            oauth_provider_id=user_info.provider_id,
        )
        user = user_crud.create_oauth_user(db, obj_in=reg, avatar=user_info.avatar_url)
        wallet_crud.create_wallet(db, user_id=user.id)
    else:
        # Link Google account if not already linked
        user_crud.link_oauth(db, user=user, provider="google", provider_id=user_info.provider_id)

    user = user_crud.get_with_profile(db, user_id=user.id)
    user_crud.update_last_login(db, user=user)

    return {
        "success": True,
        "data": {
            **_tokens(user),
            "user": _profile_data(user),
            "is_new_user": not bool(user.is_email_verified),
        },
    }


# ─────────────────────────────────────────────
# APPLE SIGN-IN
# ─────────────────────────────────────────────

@router.post("/apple")
async def auth_apple(
    *,
    db: Session = Depends(get_db),
    body: AppleAuthRequest,
) -> dict:
    """
    Authenticate via Apple Sign-In.

    Client sends Apple identity_token + optional full_name (only on first sign-in).
    """
    user_info = await apple_oauth.verify_identity_token(
        body.identity_token, full_name=body.full_name
    )
    if not user_info:
        raise InvalidCredentialsException("Invalid Apple token")

    user = user_crud.get_by_email(db, email=user_info.email)

    if not user:

        import secrets as _s

        name_parts = (user_info.name or "").split()
        reg = RegisterRequest(
            user_type=UserType.CUSTOMER,
            email=user_info.email,
            phone=body.phone or f"+000{_s.token_hex(4)}",
            password=_s.token_urlsafe(24),
            first_name=name_parts[0] if name_parts else "User",
            last_name=" ".join(name_parts[1:]) if len(name_parts) > 1 else "",
            oauth_provider="apple",
            oauth_provider_id=user_info.provider_id,
        )
        user = user_crud.create_oauth_user(db, obj_in=reg, avatar=None)
        wallet_crud.create_wallet(db, user_id=user.id)
    else:
        user_crud.link_oauth(db, user=user, provider="apple", provider_id=user_info.provider_id)

    user = user_crud.get_with_profile(db, user_id=user.id)
    user_crud.update_last_login(db, user=user)

    return {
        "success": True,
        "data": {
            **_tokens(user),
            "user": _profile_data(user),
            "is_new_user": not bool(user.is_email_verified),
        },
    }


# ─────────────────────────────────────────────
# CURRENT USER INFO
# ─────────────────────────────────────────────

@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return full profile of the authenticated user."""
    user = user_crud.get_with_profile(db, user_id=current_user.id)
    return {"success": True, "data": _profile_data(user)}


# ─────────────────────────────────────────────
# DEV ONLY
# ─────────────────────────────────────────────

@router.post("/dev/verify-all")
def dev_verify_all(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """DEV ONLY — instantly verify email + phone and activate account."""
    if settings.APP_ENV == "production":
        raise NotFoundException("Endpoint")

    current_user.is_email_verified = True
    current_user.is_phone_verified = True
    current_user.status = UserStatus.ACTIVE
    db.commit()
    db.refresh(current_user)

    return {
        "success": True,
        "data": {
            "message":  "Verified and activated.",
            "user_id":  str(current_user.id),
            "email":    current_user.email,
        },
    }