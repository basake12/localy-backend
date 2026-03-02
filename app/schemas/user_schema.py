from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional
from datetime import datetime
from uuid import UUID

from app.core.constants import UserType, UserStatus
from app.schemas.common_schema import LocationSchema


class UserBase(BaseModel):
    """Base user schema"""
    email: EmailStr
    phone: str
    user_type: UserType


class UserResponse(UserBase):
    """User response schema"""
    id: UUID
    is_email_verified: bool
    is_phone_verified: bool
    status: UserStatus
    created_at: datetime
    last_login: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class CustomerProfileResponse(BaseModel):
    """Customer profile response"""
    id: UUID
    first_name: str
    last_name: str
    date_of_birth: Optional[datetime]
    gender: Optional[str]
    profile_picture: Optional[str]
    bio: Optional[str]
    local_government: Optional[str]
    state: Optional[str]
    country: str

    model_config = ConfigDict(from_attributes=True)


class UpdateCustomerProfileRequest(BaseModel):
    """Update customer profile request"""
    first_name: Optional[str] = Field(None, min_length=2, max_length=100)
    last_name: Optional[str] = Field(None, min_length=2, max_length=100)
    date_of_birth: Optional[datetime] = None
    gender: Optional[str] = None
    bio: Optional[str] = None
    location: Optional[LocationSchema] = None
    local_government: Optional[str] = None
    state: Optional[str] = None


class UserWithProfileResponse(UserResponse):
    """User with profile data"""
    customer_profile: Optional[CustomerProfileResponse] = None