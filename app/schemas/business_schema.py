from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, time
from decimal import Decimal

from app.models.business_model import BusinessCategoryEnum, VerificationBadgeEnum


# ─── Business Hours ────────────────────────────────────────────────────────

class BusinessHoursDayOut(BaseModel):
    day_of_week: int
    is_open: bool
    open_time: Optional[time] = None
    close_time: Optional[time] = None

    model_config = ConfigDict(from_attributes=True)


# ─── Base ──────────────────────────────────────────────────────────────────

class BusinessBase(BaseModel):
    business_name: str = Field(..., min_length=3, max_length=255)
    category: BusinessCategoryEnum
    subcategory: Optional[str] = None
    description: Optional[str] = None
    address: str
    city: Optional[str] = None
    local_government: str
    state: str
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    business_phone: Optional[str] = None
    business_email: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    facebook: Optional[str] = None
    whatsapp: Optional[str] = None
    opening_hours: Optional[str] = None  # JSON string


class BusinessCreate(BusinessBase):
    pass


# ─── Update — all fields optional, full editable surface ──────────────────

class BusinessUpdate(BaseModel):
    business_name: Optional[str] = Field(None, min_length=3, max_length=255)
    subcategory: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    local_government: Optional[str] = None
    state: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    business_phone: Optional[str] = None
    business_email: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    facebook: Optional[str] = None
    whatsapp: Optional[str] = None
    opening_hours: Optional[str] = None  # JSON string
    logo: Optional[str] = None
    banner_image: Optional[str] = None


# ─── Response ──────────────────────────────────────────────────────────────

class BusinessOut(BaseModel):
    id: UUID
    user_id: UUID
    business_name: str
    category: BusinessCategoryEnum
    subcategory: Optional[str] = None
    description: Optional[str] = None
    address: str
    city: Optional[str] = None
    local_government: str
    state: str
    country: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    business_phone: Optional[str] = None
    business_email: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    facebook: Optional[str] = None
    whatsapp: Optional[str] = None
    opening_hours: Optional[str] = None
    logo: Optional[str] = None
    banner_image: Optional[str] = None

    # Subscription / visibility
    verification_badge: VerificationBadgeEnum
    # subscription_tier is NULL in DB for seeded businesses — must be Optional
    subscription_tier: Optional[str] = None
    is_featured: bool
    featured_until: Optional[datetime] = None

    # Stats
    average_rating: Decimal
    total_reviews: int
    total_orders: int
    response_time_minutes: Optional[int] = None

    # Status
    is_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Nested hours
    business_hours: List[BusinessHoursDayOut] = []

    # FIX: distance_km is dynamically set by business_crud.get_nearby_businesses()
    # using PostGIS ST_Distance. Without this field the distance is silently
    # dropped from every discovery/search API response, breaking Flutter listing
    # cards that display "X km away".
    distance_km: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class BusinessListOut(BaseModel):
    businesses: List[BusinessOut]
    total: int
    page: int
    page_size: int