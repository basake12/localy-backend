# app/schemas/services_schema.py

from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID

from app.schemas.common_schema import LocationSchema


# ============================================
# SERVICE PROVIDER SCHEMAS
# ============================================

class ServiceProviderCreateRequest(BaseModel):
    qualifications: List[str] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    years_of_experience: Optional[int] = Field(None, ge=0)
    service_location_types: List[str] = Field(..., min_length=1)
    service_radius_km: Optional[Decimal] = Field(None, gt=0)
    travel_fee: Decimal = Field(default=Decimal("0.00"), ge=0)
    provider_address: Optional[str] = None
    provider_location: Optional[LocationSchema] = None
    advance_booking_days: int = Field(default=30, ge=1, le=90)
    buffer_time_minutes: int = Field(default=15, ge=0, le=60)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "qualifications": ["Certified Beautician", "5 years experience"],
            "certifications": ["ISO 9001 Certified"],
            "years_of_experience": 5,
            "service_location_types": ["in_home", "provider_location"],
            "service_radius_km": 15.0,
            "travel_fee": 2000.00,
            "provider_address": "123 Salon Street, Wuse 2, Abuja",
            "advance_booking_days": 30,
            "buffer_time_minutes": 15
        }
    })


class ServiceProviderResponse(BaseModel):
    id: UUID
    business_id: UUID
    qualifications: List[str]
    certifications: List[str]
    portfolio_images: List[str]
    years_of_experience: Optional[int]
    service_location_types: List[str]
    service_radius_km: Optional[Decimal]
    travel_fee: Decimal
    provider_address: Optional[str]
    advance_booking_days: int
    buffer_time_minutes: int
    total_bookings: int
    completion_rate: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# SERVICE SCHEMAS
# ============================================

class ServiceCreateRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    category: str = Field(..., min_length=2, max_length=100)
    subcategory: Optional[str] = None
    base_price: Decimal = Field(..., gt=0)
    pricing_type: str = "fixed"
    duration_minutes: Optional[int] = Field(None, gt=0)
    service_options: List[Dict[str, Any]] = Field(default_factory=list)
    images: List[str] = Field(default_factory=list)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "Women's Haircut & Styling",
            "description": "Professional haircut with styling",
            "category": "beauty",
            "subcategory": "hair",
            "base_price": 15000.00,
            "pricing_type": "fixed",
            "duration_minutes": 60,
            "service_options": [
                {
                    "name": "Hair Length",
                    "type": "select",
                    "options": ["Short", "Medium", "Long"],
                    "price_modifier": [0, 5000, 10000]
                }
            ],
            "images": ["https://example.com/haircut.jpg"]
        }
    })


class ServiceUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = None
    base_price: Optional[Decimal] = Field(None, gt=0)
    duration_minutes: Optional[int] = Field(None, gt=0)
    service_options: Optional[List[Dict[str, Any]]] = None
    images: Optional[List[str]] = None
    is_active: Optional[bool] = None


class ServiceResponse(BaseModel):
    id: UUID
    provider_id: UUID
    name: str
    description: Optional[str]
    category: str
    subcategory: Optional[str]
    base_price: Decimal
    pricing_type: str
    duration_minutes: Optional[int]
    service_options: List[Dict[str, Any]]
    images: List[str]
    videos: List[str]
    bookings_count: int
    average_rating: Decimal
    total_reviews: int
    is_active: bool
    created_at: datetime

    provider: Optional[ServiceProviderResponse] = None

    model_config = ConfigDict(from_attributes=True)


class ServiceListResponse(BaseModel):
    """Simplified response for list views — provider_name injected by service layer."""
    id: UUID
    name: str
    category: str
    base_price: Decimal
    duration_minutes: Optional[int]
    images: List[str]
    average_rating: Decimal
    provider_name: str


# ============================================
# AVAILABILITY SCHEMAS
# ============================================

class AvailabilityCreateRequest(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)
    is_available: bool = True
    start_time: time
    end_time: time
    break_start: Optional[time] = None
    break_end: Optional[time] = None
    slot_duration_minutes: int = Field(default=60, ge=15, le=240)
    max_bookings_per_slot: int = Field(default=1, ge=1, le=10)

    @field_validator('end_time')
    @classmethod
    def validate_end_after_start(cls, v, info):
        start = info.data.get('start_time')
        if start and v <= start:
            raise ValueError('end_time must be after start_time')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "day_of_week": 1,
            "is_available": True,
            "start_time": "09:00",
            "end_time": "17:00",
            "break_start": "12:00",
            "break_end": "13:00",
            "slot_duration_minutes": 60,
            "max_bookings_per_slot": 1
        }
    })


