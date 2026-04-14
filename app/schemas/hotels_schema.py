"""
app/schemas/hotels_schema.py

Hotel module schemas per Blueprint v2.0 Section 11.1

CHANGES:
- Removed lga_id from search filters (replaced with location + radius)
- Added BookingPaymentResponse for booking + payment data
- Proper enum types for status fields
"""
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List
from datetime import date, time, datetime
from decimal import Decimal
from uuid import UUID

from app.models.hotels_model import BookingStatusEnum, PaymentStatusEnum
from app.schemas.common_schema import LocationSchema


# ============================================
# SHARED SUB-SCHEMAS
# ============================================

class AddOnItem(BaseModel):
    """A single booking add-on"""
    type: str = Field(..., description="e.g. breakfast, airport_transfer, spa")
    quantity: int = Field(default=1, gt=0)
    price: Optional[Decimal] = Field(None, ge=0)

    model_config = ConfigDict(json_schema_extra={
        "example": {"type": "breakfast", "quantity": 2, "price": 5000}
    })


# ============================================
# HOTEL SCHEMAS
# ============================================

class HotelCreateRequest(BaseModel):
    star_rating: Optional[int] = Field(None, ge=1, le=5)
    total_rooms: int = Field(..., gt=0)
    check_in_time: Optional[time] = time(14, 0)
    check_out_time: Optional[time] = time(11, 0)
    facilities: List[str] = Field(default_factory=list)
    policies: Optional[str] = None
    cancellation_policy: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "star_rating": 4,
            "total_rooms": 50,
            "check_in_time": "14:00",
            "check_out_time": "11:00",
            "facilities": ["pool", "gym", "spa", "restaurant", "parking", "wifi"],
            "policies": "No smoking in rooms",
            "cancellation_policy": "Free cancellation up to 24 hours before check-in"
        }
    })


class HotelResponse(BaseModel):
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
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    base_price_per_night: Optional[Decimal] = Field(None, gt=0)
    amenities: Optional[List[str]] = None
    images: Optional[List[str]] = None


# ============================================
# BOOKING SCHEMAS
# ============================================

class BookingSearchRequest(BaseModel):
    check_in_date: date
    check_out_date: date
    number_of_guests: int = Field(..., gt=0)
    number_of_rooms: int = Field(default=1, gt=0)

    @field_validator('check_out_date')
    @classmethod
    def validate_dates(cls, v, info):
        check_in = info.data.get('check_in_date')
        if check_in and v <= check_in:
            raise ValueError('check_out_date must be after check_in_date')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "check_in_date": "2026-04-15",
            "check_out_date": "2026-04-18",
            "number_of_guests": 2,
            "number_of_rooms": 1
        }
    })


class BookingCreateRequest(BaseModel):
    room_type_id: UUID
    check_in_date: date
    check_out_date: date
    number_of_rooms: int = Field(default=1, gt=0)
    number_of_guests: int = Field(..., gt=0)
    add_ons: List[AddOnItem] = Field(default_factory=list)
    special_requests: Optional[str] = None

    @field_validator('check_out_date')
    @classmethod
    def validate_dates(cls, v, info):
        check_in = info.data.get('check_in_date')
        if check_in and v <= check_in:
            raise ValueError('check_out_date must be after check_in_date')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "room_type_id": "123e4567-e89b-12d3-a456-426614174000",
            "check_in_date": "2026-04-15",
            "check_out_date": "2026-04-18",
            "number_of_rooms": 1,
            "number_of_guests": 2,
            "add_ons": [
                {"type": "breakfast", "quantity": 2, "price": 5000},
                {"type": "airport_transfer", "quantity": 1, "price": 15000}
            ],
            "special_requests": "High floor with city view preferred"
        }
    })


class BookingResponse(BaseModel):
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
    add_ons: List[AddOnItem]
    special_requests: Optional[str]
    status: BookingStatusEnum
    payment_status: PaymentStatusEnum
    check_in_form_completed: bool
    id_uploaded: bool
    created_at: datetime

    room_type: Optional[RoomTypeResponse] = None

    model_config = ConfigDict(from_attributes=True)


class BookingListResponse(BaseModel):
    """Simplified booking response for list views."""
    id: UUID
    hotel_name: str
    room_type_name: str
    check_in_date: date
    check_out_date: date
    number_of_rooms: int
    total_price: Decimal
    status: BookingStatusEnum
    payment_status: PaymentStatusEnum
    created_at: datetime


class BookingPaymentResponse(BaseModel):
    """
    Response for booking creation with payment details.
    
    Includes booking record + transaction info + platform fee breakdown.
    Per Blueprint Section 4.4: ₦100 platform fee for hotel bookings.
    """
    booking: BookingResponse
    customer_transaction: dict  # WalletTransaction details
    platform_fee: Decimal
    total_paid: Decimal

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "booking": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "total_price": 75000.00,
                "status": "confirmed",
                "payment_status": "paid",
            },
            "customer_transaction": {
                "id": "987fcdeb-51a2-43d7-8c6f-123456789abc",
                "amount": 75000.00,
                "transaction_type": "debit",
            },
            "platform_fee": 100.00,
            "total_paid": 75000.00,
        }
    })


# ============================================
# SERVICE REQUEST SCHEMAS
# ============================================

class ServiceRequestCreate(BaseModel):
    service_type: str = Field(..., min_length=2)
    description: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "service_type": "room_service",
            "description": "2 club sandwiches and 2 cokes"
        }
    })


class ServiceRequestResponse(BaseModel):
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
    """
    Hotel search filters - RADIUS-BASED ONLY.
    
    Per Blueprint Section 3: "Location model — Radius-based (default 5 km)
    — no LGA dependency"
    
    LGA filtering has been removed completely.
    """
    # Primary: coordinate-based radius search
    location: Optional[LocationSchema] = Field(
        None,
        description="User's location (lat/lng) - required for radius filtering"
    )
    radius_km: Optional[float] = Field(
        None,
        gt=0,
        le=50,
        description="Search radius in kilometers (default 5km, max 50km)"
    )

    # Content filters
    star_rating: Optional[int] = Field(None, ge=1, le=5)
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)
    facilities: Optional[List[str]] = Field(
        None,
        description="Required facilities (e.g. ['pool', 'gym', 'wifi'])"
    )

    # Availability check
    check_in_date: Optional[date] = None
    check_out_date: Optional[date] = None
    guests: Optional[int] = Field(None, gt=0)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "location": {"latitude": 6.5244, "longitude": 3.3792},
            "radius_km": 10.0,
            "star_rating": 4,
            "min_price": 20000,
            "max_price": 100000,
            "facilities": ["pool", "gym", "wifi"],
            "check_in_date": "2026-04-15",
            "check_out_date": "2026-04-18",
            "guests": 2
        }
    })