from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List
from datetime import date, time, datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.common import LocationSchema


# ============================================
# HOTEL SCHEMAS
# ============================================

class HotelCreateRequest(BaseModel):
    """Create hotel request (admin/business)"""
    star_rating: Optional[int] = Field(None, ge=1, le=5)
    total_rooms: int = Field(..., gt=0)
    check_in_time: Optional[time] = time(14, 0)
    check_out_time: Optional[time] = time(12, 0)
    facilities: List[str] = Field(default_factory=list)
    policies: Optional[str] = None
    cancellation_policy: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "star_rating": 4,
            "total_rooms": 50,
            "check_in_time": "14:00",
            "check_out_time": "12:00",
            "facilities": ["pool", "gym", "spa", "restaurant", "parking", "wifi"],
            "policies": "No smoking in rooms",
            "cancellation_policy": "Free cancellation up to 24 hours before check-in"
        }
    })


class HotelResponse(BaseModel):
    """Hotel response"""
    id: UUID
    business_id: UUID
    star_rating: Optional[int]
    total_rooms: int
    check_in_time: time
    check_out_time: time
    facilities: List[str]
    policies: Optional[str]
    cancellation_policy: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# ROOM TYPE SCHEMAS
# ============================================

class RoomTypeCreateRequest(BaseModel):
    """Create room type request"""
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    bed_configuration: Optional[str] = None
    max_occupancy: int = Field(..., gt=0)
    size_sqm: Optional[Decimal] = Field(None, gt=0)
    floor_range: Optional[str] = None
    view_type: Optional[str] = None
    amenities: List[str] = Field(default_factory=list)
    base_price_per_night: Decimal = Field(..., gt=0)
    total_rooms: int = Field(..., gt=0)
    images: List[str] = Field(default_factory=list)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "Deluxe Suite",
            "description": "Spacious suite with city view",
            "bed_configuration": "1 King Bed",
            "max_occupancy": 2,
            "size_sqm": 45.5,
            "floor_range": "10-15",
            "view_type": "city",
            "amenities": ["tv", "minibar", "safe", "balcony", "bathtub"],
            "base_price_per_night": 25000.00,
            "total_rooms": 10,
            "images": ["https://example.com/room1.jpg"]
        }
    })


class RoomTypeResponse(BaseModel):
    """Room type response"""
    id: UUID
    hotel_id: UUID
    name: str
    description: Optional[str]
    bed_configuration: Optional[str]
    max_occupancy: int
    size_sqm: Optional[Decimal]
    floor_range: Optional[str]
    view_type: Optional[str]
    amenities: List[str]
    base_price_per_night: Decimal
    images: List[str]
    total_rooms: int
    available_rooms: Optional[int] = None  # Calculated at runtime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RoomTypeUpdateRequest(BaseModel):
    """Update room type request"""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    base_price_per_night: Optional[Decimal] = Field(None, gt=0)
    amenities: Optional[List[str]] = None
    images: Optional[List[str]] = None


# ============================================
# BOOKING SCHEMAS
# ============================================

class BookingSearchRequest(BaseModel):
    """Search available rooms"""
    check_in_date: date
    check_out_date: date
    number_of_guests: int = Field(..., gt=0)
    number_of_rooms: int = Field(default=1, gt=0)

    @field_validator('check_out_date')
    @classmethod
    def validate_dates(cls, v, info):
        check_in = info.data.get('check_in_date')
        if check_in and v <= check_in:
            raise ValueError('Check-out date must be after check-in date')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "check_in_date": "2026-03-15",
            "check_out_date": "2026-03-18",
            "number_of_guests": 2,
            "number_of_rooms": 1
        }
    })


class BookingCreateRequest(BaseModel):
    """Create booking request"""
    room_type_id: UUID
    check_in_date: date
    check_out_date: date
    number_of_rooms: int = Field(default=1, gt=0)
    number_of_guests: int = Field(..., gt=0)
    add_ons: List[dict] = Field(default_factory=list)
    special_requests: Optional[str] = None

    @field_validator('check_out_date')
    @classmethod
    def validate_dates(cls, v, info):
        check_in = info.data.get('check_in_date')
        if check_in and v <= check_in:
            raise ValueError('Check-out date must be after check-in date')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "room_type_id": "123e4567-e89b-12d3-a456-426614174000",
            "check_in_date": "2026-03-15",
            "check_out_date": "2026-03-18",
            "number_of_rooms": 1,
            "number_of_guests": 2,
            "add_ons": [
                {"type": "breakfast", "quantity": 2},
                {"type": "airport_transfer", "quantity": 1}
            ],
            "special_requests": "High floor with city view preferred"
        }
    })


class BookingResponse(BaseModel):
    """Booking response"""
    id: UUID
    hotel_id: UUID
    room_type_id: UUID
    customer_id: UUID
    check_in_date: date
    check_out_date: date
    number_of_rooms: int
    number_of_guests: int
    base_price: Decimal
    add_ons_price: Decimal
    total_price: Decimal
    add_ons: List[dict]
    special_requests: Optional[str]
    status: str
    payment_status: str
    check_in_form_completed: bool
    id_uploaded: bool
    created_at: datetime

    # Nested data
    room_type: Optional[RoomTypeResponse] = None

    model_config = ConfigDict(from_attributes=True)


class BookingListResponse(BaseModel):
    """Simplified booking list response"""
    id: UUID
    hotel_name: str
    room_type_name: str
    check_in_date: date
    check_out_date: date
    number_of_rooms: int
    total_price: Decimal
    status: str
    payment_status: str
    created_at: datetime


# ============================================
# SERVICE REQUEST SCHEMAS
# ============================================

class ServiceRequestCreate(BaseModel):
    """Create service request"""
    service_type: str = Field(..., min_length=2)
    description: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "service_type": "room_service",
            "description": "2 club sandwiches and 2 cokes"
        }
    })


class ServiceRequestResponse(BaseModel):
    """Service request response"""
    id: UUID
    booking_id: UUID
    service_type: str
    description: Optional[str]
    status: str
    requested_at: datetime
    completed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


# ============================================
# SEARCH FILTERS
# ============================================

class HotelSearchFilters(BaseModel):
    """Hotel search filters"""
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0, le=50)
    star_rating: Optional[int] = Field(None, ge=1, le=5)
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)
    facilities: Optional[List[str]] = None
    check_in_date: Optional[date] = None
    check_out_date: Optional[date] = None
    guests: Optional[int] = Field(None, gt=0)