class AvailabilityResponse(BaseModel):
    id: UUID
    provider_id: UUID
    day_of_week: int
    is_available: bool
    start_time: time
    end_time: time
    break_start: Optional[time]
    break_end: Optional[time]
    slot_duration_minutes: int
    max_bookings_per_slot: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AvailableSlot(BaseModel):
    slot_time: time
    available_capacity: int
    is_available: bool


class DailyAvailability(BaseModel):
    date: date
    day_name: str
    slots: List[AvailableSlot]


# ============================================
# BOOKING SCHEMAS
# ============================================

class BookingCreateRequest(BaseModel):
    service_id: UUID
    booking_date: date
    booking_time: time
    number_of_people: int = Field(default=1, gt=0)
    service_location_type: str
    service_address: Optional[str] = None
    service_location: Optional[LocationSchema] = None
    selected_options: List[Dict[str, Any]] = Field(default_factory=list)
    special_requests: Optional[str] = None
    payment_method: str = "wallet"

    @field_validator('booking_date')
    @classmethod
    def validate_future_date(cls, v):
        from datetime import date as dt_date
        if v < dt_date.today():
            raise ValueError('booking_date must be today or in the future')
        return v

    @field_validator('service_location_type')
    @classmethod
    def validate_location_type(cls, v):
        # Normalize to uppercase to match DB enum values (IN_HOME, PROVIDER_LOCATION, VIRTUAL).
        # Clients may send lowercase (in_home) or uppercase (IN_HOME) — both accepted.
        v_upper = v.upper()
        valid = {"IN_HOME", "PROVIDER_LOCATION", "VIRTUAL"}
        if v_upper not in valid:
            raise ValueError("service_location_type must be one of: in_home, provider_location, virtual")
        return v_upper

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "service_id": "123e4567-e89b-12d3-a456-426614174000",
            "booking_date": "2026-03-15",
            "booking_time": "14:00",
            "number_of_people": 1,
            "service_location_type": "provider_location",
            "selected_options": [
                {"name": "Hair Length", "value": "Long", "price": 10000}
            ],
            "special_requests": "Please use organic products",
            "payment_method": "wallet"
        }
    })


class PriceCalculateRequest(BaseModel):
    """Request body for price calculation endpoint."""
    service_id: UUID
    selected_options: List[Dict[str, Any]] = Field(default_factory=list)
    service_location_type: str

    @field_validator('service_location_type')
    @classmethod
    def validate_location_type(cls, v):
        v_upper = v.upper()
        valid = {"IN_HOME", "PROVIDER_LOCATION", "VIRTUAL"}
        if v_upper not in valid:
            raise ValueError("Must be one of: in_home, provider_location, virtual")
        return v_upper


class BookingResponse(BaseModel):
    id: UUID
    service_id: UUID
    provider_id: UUID
    customer_id: UUID
    booking_date: date
    booking_time: time
    duration_minutes: int
    number_of_people: int
    service_location_type: str
    service_address: Optional[str]
    base_price: Decimal
    add_ons_price: Decimal
    travel_fee: Decimal
    total_price: Decimal
    selected_options: List[Dict[str, Any]]
    special_requests: Optional[str]
    status: str
    payment_status: str
    created_at: datetime

    service: Optional[ServiceResponse] = None

    model_config = ConfigDict(from_attributes=True)


class BookingListResponse(BaseModel):
    id: UUID
    service_name: str
    provider_name: str
    booking_date: date
    booking_time: time
    total_price: Decimal
    status: str
    payment_status: str
    created_at: datetime


# ============================================
# SEARCH FILTERS
# ============================================

class ServiceSearchFilters(BaseModel):
    """
    Filters for service discovery.

    Per Blueprint Section 3.1: location is strictly radius-based using GPS
    coordinates. No LGA or city-name filtering anywhere.

    Default radius: 5 km (Blueprint default).
    Adjustable by user: 1 km to 50 km.
    """
    query: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)
    location: Optional[LocationSchema] = None          # GPS coordinates (required for radius filter)
    radius_km: Optional[float] = Field(5.0, gt=0)     # default 5 km — Blueprint Section 3.1
    service_location_type: Optional[str] = None
    sort_by: Optional[str] = "created_at"