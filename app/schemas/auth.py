"""
Auth schemas for Localy.
Pydantic v2 models for all auth request/response payloads.
"""
from __future__ import annotations

from typing import Optional, Literal, Dict, Any
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.constants import UserType, BusinessCategory

# Allowed OTP delivery channels
OtpChannel = Literal["email", "phone", "both"]

# ──────────────────────────────────────────────────────────────
# BUSINESS SUBCATEGORY MAP
# Must stay in sync with constants.BusinessCategory
# ──────────────────────────────────────────────────────────────
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


def validate_subcategory(category: str, subcategory: Optional[str]) -> Optional[str]:
    """Check that subcategory belongs to the given category."""
    if subcategory is None:
        return None
    allowed = SUBCATEGORIES.get(category.lower(), [])
    if subcategory not in allowed:
        raise ValueError(
            f"'{subcategory}' is not a valid subcategory for '{category}'. "
            f"Allowed: {allowed}"
        )
    return subcategory


# ──────────────────────────────────────────────────────────────
# SHARED BASE
# ──────────────────────────────────────────────────────────────

class _BaseRegister(BaseModel):
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=20)
    password: str = Field(..., min_length=8)
    otp_channel: OtpChannel = "both"

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v: str) -> str:
        digits = v.replace("+", "").replace(" ", "").replace("-", "")
        if not digits.isdigit():
            raise ValueError("Phone must contain only digits (and optional + prefix)")
        return v


# ──────────────────────────────────────────────────────────────
# CUSTOMER REGISTRATION
# ──────────────────────────────────────────────────────────────

class CustomerRegisterRequest(_BaseRegister):
    first_name: str = Field(..., min_length=2, max_length=100)
    last_name:  str = Field(..., min_length=2, max_length=100)


# ──────────────────────────────────────────────────────────────
# BUSINESS REGISTRATION
# ──────────────────────────────────────────────────────────────

class BusinessHoursDay(BaseModel):
    """Opening hours for a single day."""
    open:  Optional[str] = None   # "09:00"
    close: Optional[str] = None   # "21:00"
    closed: bool         = False


class BusinessRegisterRequest(_BaseRegister):
    # Identity
    business_name: str = Field(..., min_length=2, max_length=200)

    # Category (one of 7 defined)
    business_category: BusinessCategory
    business_subcategory: Optional[str] = None

    # Location
    address:           str = Field(..., min_length=5, max_length=500)
    city:              str = Field(..., min_length=2, max_length=100)
    local_government:  str = Field(..., min_length=2, max_length=100)
    state:             str = Field(..., min_length=2, max_length=100)
    latitude:          Optional[float] = Field(None, ge=-90,  le=90)
    longitude:         Optional[float] = Field(None, ge=-180, le=180)

    # Details (optional but encouraged)
    description:       Optional[str] = Field(None, max_length=2000)
    website:           Optional[str] = None
    instagram:         Optional[str] = None
    facebook:          Optional[str] = None
    whatsapp:          Optional[str] = None

    # Operating hours — keyed by day name
    opening_hours: Optional[Dict[str, BusinessHoursDay]] = None

    @model_validator(mode="after")
    def check_subcategory(self) -> "BusinessRegisterRequest":
        validate_subcategory(self.business_category.value, self.business_subcategory)
        return self


# ──────────────────────────────────────────────────────────────
# RIDER REGISTRATION
# ──────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────
# ADMIN REGISTRATION
# ──────────────────────────────────────────────────────────────

class AdminRegisterRequest(_BaseRegister):
    full_name: str = Field(..., min_length=2, max_length=200)
    role:      str = Field("admin", max_length=50)


# ──────────────────────────────────────────────────────────────
# GENERIC RegisterRequest (used internally by crud)
# ──────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    """Internal register payload — assembled by each endpoint before calling crud."""
    user_type:            UserType
    email:                EmailStr
    phone:                str
    password:             str
    otp_channel:          OtpChannel = "both"

    # Customer / Rider
    first_name:           Optional[str] = None
    last_name:            Optional[str] = None

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

    # OAuth
    oauth_provider:       Optional[str] = None
    oauth_provider_id:    Optional[str] = None


# ──────────────────────────────────────────────────────────────
# LOGIN
# ──────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


# ──────────────────────────────────────────────────────────────
# VERIFICATION
# ──────────────────────────────────────────────────────────────

class VerifyEmailRequest(BaseModel):
    otp: str = Field(..., min_length=6, max_length=6)


class VerifyPhoneRequest(BaseModel):
    otp: str = Field(..., min_length=6, max_length=6)


class ResendOTPRequest(BaseModel):
    channel: OtpChannel = "both"


# ──────────────────────────────────────────────────────────────
# PASSWORD RESET
# ──────────────────────────────────────────────────────────────

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
    email: Optional[EmailStr] = None
    phone: Optional[str]      = None
    otp:   str = Field(..., min_length=6, max_length=6)

    @model_validator(mode="after")
    def at_least_one(self) -> "VerifyResetOTPRequest":
        if not self.email and not self.phone:
            raise ValueError("Provide either email or phone")
        return self


class ResetPasswordRequest(BaseModel):
    reset_token:  str
    new_password: str = Field(..., min_length=8)
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


# ──────────────────────────────────────────────────────────────
# OAUTH
# ──────────────────────────────────────────────────────────────

class GoogleAuthRequest(BaseModel):
    id_token: str
    phone:    Optional[str] = None  # Required if user is new


class AppleAuthRequest(BaseModel):
    identity_token: str
    full_name:      Optional[str] = None  # Only sent on first Apple sign-in
    phone:          Optional[str] = None


# ──────────────────────────────────────────────────────────────
# TOKEN RESPONSE
# ──────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int