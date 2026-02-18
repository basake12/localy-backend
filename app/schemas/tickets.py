from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID

from app.schemas.common import LocationSchema


# ============================================
# EVENT SCHEMAS
# ============================================

class TicketEventCreateRequest(BaseModel):
    """Create ticket event request"""
    event_type: str  # event, transport
    name: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = None

    # Event details
    event_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None

    # Transport details
    transport_type: Optional[str] = None
    departure_date: Optional[date] = None
    departure_time: Optional[time] = None
    arrival_time: Optional[time] = None

    # Location/Route
    venue_name: Optional[str] = None
    venue_address: Optional[str] = None
    venue_location: Optional[LocationSchema] = None

    origin_city: Optional[str] = None
    origin_terminal: Optional[str] = None
    origin_location: Optional[LocationSchema] = None

    destination_city: Optional[str] = None
    destination_terminal: Optional[str] = None
    destination_location: Optional[LocationSchema] = None

    # Capacity
    total_capacity: int = Field(..., gt=0)

    # Organizer
    organizer_name: Optional[str] = None
    organizer_contact: Optional[str] = None

    # Media
    banner_image: Optional[str] = None
    images: List[str] = Field(default_factory=list)

    # Policies
    terms_and_conditions: Optional[str] = None
    cancellation_policy: Optional[str] = None
    age_restriction: Optional[int] = Field(None, ge=0)

    # Features
    features: List[str] = Field(default_factory=list)

    # Sales Period
    sales_start_date: Optional[datetime] = None
    sales_end_date: Optional[datetime] = None

    @field_validator('event_type')
    @classmethod
    def validate_event_type(cls, v):
        valid_types = ["event", "transport"]
        if v not in valid_types:
            raise ValueError(f'Invalid event type. Must be one of: {valid_types}')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "event_type": "event",
            "name": "Afrobeat Music Festival 2026",
            "description": "Annual celebration of African music",
            "category": "concert",
            "event_date": "2026-07-15",
            "start_time": "18:00",
            "end_time": "23:00",
            "venue_name": "Eko Atlantic",
            "venue_address": "Eko Atlantic City, Lagos",
            "total_capacity": 5000,
            "organizer_name": "Live Events Ltd",
            "features": ["parking", "vip_lounge", "food_stalls"]
        }
    })


class TicketEventResponse(BaseModel):
    """Ticket event response"""
    id: UUID
    business_id: UUID
    event_type: str
    name: str
    description: Optional[str]
    category: Optional[str]

    event_date: Optional[date]
    start_time: Optional[time]
    end_time: Optional[time]

    transport_type: Optional[str]
    departure_date: Optional[date]
    departure_time: Optional[time]
    arrival_time: Optional[time]

    venue_name: Optional[str]
    venue_address: Optional[str]

    origin_city: Optional[str]
    origin_terminal: Optional[str]
    destination_city: Optional[str]
    destination_terminal: Optional[str]

    total_capacity: int
    available_capacity: int

    organizer_name: Optional[str]
    banner_image: Optional[str]
    images: List[str]

    features: List[str]
    age_restriction: Optional[int]

    status: str
    is_featured: bool
    is_active: bool

    total_tickets_sold: int
    average_rating: Decimal

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketEventListResponse(BaseModel):
    """Simplified event list"""
    id: UUID
    event_type: str
    name: str
    event_date: Optional[date]
    departure_date: Optional[date]
    venue_name: Optional[str]
    origin_city: Optional[str]
    destination_city: Optional[str]
    available_capacity: int
    status: str
    banner_image: Optional[str]


# ============================================
# TICKET TIER SCHEMAS
# ============================================

class TicketTierCreateRequest(BaseModel):
    """Create ticket tier"""
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    price: Decimal = Field(..., ge=0)
    total_quantity: int = Field(..., gt=0)
    has_seat_numbers: bool = False
    seat_section: Optional[str] = None
    benefits: List[str] = Field(default_factory=list)
    min_purchase: int = Field(default=1, gt=0)
    max_purchase: int = Field(default=10, gt=0)
    display_order: int = Field(default=0, ge=0)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "VIP",
            "description": "VIP access with all benefits",
            "price": 50000.00,
            "total_quantity": 100,
            "has_seat_numbers": True,
            "seat_section": "Section A",
            "benefits": ["VIP lounge access", "Free drink", "Priority entry"],
            "min_purchase": 1,
            "max_purchase": 5
        }
    })


class TicketTierResponse(BaseModel):
    """Ticket tier response"""
    id: UUID
    event_id: UUID
    name: str
    description: Optional[str]
    price: Decimal
    total_quantity: int
    available_quantity: int
    has_seat_numbers: bool
    seat_section: Optional[str]
    benefits: List[str]
    min_purchase: int
    max_purchase: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# BOOKING SCHEMAS
# ============================================

class TicketBookingCreateRequest(BaseModel):
    """Create ticket booking"""
    event_id: UUID
    tier_id: UUID
    quantity: int = Field(..., gt=0)

    # Attendee info
    attendee_name: str = Field(..., min_length=2)
    attendee_email: str
    attendee_phone: str = Field(..., min_length=10)

    # Additional attendees (for group bookings)
    additional_attendees: List[Dict[str, str]] = Field(default_factory=list)

    # Seat preference (if applicable)
    preferred_seats: List[str] = Field(default_factory=list)

    special_requests: Optional[str] = None
    payment_method: str = "wallet"

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "event_id": "123e4567-e89b-12d3-a456-426614174000",
            "tier_id": "123e4567-e89b-12d3-a456-426614174001",
            "quantity": 2,
            "attendee_name": "John Doe",
            "attendee_email": "john@example.com",
            "attendee_phone": "+2348012345678",
            "additional_attendees": [
                {"name": "Jane Doe", "email": "jane@example.com", "phone": "+2348087654321"}
            ],
            "payment_method": "wallet"
        }
    })


class TicketBookingResponse(BaseModel):
    """Ticket booking response"""
    id: UUID
    event_id: UUID
    tier_id: UUID
    customer_id: UUID
    quantity: int
    unit_price: Decimal
    service_charge: Decimal
    total_amount: Decimal
    attendee_name: str
    attendee_email: str
    attendee_phone: str
    assigned_seats: List[str]
    booking_reference: str
    qr_code_url: Optional[str]
    status: str
    payment_status: str
    created_at: datetime

    # Nested
    event: Optional[TicketEventResponse] = None
    tier: Optional[TicketTierResponse] = None

    model_config = ConfigDict(from_attributes=True)


class TicketBookingListResponse(BaseModel):
    """Simplified booking list"""
    id: UUID
    booking_reference: str
    event_name: str
    tier_name: str
    quantity: int
    total_amount: Decimal
    status: str
    event_date: Optional[date]
    created_at: datetime


# ============================================
# SEARCH FILTERS
# ============================================

class TicketEventSearchFilters(BaseModel):
    """Event search filters"""
    query: Optional[str] = None
    event_type: Optional[str] = None  # event, transport
    category: Optional[str] = None
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0)

    # For events
    event_date_from: Optional[date] = None
    event_date_to: Optional[date] = None

    # For transport
    origin_city: Optional[str] = None
    destination_city: Optional[str] = None
    departure_date: Optional[date] = None
    transport_type: Optional[str] = None

    # Filters
    max_price: Optional[Decimal] = Field(None, ge=0)
    available_only: bool = True
    is_featured: Optional[bool] = None