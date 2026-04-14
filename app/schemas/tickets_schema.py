"""
app/schemas/tickets_schema.py

BUG FIX — ResponseValidationError on POST /tickets/search:

Root cause: TicketEvent ORM model has Geography columns (venue_location,
origin_location, destination_location) stored as GeoAlchemy2 WKBElement.
When FastAPI's jsonable_encoder walks ORM objects through TicketEventListResponse
(which has from_attributes=True), it hits WKBElement and raises
ResponseValidationError because WKBElement is not JSON-serialisable.

Fix: add model_validator(mode="before") to TicketEventListResponse and
TicketEventResponse that calls _strip_geography() before Pydantic reads
any field from the ORM object.

ADDITIONAL WARNING — tickets.py router uses async def + AsyncSession:
The tickets router (tickets.py) uses `async def` endpoints and depends on
`get_async_db` (AsyncSession). All other routers in this project use sync
`def` + `Session`. If `ticket_event_crud`, `ticket_tier_crud`, and
`ticket_booking_crud` are implemented as sync CRUD classes (like the other
CRUDs), the `await crud.method(db, ...)` calls in the router will fail with
a coroutine error. Verify that the CRUD layer is async-compatible, or convert
the tickets router to sync (remove async/await, change Depends to get_db).
"""

from pydantic import BaseModel, Field, field_validator, EmailStr, ConfigDict, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID

from app.schemas.common_schema import LocationSchema


# ─────────────────────────────────────────────────────────────────────────────
# SHARED GEOGRAPHY STRIP HELPER (mirrors health_schema._strip_geography)
# ─────────────────────────────────────────────────────────────────────────────

def _strip_geography(data: Any) -> dict:
    """
    Convert a SQLAlchemy ORM object to a plain dict, replacing any
    GeoAlchemy2 WKBElement / WKTElement values with a lat/lng dict or None.
    Safe to call on plain dicts (no-op).
    """
    if isinstance(data, dict):
        return data

    try:
        from geoalchemy2.elements import WKBElement, WKTElement
        from geoalchemy2.shape import to_shape

        row = {}
        for key in data.__mapper__.column_attrs.keys():  # type: ignore[union-attr]
            val = getattr(data, key, None)
            if isinstance(val, (WKBElement, WKTElement)):
                try:
                    point = to_shape(val)
                    row[key] = {"latitude": point.y, "longitude": point.x}
                except Exception:
                    row[key] = None
            else:
                row[key] = val

        for key, val in data.__dict__.items():
            if key.startswith("_"):
                continue
            if key not in row:
                if isinstance(val, (WKBElement, WKTElement)):
                    try:
                        point = to_shape(val)
                        row[key] = {"latitude": point.y, "longitude": point.x}
                    except Exception:
                        row[key] = None
                else:
                    row[key] = val

        return row

    except (ImportError, AttributeError):
        if hasattr(data, "__dict__"):
            return {k: v for k, v in data.__dict__.items() if not k.startswith("_")}
        return data  # type: ignore[return-value]


# ============================================
# EVENT SCHEMAS
# ============================================

class TicketEventCreateRequest(BaseModel):
    event_type: str
    name: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = None

    event_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None

    transport_type: Optional[str] = None
    departure_date: Optional[date] = None
    departure_time: Optional[time] = None
    arrival_time: Optional[time] = None

    venue_name: Optional[str] = None
    venue_address: Optional[str] = None
    venue_location: Optional[LocationSchema] = None

    origin_city: Optional[str] = None
    origin_terminal: Optional[str] = None
    origin_location: Optional[LocationSchema] = None

    destination_city: Optional[str] = None
    destination_terminal: Optional[str] = None
    destination_location: Optional[LocationSchema] = None

    total_capacity: int = Field(..., gt=0)

    organizer_name: Optional[str] = None
    organizer_contact: Optional[str] = None

    banner_image: Optional[str] = None
    images: List[str] = Field(default_factory=list)

    terms_and_conditions: Optional[str] = None
    cancellation_policy: Optional[str] = None
    age_restriction: Optional[int] = Field(None, ge=0, le=100)

    features: List[str] = Field(default_factory=list)

    sales_start_date: Optional[datetime] = None
    sales_end_date: Optional[datetime] = None

    @field_validator('event_type')
    @classmethod
    def validate_event_type(cls, v):
        valid_types = ["event", "transport"]
        if v not in valid_types:
            raise ValueError(f'event_type must be one of: {valid_types}')
        return v

    @field_validator('sales_end_date')
    @classmethod
    def validate_sales_period(cls, v, info):
        start = info.data.get('sales_start_date')
        if v and start and v <= start:
            raise ValueError('sales_end_date must be after sales_start_date')
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


class TicketEventUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = None
    banner_image: Optional[str] = None
    images: Optional[List[str]] = None
    features: Optional[List[str]] = None
    terms_and_conditions: Optional[str] = None
    cancellation_policy: Optional[str] = None
    sales_start_date: Optional[datetime] = None
    sales_end_date: Optional[datetime] = None
    is_active: Optional[bool] = None


