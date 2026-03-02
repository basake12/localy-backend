from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.models.business_model import BusinessCategoryEnum, VerificationBadgeEnum


class BusinessBase(BaseModel):
    business_name: str = Field(..., min_length=3, max_length=255)
    category: BusinessCategoryEnum
    subcategory: Optional[str] = None
    description: Optional[str] = None
    address: str
    local_government: str
    state: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    business_phone: Optional[str] = None
    business_email: Optional[str] = None
    website: Optional[str] = None


class BusinessCreate(BusinessBase):
    pass


class BusinessUpdate(BaseModel):
    business_name: Optional[str] = None
    description: Optional[str] = None
    business_phone: Optional[str] = None
    business_email: Optional[str] = None
    website: Optional[str] = None
    logo: Optional[str] = None
    banner_image: Optional[str] = None


class BusinessOut(BusinessBase):
    id: UUID
    user_id: UUID
    logo: Optional[str] = None
    banner_image: Optional[str] = None
    verification_badge: VerificationBadgeEnum
    subscription_tier: str
    is_featured: bool
    average_rating: Decimal
    total_reviews: int
    total_orders: int
    is_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BusinessListOut(BaseModel):
    businesses: List[BusinessOut]
    total: int
    page: int
    page_size: int






