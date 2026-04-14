"""
Auth schemas for Localy.
Pydantic v2 models for all auth request/response payloads.

Blueprint v2.0 changes:
- OAuth removed entirely (Google/Apple Sign-In removed)
- PIN setup/verify/change added
- date_of_birth added to customer registration
- terms_accepted added to all registrations
- Biometric enrollment added
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional, Literal, Dict, Any
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.constants import UserType, BusinessCategory

# Allowed OTP delivery channels
OtpChannel = Literal["email", "phone", "both"]

# ─── Subcategory map ─────────────────────────────────────────────────────
SUBCATEGORIES: Dict[str, list[str]] = {
    "lodges": [
        "Hotel", "Guest House", "Motel", "Shortlet / Airbnb",
        "Hostel", "Resort", "Lodge",
    ],
    "food": [
        "Restaurant", "Fast Food", "Buka / Local Food", "Bakery",
        "Cafe / Coffee Shop", "Bar / Lounge", "Catering Service",
        "Cloud Kitchen", "Food Truck", "Dessert Shop",
    ],
    "services": [
        "Salon / Barber", "Laundry / Dry Cleaning", "Photography",
        "Event Planning", "Cleaning Service", "Auto Repair / Mechanic",
        "Tailoring / Fashion", "Printing / Branding",
        "IT / Tech Support", "Home Repairs / Contractor",
        "Security Services", "Logistics / Courier",
    ],
    "products": [
        "Fashion & Clothing", "Electronics", "Groceries / Supermarket",
        "Furniture / Home Decor", "Books & Stationery",
        "Sporting Goods", "Beauty & Cosmetics",
        "Baby & Kids Items", "Agriculture / Farm Produce",
        "Phone Accessories", "Hardware / Tools",
    ],
    "health": [
        "Hospital / Clinic", "Pharmacy", "Laboratory / Diagnostics",
        "Dental Clinic", "Optician / Eye Care", "Physiotherapy",
        "Mental Health / Counselling", "Veterinary Clinic",
        "Fitness / Gym", "Nutrition & Dietetics",
    ],
    "property_agent": [
        "Residential Sales", "Residential Rentals",
        "Commercial Sales", "Commercial Rentals",
        "Land Sales", "Property Management", "Short-let Management",
    ],
    "ticket_sales": [
        "Concert / Music", "Sports Event", "Comedy Show",
        "Conference / Seminar", "Festival", "Party / Club Night",
        "Movie / Cinema", "Transportation Ticket",
    ],
}


def validate_subcategory(
    category: str, subcategory: Optional[str]
) -> Optional[str]:
    if subcategory is None:
        return None
    allowed = SUBCATEGORIES.get(category.lower(), [])
    if subcategory not in allowed:
        raise ValueError(
            f"'{subcategory}' is not a valid subcategory for '{category}'. "
            f"Allowed: {allowed}"
        )
    return subcategory


# ─── Shared base ─────────────────────────────────────────────────────────

class _BaseRegister(BaseModel):
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=20)
    password: str = Field(..., min_length=8)
    otp_channel: OtpChannel = "both"
    terms_accepted: bool = Field(..., description="User must accept Terms & Conditions")

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        errors = []
        if not any(c.isupper() for c in v):
            errors.append("at least one uppercase letter")
        if not any(c.islower() for c in v):
            errors.append("at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            errors.append("at least one digit")
        if errors:
            raise ValueError(f"Password must contain {', '.join(errors)}")
        return v

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v: str) -> str:
        digits = v.replace("+", "").replace(" ", "").replace("-", "")
        if not digits.isdigit():
            raise ValueError("Phone must contain only digits (and optional +/spaces/dashes)")
        return v

    @field_validator("terms_accepted")
    @classmethod
    def terms_must_be_accepted(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must accept the Terms & Conditions to register")
        return v


# ─── Customer Registration ────────────────────────────────────────────────

class CustomerRegisterRequest(_BaseRegister):
    first_name: str = Field(..., min_length=2, max_length=100)
    last_name:  str = Field(..., min_length=2, max_length=100)
    date_of_birth: Optional[date] = Field(None, description="Date of birth (YYYY-MM-DD)")
    referral_code: Optional[str] = Field(None, max_length=20)

    @field_validator("date_of_birth")
    @classmethod
    def validate_age(cls, v: Optional[date]) -> Optional[date]:
        """Ensure user is at least 13 years old if date_of_birth is provided."""
        if v is None:
            return v
        from datetime import date as date_type, timedelta
        today = date_type.today()
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < 13:
            raise ValueError("You must be at least 13 years old to register")
        return v


# ─── Business Registration ────────────────────────────────────────────────

class BusinessHoursDay(BaseModel):
    open:   Optional[str] = None   # "09:00"
    close:  Optional[str] = None   # "21:00"
    closed: bool          = False


class BusinessRegisterRequest(_BaseRegister):
    business_name:        str = Field(..., min_length=2, max_length=200)
    business_category:    BusinessCategory
    business_subcategory: Optional[str] = None
    address:              str = Field(..., min_length=5, max_length=500)
    city:                 str = Field(..., min_length=2, max_length=100)
    local_government:     str = Field(..., min_length=2, max_length=100)
    state:                str = Field(..., min_length=2, max_length=100)
    latitude:             Optional[float] = Field(None, ge=-90,  le=90)
    longitude:            Optional[float] = Field(None, ge=-180, le=180)
    description:          Optional[str]  = Field(None, max_length=2000)
    website:              Optional[str]  = None
    instagram:            Optional[str]  = None
    facebook:             Optional[str]  = None
    whatsapp:             Optional[str]  = None
    opening_hours:        Optional[Dict[str, BusinessHoursDay]] = None

    @model_validator(mode="after")
    def check_subcategory(self) -> "BusinessRegisterRequest":
        validate_subcategory(self.business_category.value, self.business_subcategory)
        return self


# ─── Rider Registration ───────────────────────────────────────────────────

class RiderRegisterRequest(_BaseRegister):
    first_name:           str = Field(..., min_length=2, max_length=100)
    last_name:            str = Field(..., min_length=2, max_length=100)
    vehicle_type:         str = Field(..., description="bicycle | motorcycle | car | van")
    vehicle_plate_number: str = Field(..., min_length=4, max_length=20)
    vehicle_color:        Optional[str] = None
    vehicle_model:        Optional[str] = None

    @field_validator("vehicle_type")
    @classmethod
    def vehicle_type_valid(cls, v: str) -> str:
        allowed = {"bicycle", "motorcycle", "car", "van"}
        if v.lower() not in allowed:
            raise ValueError(f"vehicle_type must be one of {allowed}")
        return v.lower()


# ─── Admin Registration ───────────────────────────────────────────────────

class AdminRegisterRequest(_BaseRegister):
    full_name: str = Field(..., min_length=2, max_length=200)
    role:      str = Field("admin", max_length=50)


# ─── Internal RegisterRequest (used by crud) ─────────────────────────────

class RegisterRequest(BaseModel):
    """Internal payload assembled by each endpoint before calling crud."""
    user_type:            UserType
    email:                EmailStr
    phone:                str
    password:             str
    otp_channel:          OtpChannel = "both"
    terms_accepted:       bool = True
    terms_version:        str = "v1.0"  # Current terms version

    # Customer / Rider
    first_name:           Optional[str] = None
    last_name:            Optional[str] = None
    date_of_birth:        Optional[date] = None
    referral_code:        Optional[str] = None

    # Business
    business_name:        Optional[str] = None
    business_category:    Optional[BusinessCategory] = None
    business_subcategory: Optional[str] = None
    address:              Optional[str] = None
    city:                 Optional[str] = None
    local_government:     Optional[str] = None
    state:                Optional[str] = None
    latitude:             Optional[float] = None
    longitude:            Optional[float] = None
    description:          Optional[str] = None
    website:              Optional[str] = None
    instagram:            Optional[str] = None
    facebook:             Optional[str] = None
    whatsapp:             Optional[str] = None
    opening_hours:        Optional[Dict[str, Any]] = None

    # Rider extras
    vehicle_type:         Optional[str] = None
    vehicle_plate_number: Optional[str] = None
    vehicle_color:        Optional[str] = None
    vehicle_model:        Optional[str] = None

    # Admin
    full_name:            Optional[str] = None
    role:                 Optional[str] = None


# ─── Login ────────────────────────────────────────────────────────────────

def _is_email(value: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", value))


class LoginRequest(BaseModel):
    """
    Accepts email OR phone number as `identifier`.

    Frontend sends: { "identifier": "user@example.com", "password": "..." }
                or: { "identifier": "08012345678", "password": "..." }
    """
    identifier: str = Field(..., min_length=5, max_length=255)
    password:   str

    # Resolved fields — populated by validator, used by crud.authenticate()
    resolved_email: Optional[str] = None
    resolved_phone: Optional[str] = None

    @model_validator(mode="after")
    def resolve_identifier(self) -> "LoginRequest":
        v = self.identifier.strip()
        if _is_email(v):
            self.resolved_email = v.lower()
        else:
            # Treat as phone
            self.resolved_phone = v
        return self


# ─── PIN Management (Blueprint v2.0) ──────────────────────────────────────

class SetupPinRequest(BaseModel):
    """
    Set up 4-digit PIN during registration flow.
    
    Blueprint: "Set 4-digit transaction PIN (mandatory — enables wallet and payments)"
    """
    pin: str = Field(..., min_length=4, max_length=4, description="4-digit PIN")

    @field_validator("pin")
    @classmethod
    def pin_must_be_4_digits(cls, v: str) -> str:
        if not (len(v) == 4 and v.isdigit()):
            raise ValueError("PIN must be exactly 4 digits")
        return v


class VerifyPinRequest(BaseModel):
    """
    Verify PIN for wallet transactions or PIN login.
    
    Blueprint: "PIN is required for all wallet transactions, withdrawals, 
    and payments above ₦5,000"
    """
    pin: str = Field(..., min_length=4, max_length=4)


class ChangePinRequest(BaseModel):
    """Change existing PIN — requires old PIN for security."""
    old_pin: str = Field(..., min_length=4, max_length=4)
    new_pin: str = Field(..., min_length=4, max_length=4)

    @field_validator("new_pin")
    @classmethod
    def new_pin_must_be_4_digits(cls, v: str) -> str:
        if not (len(v) == 4 and v.isdigit()):
            raise ValueError("New PIN must be exactly 4 digits")
        return v


class PinLoginRequest(BaseModel):
    """
    PIN login for quick access after first session.
    
    Blueprint: "PIN login (quick access after first session)"
    """
    identifier: str = Field(..., description="Email or phone number")
    pin: str = Field(..., min_length=4, max_length=4)


# ─── Biometric (Blueprint v2.0) ───────────────────────────────────────────

class EnableBiometricRequest(BaseModel):
    """
    Enable biometric authentication (Face ID / fingerprint).
    
    Blueprint: "Optional: enable biometric authentication (Face ID / fingerprint) 
    after PIN is set"
    
    Note: Biometric enrollment happens client-side. This endpoint just stores
    the flag that biometric is enabled for this user on this device.
    """
    device_id: str = Field(..., description="Unique device identifier")
    biometric_type: Literal["face_id", "fingerprint", "other"] = Field(
        ..., description="Type of biometric authentication"
    )


class DisableBiometricRequest(BaseModel):
    """Disable biometric authentication."""
    device_id: Optional[str] = None  # If None, disable for all devices


# ─── Verification ─────────────────────────────────────────────────────────

class VerifyEmailRequest(BaseModel):
    otp: str = Field(..., min_length=6, max_length=6)


class VerifyPhoneRequest(BaseModel):
    otp: str = Field(..., min_length=6, max_length=6)


class ResendOTPRequest(BaseModel):
    channel: OtpChannel = "both"


# ─── Password Reset ───────────────────────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    """At least one of email or phone must be provided."""
    email:   Optional[EmailStr] = None
    phone:   Optional[str]      = None
    channel: OtpChannel         = "email"

    @model_validator(mode="after")
    def at_least_one(self) -> "ForgotPasswordRequest":
        if not self.email and not self.phone:
            raise ValueError("Provide either email or phone")
        return self


class VerifyResetOTPRequest(BaseModel):
    """Validate the password-reset OTP and exchange it for a reset_token."""
    email: Optional[EmailStr] = None
    phone: Optional[str]      = None
    otp:   str = Field(..., min_length=6, max_length=6)

    @model_validator(mode="after")
    def at_least_one(self) -> "VerifyResetOTPRequest":
        if not self.email and not self.phone:
            raise ValueError("Provide either email or phone")
        return self


class ResetPasswordRequest(BaseModel):
    reset_token:      str
    new_password:     str = Field(..., min_length=8)
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


# ─── Token Response ───────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int


class RefreshTokenRequest(BaseModel):
    refresh_token: str