class TicketEventResponse(BaseModel):
    id: UUID
    business_id: UUID
    lga_id: Optional[UUID] = None
    event_type: str
    name: str
    description: Optional[str] = None
    category: Optional[str] = None

    event_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None

    transport_type: Optional[str] = None
    departure_date: Optional[date] = None
    departure_time: Optional[time] = None
    arrival_time: Optional[time] = None

    venue_name: Optional[str] = None
    venue_address: Optional[str] = None

    origin_city: Optional[str] = None
    origin_terminal: Optional[str] = None
    destination_city: Optional[str] = None
    destination_terminal: Optional[str] = None

    total_capacity: int
    available_capacity: int

    organizer_name: Optional[str] = None
    organizer_contact: Optional[str] = None
    banner_image: Optional[str] = None
    images: List[str]

    features: List[str]
    age_restriction: Optional[int] = None
    terms_and_conditions: Optional[str] = None
    cancellation_policy: Optional[str] = None

    status: str
    is_featured: bool
    is_active: bool

    sales_start_date: Optional[datetime] = None
    sales_end_date: Optional[datetime] = None

    total_tickets_sold: int
    total_revenue: Decimal
    average_rating: Decimal
    total_reviews: int

    created_at: datetime
    updated_at: datetime

    # FIX: strip GeoAlchemy2 Geography columns (venue_location, origin_location,
    # destination_location) before Pydantic serialization.
    # These columns are WKBElement objects — not JSON-serialisable.
    @model_validator(mode="before")
    @classmethod
    def strip_geography(cls, data):
        return _strip_geography(data)

    model_config = ConfigDict(from_attributes=True)


class TicketEventListResponse(BaseModel):
    """Simplified event list item — used by POST /tickets/search"""
    id: UUID
    event_type: str
    name: str
    category: Optional[str] = None
    event_date: Optional[date] = None
    start_time: Optional[time] = None
    departure_date: Optional[date] = None
    departure_time: Optional[time] = None
    venue_name: Optional[str] = None
    origin_city: Optional[str] = None
    destination_city: Optional[str] = None
    available_capacity: int
    status: str
    banner_image: Optional[str] = None
    is_featured: bool
    average_rating: Decimal

    # FIX: strip GeoAlchemy2 Geography columns before Pydantic serialization.
    # This is the schema used by POST /tickets/search — the crashing endpoint.
    # venue_location / origin_location / destination_location on the ORM object
    # are WKBElement objects that jsonable_encoder cannot handle.
    @model_validator(mode="before")
    @classmethod
    def strip_geography(cls, data):
        return _strip_geography(data)

    model_config = ConfigDict(from_attributes=True)


# ============================================
# TICKET TIER SCHEMAS
# ============================================

class TicketTierCreateRequest(BaseModel):
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

    @field_validator('max_purchase')
    @classmethod
    def validate_max_gte_min(cls, v, info):
        min_val = info.data.get('min_purchase', 1)
        if v < min_val:
            raise ValueError('max_purchase must be >= min_purchase')
        return v

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
    id: UUID
    event_id: UUID
    name: str
    description: Optional[str] = None
    price: Decimal
    total_quantity: int
    available_quantity: int
    has_seat_numbers: bool
    seat_section: Optional[str] = None
    benefits: List[str]
    min_purchase: int
    max_purchase: int
    is_active: bool
    display_order: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# BOOKING SCHEMAS
# ============================================

class AttendeeInfo(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=20)


class TicketBookingCreateRequest(BaseModel):
    event_id: UUID
    tier_id: UUID
    quantity: int = Field(..., gt=0, le=20)

    attendee_name: str = Field(..., min_length=2, max_length=200)
    attendee_email: EmailStr
    attendee_phone: str = Field(..., min_length=7, max_length=20)

    additional_attendees: List[AttendeeInfo] = Field(default_factory=list)
    preferred_seats: List[str] = Field(default_factory=list)
    special_requests: Optional[str] = Field(None, max_length=500)
    payment_method: str = Field(default="wallet")

    @field_validator('payment_method')
    @classmethod
    def validate_payment_method(cls, v):
        allowed = ["wallet", "paystack", "monnify"]
        if v not in allowed:
            raise ValueError(f'payment_method must be one of: {allowed}')
        return v

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
    qr_code_url: Optional[str] = None
    status: str
    payment_status: str
    checked_in_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None
    created_at: datetime

    event: Optional[TicketEventResponse] = None
    tier: Optional[TicketTierResponse] = None

    model_config = ConfigDict(from_attributes=True)


class TicketBookingListResponse(BaseModel):
    id: UUID
    booking_reference: str
    quantity: int
    total_amount: Decimal
    status: str
    payment_status: str
    created_at: datetime

    event_name: str
    tier_name: str
    event_date: Optional[date] = None
    departure_date: Optional[date] = None
    venue_name: Optional[str] = None
    qr_code_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CheckInRequest(BaseModel):
    booking_reference: str = Field(..., min_length=5)


# ============================================
# SEARCH FILTERS
# ============================================

class TicketEventSearchFilters(BaseModel):
    query: Optional[str] = Field(None, max_length=200)
    event_type: Optional[str] = None
    category: Optional[str] = None
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0, le=500)
    lga_id: Optional[UUID] = None

    event_date_from: Optional[date] = None
    event_date_to: Optional[date] = None

    origin_city: Optional[str] = None
    destination_city: Optional[str] = None
    departure_date: Optional[date] = None
    transport_type: Optional[str] = None

    max_price: Optional[Decimal] = Field(None, ge=0)
    available_only: bool = True
    is_featured: Optional[bool] = None