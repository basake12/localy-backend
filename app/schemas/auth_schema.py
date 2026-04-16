"""
app/schemas/auth_schema.py

Pydantic v2 models for all auth request/response payloads.

FIXES vs previous version:
  1.  email is now Optional in _BaseRegister.
      Blueprint §3.1 step 3: "Email address (optional, indexed for search
      if provided)."

  2.  date_of_birth in CustomerRegisterRequest is now required (not Optional).
      Blueprint §3.1 step 3: "Date of birth (required — used for age
      verification)."

  3.  [HARD RULE] local_government removed from BusinessRegisterRequest.
      [HARD RULE] local_government removed from RegisterRequest.
      Blueprint §4 / §2: no LGA column or parameter anywhere.

  4.  [HARD RULE] AdminRegisterRequest DELETED.
      Blueprint §2: "Admin cannot register through mobile app or
      self-provision an account." Admin provisioning is a separate
      out-of-band process in the admin web app.

  5.  ForgotPasswordRequest changed to phone-only.
      Blueprint §3.2: "FORGOT PASSWORD: Enter phone number → Receive SMS OTP
      (same Termii gateway)." No email option.

  6.  OTP channel default changed from "both" → "phone" throughout.
      Blueprint §3.1: OTP is sent via Termii SMS. Email OTP is not part
      of the blueprint registration flow.

  7.  pin field added to RegisterRequest — enables mandatory PIN collection
      at the same time as profile details (Step 6 is mandatory).
      Flutter must send the PIN in the registration completion payload.

  8.  terms_version removed as a hardcoded string — it's looked up from the
      DB at runtime by the service layer, not hardcoded in the schema.

  9.  vehicle_type validator updated to include all 4 blueprint types:
      motorcycle / bicycle / car / van (Blueprint §3.1 step 5b).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.constants import BusinessCategory

# Allowed OTP delivery channels
# Blueprint §3.1: SMS via Termii is the primary channel.
# "phone" is the default — email is secondary/optional.
OtpChannel = Literal["phone", "email", "both"]


# ─── Subcategory map (unchanged — no blueprint conflict) ──────────────────────

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


# ─── Shared base ──────────────────────────────────────────────────────────────

class _BaseRegister(BaseModel):
    """Common fields for all registration types."""

    # Blueprint §3.1 step 3: email is OPTIONAL
    email: Optional[EmailStr] = None

    # Blueprint §3.1 step 1: phone number required (primary identifier)
    phone:    str  = Field(..., min_length=10, max_length=20)
    password: str  = Field(..., min_length=8)

    # Blueprint §3.1 step 6: PIN is MANDATORY — collected at registration
    # "MANDATORY. Cannot be skipped. No 'do it later' option."
    pin: str = Field(..., min_length=4, max_length=4, description="4-digit transaction PIN")

    # Blueprint §3.1 step 8: T&C acceptance required
    terms_accepted: bool = Field(
        ..., description="User must accept Terms & Conditions"
    )

    # OTP channel: 'phone' is default per blueprint (SMS via Termii)
    otp_channel: OtpChannel = "phone"

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

    @field_validator("pin")
    @classmethod
    def pin_must_be_4_digits(cls, v: str) -> str:
        if not (len(v) == 4 and v.isdigit()):
            raise ValueError("PIN must be exactly 4 numeric digits")
        return v

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v: str) -> str:
        digits = v.replace("+", "").replace(" ", "").replace("-", "")
        if not digits.isdigit():
            raise ValueError(
                "Phone must contain only digits (optional +/spaces/dashes)"
            )
        return v

    @field_validator("terms_accepted")
    @classmethod
    def terms_must_be_accepted(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "You must accept the Terms & Conditions to register"
            )
        return v


# ─── Customer Registration ────────────────────────────────────────────────────

class CustomerRegisterRequest(_BaseRegister):
    first_name: str  = Field(..., min_length=2, max_length=100)
    last_name:  str  = Field(..., min_length=2, max_length=100)

    # Blueprint §3.1 step 3: date_of_birth REQUIRED (not Optional)
    date_of_birth: date = Field(..., description="Date of birth (YYYY-MM-DD)")

    referral_code: Optional[str] = Field(None, max_length=20)

    @field_validator("date_of_birth")
    @classmethod
    def validate_age(cls, v: date) -> date:
        """Minimum age validation."""
        from datetime import date as date_type
        today = date_type.today()
        age   = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < 13:
            raise ValueError("You must be at least 13 years old to register")
        return v


# ─── Business Registration ────────────────────────────────────────────────────

class BusinessHoursDay(BaseModel):
    open:   Optional[str] = None   # "09:00"
    close:  Optional[str] = None   # "21:00"
    closed: bool          = False


class BusinessRegisterRequest(_BaseRegister):
    business_name:        str              = Field(..., min_length=2, max_length=200)
    business_category:    BusinessCategory
    business_subcategory: Optional[str]    = None
    address:              str              = Field(..., min_length=5, max_length=500)
    city:                 str              = Field(..., min_length=2, max_length=100)
    # REMOVED: local_government — Blueprint HARD RULE: no LGA anywhere.
    state:                str              = Field(..., min_length=2, max_length=100)

    # GPS coordinates — used to geocode business location
    # Blueprint §3.1 step 5a: "Address is geocoded immediately to PostGIS point
    # via Google Geocoding API." Lat/lng sent by Flutter or geocoded server-side.
    latitude:  Optional[float] = Field(None, ge=-90,  le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)

    # Optional profile fields
    description: Optional[str] = Field(None, max_length=2000)
    website:     Optional[str] = None
    instagram:   Optional[str] = None
    facebook:    Optional[str] = None
    whatsapp:    Optional[str] = None
    opening_hours: Optional[Dict[str, BusinessHoursDay]] = None

    @model_validator(mode="after")
    def check_subcategory(self) -> "BusinessRegisterRequest":
        validate_subcategory(
            self.business_category.value, self.business_subcategory
        )
        return self


# ─── Rider Registration ───────────────────────────────────────────────────────

class RiderRegisterRequest(_BaseRegister):
    first_name: str = Field(..., min_length=2, max_length=100)
    last_name:  str = Field(..., min_length=2, max_length=100)

    # Blueprint §3.1 step 5b: vehicle type + government ID
    # vehicle_type: motorcycle | bicycle | car | van (Blueprint §3.1 step 5b)
    vehicle_type:         str            = Field(..., description="motorcycle | bicycle | car | van")
    vehicle_plate_number: str            = Field(..., min_length=4, max_length=20)
    vehicle_color:        Optional[str]  = None
    vehicle_model:        Optional[str]  = None
    # gov_id_url stored separately after S3 upload (rider uploads doc to S3,
    # then passes the URL in this field)
    gov_id_url:           Optional[str]  = None

    @field_validator("vehicle_type")
    @classmethod
    def vehicle_type_valid(cls, v: str) -> str:
        allowed = {"bicycle", "motorcycle", "car", "van"}
        if v.lower() not in allowed:
            raise ValueError(f"vehicle_type must be one of: {sorted(allowed)}")
        return v.lower()


# REMOVED: AdminRegisterRequest
# Blueprint §2 HARD RULE: "Admin cannot register through mobile app or
# self-provision an account." Admin accounts are provisioned via the admin
# web app by a super-admin — no mobile endpoint exists for this.


# ─── Internal RegisterRequest (used by service layer / crud) ─────────────────

class RegisterRequest(BaseModel):
    """
    Internal payload assembled by each registration endpoint.
    Passed to auth_service.register_user().
    """
    role:           str                      # "customer" | "business" | "rider"
    phone:          str
    password:       str
    pin:            str                      # 4-digit PIN — mandatory
    terms_accepted: bool = True
    otp_channel:    OtpChannel = "phone"

    # Blueprint §14 / user model fields
    full_name:     Optional[str] = None      # for user.full_name
    date_of_birth: Optional[date] = None     # for user.date_of_birth
    email:         Optional[str] = None      # optional

    # Referral
    referral_code: Optional[str] = None

    # Customer-specific
    first_name:    Optional[str] = None
    last_name:     Optional[str] = None

    # Business-specific
    business_name:        Optional[str] = None
    business_category:    Optional[str] = None
    business_subcategory: Optional[str] = None
    address:              Optional[str] = None    # registered_address on business
    city:                 Optional[str] = None
    # REMOVED: local_government — Blueprint HARD RULE: no LGA anywhere.
    state:                Optional[str] = None
    latitude:             Optional[float] = None
    longitude:            Optional[float] = None
    description:          Optional[str] = None
    website:              Optional[str] = None
    instagram:            Optional[str] = None
    facebook:             Optional[str] = None
    whatsapp:             Optional[str] = None
    opening_hours:        Optional[Dict[str, Any]] = None

    # Rider-specific
    vehicle_type:         Optional[str] = None
    vehicle_plate_number: Optional[str] = None
    vehicle_color:        Optional[str] = None
    vehicle_model:        Optional[str] = None
    gov_id_url:           Optional[str] = None


# ─── Login ────────────────────────────────────────────────────────────────────

def _is_email(value: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", value))


class LoginRequest(BaseModel):
    """
    Blueprint §3.2: phone number + password.
    NOTE: The identifier field accepts phone number only per blueprint.
    Email login is removed from the flow. The resolver is kept for
    defensive validation but phone is the canonical method.
    """
    identifier: str = Field(..., min_length=5, max_length=255)
    password:   str

    resolved_email: Optional[str] = None
    resolved_phone: Optional[str] = None

    @model_validator(mode="after")
    def resolve_identifier(self) -> "LoginRequest":
        v = self.identifier.strip()
        if _is_email(v):
            self.resolved_email = v.lower()
        else:
            self.resolved_phone = v
        return self


# ─── PIN Management (Blueprint §3.1 step 6 / §3.3) ───────────────────────────

class SetupPinRequest(BaseModel):
    """Set/reset 4-digit PIN from security settings (not registration — PIN is in RegisterRequest)."""
    pin: str = Field(..., min_length=4, max_length=4)

    @field_validator("pin")
    @classmethod
    def pin_must_be_4_digits(cls, v: str) -> str:
        if not (len(v) == 4 and v.isdigit()):
            raise ValueError("PIN must be exactly 4 digits")
        return v


class VerifyPinRequest(BaseModel):
    """
    Verify PIN for a wallet transaction.
    Blueprint §3.3: required for ALL wallet transactions + withdrawals
    + any payment above ₦5,000.
    """
    pin: str = Field(..., min_length=4, max_length=4)


class ChangePinRequest(BaseModel):
    """
    Change PIN — requires current PIN + OTP confirmation.
    Blueprint §3.3: "Changeable from security settings
    (requires current PIN + OTP confirmation from registered phone)."
    """
    old_pin: str = Field(..., min_length=4, max_length=4)
    new_pin: str = Field(..., min_length=4, max_length=4)
    otp:     str = Field(..., min_length=6, max_length=6, description="SMS OTP from registered phone")

    @field_validator("new_pin")
    @classmethod
    def new_pin_must_be_4_digits(cls, v: str) -> str:
        if not (len(v) == 4 and v.isdigit()):
            raise ValueError("New PIN must be exactly 4 digits")
        return v


class PinLoginRequest(BaseModel):
    """
    Quick PIN login after first session.
    Blueprint §3.2: "PIN login → validates against bcrypt hash → issues new session tokens."
    """
    identifier: str = Field(..., description="Phone number")
    pin:        str = Field(..., min_length=4, max_length=4)


# ─── Biometric (Blueprint §3.1 step 7 / §3.3) ────────────────────────────────

class EnableBiometricRequest(BaseModel):
    """
    Enable biometric authentication.
    Blueprint §3.1 step 7: "Only presented AFTER PIN is confirmed active."
    Server stores only biometric_flag BOOLEAN — no biometric data reaches server.
    """
    device_id:      str                                      = Field(..., description="Unique device identifier")
    biometric_type: Literal["face_id", "fingerprint", "other"] = Field(...)


class DisableBiometricRequest(BaseModel):
    """Disable biometric authentication."""
    device_id: Optional[str] = None


# ─── OTP Verification ─────────────────────────────────────────────────────────

class VerifyPhoneRequest(BaseModel):
    """Verify phone OTP — 6-digit code from Termii SMS."""
    otp: str = Field(..., min_length=6, max_length=6)


class ResendOTPRequest(BaseModel):
    """Resend OTP — Blueprint §3.1: resend available after 60 seconds."""
    channel: OtpChannel = "phone"


# ─── Password Reset — Blueprint §3.2: phone only ─────────────────────────────

class ForgotPasswordRequest(BaseModel):
    """
    Blueprint §3.2: "FORGOT PASSWORD: Enter phone number →
    Receive SMS OTP (same Termii gateway, same TTL rules)."
    Phone only — no email option.
    """
    phone: str = Field(..., min_length=10, max_length=20)


class VerifyResetOTPRequest(BaseModel):
    """Validate password-reset OTP (sent to phone) and return a reset_token."""
    phone: str = Field(..., min_length=10, max_length=20)
    otp:   str = Field(..., min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    """
    Set new password using the reset_token.
    Blueprint §3.2: "All existing session tokens invalidated on password reset."
    """
    reset_token:      str
    new_password:     str = Field(..., min_length=8)
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


# ─── Token Response ───────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int


class RefreshTokenRequest(BaseModel):
    refresh_token: str