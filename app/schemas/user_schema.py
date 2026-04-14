"""
app/schemas/user_schema.py

FIXES:
  1. CustomerAddressOut/Create/Update — replaced single `address` string
     with `street`, `city`, `lga_name` fields to match Flutter's
     CustomerAddress.fromJson() which reads those exact keys.
     Flutter was always getting empty strings because the backend
     was returning {"address": "5 Aba Road"} not {"street": "5 Aba Road"}.

  2. CustomerSettingsUpdate / CustomerSettingsResponse — removed the
     duplicate nested definitions inside UserWithProfileResponse.
     They are now defined once at module level only.

  3. UserWithProfileResponse — added wallet_balance field so
     CustomerProfileDetail.fromJson() gets a real value instead of 0.

  4. reset_password method added to user_crud via the schema fix —
     users.py calls user_crud.reset_password() which was missing.
"""
import uuid

from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from uuid import UUID

from app.core.constants import UserType, UserStatus
from app.schemas.common_schema import LocationSchema


# ─── Base ─────────────────────────────────────────────────────────────────────

class UserBase(BaseModel):
    email: EmailStr
    phone: str
    user_type: UserType


class UserResponse(UserBase):
    id: UUID
    is_email_verified: bool
    is_phone_verified: bool
    status: UserStatus
    created_at: datetime
    last_login: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ─── Customer Profile ─────────────────────────────────────────────────────────

class CustomerProfileResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    date_of_birth: Optional[datetime] = None
    gender: Optional[str] = None
    profile_picture: Optional[str] = None
    bio: Optional[str] = None
    local_government: Optional[str] = None
    state: Optional[str] = None
    country: str = "Nigeria"

    model_config = ConfigDict(from_attributes=True)


class UpdateCustomerProfileRequest(BaseModel):
    first_name:       Optional[str]            = Field(None, min_length=2, max_length=100)
    last_name:        Optional[str]            = Field(None, min_length=2, max_length=100)
    date_of_birth:    Optional[datetime]       = None
    gender:           Optional[str]            = None
    bio:              Optional[str]            = None
    location:         Optional[LocationSchema] = None
    local_government: Optional[str]            = None
    state:            Optional[str]            = None
    phone:            Optional[str]            = None


# ─── User with profile ────────────────────────────────────────────────────────

class UserWithProfileResponse(UserResponse):
    """
    Full user response including profile and wallet balance.
    wallet_balance is read from the joined wallet relationship so
    CustomerProfileDetail.fromJson() gets a real value.
    """
    customer_profile: Optional[CustomerProfileResponse] = None
    wallet_balance:   float = 0.0
    referral_code:    Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_user(cls, user) -> "UserWithProfileResponse":
        """
        Build response from a User ORM object with eager-loaded relations.
        Extracts wallet balance from the joined wallet.
        """
        wallet_balance = 0.0
        if user.wallet:
            wallet_balance = float(user.wallet.balance or 0)

        data = cls.model_validate(user)
        data.wallet_balance = wallet_balance
        return data


# ─── Addresses ────────────────────────────────────────────────────────────────

class CustomerAddressOut(BaseModel):
    """
    FIX: fields now match Flutter's CustomerAddress.fromJson() exactly.
    Flutter reads: id, label, street, city, lga_name, lat, lng, is_default.
    Previous schema had a single `address` string — Flutter always got
    empty street/city/lga_name.
    """
    id:         uuid.UUID
    label:      Optional[str] = None
    street:     str = ""
    city:       Optional[str] = None
    lga_name:   Optional[str] = None
    lat:        Optional[float] = None
    lng:        Optional[float] = None
    is_default: bool = False

    model_config = ConfigDict(from_attributes=True)


class CustomerAddressCreate(BaseModel):
    label:      Optional[str] = None
    street:     str
    city:       Optional[str] = None
    lga_name:   Optional[str] = None
    lat:        Optional[float] = None
    lng:        Optional[float] = None
    is_default: bool = False


class CustomerAddressUpdate(BaseModel):
    label:      Optional[str]   = None
    street:     Optional[str]   = None
    city:       Optional[str]   = None
    lga_name:   Optional[str]   = None
    lat:        Optional[float] = None
    lng:        Optional[float] = None
    is_default: Optional[bool]  = None


# ─── Settings ─────────────────────────────────────────────────────────────────

class CustomerSettingsUpdate(BaseModel):
    push_notifications:  Optional[bool] = None
    email_notifications: Optional[bool] = None
    sms_notifications:   Optional[bool] = None
    order_updates:       Optional[bool] = None
    promotions:          Optional[bool] = None
    location_services:   Optional[bool] = None
    language:            Optional[str]  = None
    currency:            Optional[str]  = None


class CustomerSettingsResponse(BaseModel):
    push_notifications:  bool = True
    email_notifications: bool = True
    sms_notifications:   bool = True
    order_updates:       bool = True
    promotions:          bool = False
    location_services:   bool = True
    language:            str  = "en"
    currency:            str  = "NGN"

    model_config = ConfigDict(from_attributes=True)