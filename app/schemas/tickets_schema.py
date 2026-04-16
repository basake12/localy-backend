"""
app/schemas/tickets_schema.py

FIXES vs previous version:
  1.  [HARD RULE §2/§4] lga_id DELETED from TicketEventResponse.
      Blueprint §2: "No LGA column exists in any database table."

  2.  [HARD RULE §2/§4] lga_id DELETED from TicketEventSearchFilters.
      Blueprint §2: no LGA filtering anywhere in the codebase.

  3.  payment_method validator updated — "paystack" and "monnify" removed.
      Blueprint §5.1: wallet is the payment method at checkout.
      Paystack and Monnify are for FUNDING the wallet, not for direct checkout.
      Allowing them as checkout methods creates a path that the service never
      handles, resulting in tickets delivered without payment.
"""
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)
from typing import Any, Dict, List, Optional
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from app.schemas.common_schema import LocationSchema


# ─── Geography strip helper ───────────────────────────────────────────────────

def _strip_geography(data: Any) -> dict:
    """
    Convert a SQLAlchemy ORM object to a plain dict, replacing GeoAlchemy2
    WKBElement / WKTElement values with a lat/lng dict or None.
    """
    if isinstance(data, dict):
        return data
    try:
        from geoalchemy2.elements import WKBElement, WKTElement
        from geoalchemy2.shape import to_shape

        row: dict = {}
        for key in data.__mapper__.column_attrs.keys():  # type: ignore[union-attr]
            val = getattr(data, key, None)
            if isinstance(val, (WKBElement, WKTElement)):
                try:
                    point  = to_shape(val)
                    row[key] = {"latitude": point.y, "longitude": point.x}
                except Exception:
                    row[key] = None
            else:
                row[key] = val

        for key, val in data.__dict__.items():
            if key.startswith("_") or key in row:
                continue
            if isinstance(val, (WKBElement, WKTElement)):
                try:
                    point  = to_shape(val)
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


# ─── Event schemas ────────────────────────────────────────────────────────────

class TicketEventCreateRequest(BaseModel):
    event_type: str
    name: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = None

    event_date:  Optional[date] = None
    start_time:  Optional[time] = None
    end_time:    Optional[time] = None

    transport_type:  Optional[str]  = None
    departure_date:  Optional[date] = None
    departure_time:  Optional[time] = None
    arrival_time:    Optional[time] = None

    venue_name:    Optional[str]            = None
    venue_address: Optional[str]            = None
    venue_location: Optional[LocationSchema] = None

    origin_city:        Optional[str]            = None
    origin_terminal:    Optional[str]            = None
    origin_location:    Optional[LocationSchema] = None

    destination_city:       Optional[str]            = None
    destination_terminal:   Optional[str]            = None
    destination_location:   Optional[LocationSchema] = None

    total_capacity: int = Field(..., gt=0)

    organizer_name:    Optional[str] = None
    organizer_contact: Optional[str] = None

    banner_image: Optional[str]   = None
    images:       List[str]       = Field(default_factory=list)

    terms_and_conditions: Optional[str] = None
    cancellation_policy:  Optional[str] = None
    age_restriction:      Optional[int] = Field(None, ge=0, le=100)
    features:             List[str]     = Field(default_factory=list)

    sales_start_date: Optional[datetime] = None
    sales_end_date:   Optional[datetime] = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        valid = ["event", "transport"]
        if v not in valid:
            raise ValueError(f"event_type must be one of: {valid}")
        return v

    @field_validator("sales_end_date")
    @classmethod
    def validate_sales_period(cls, v, info):
        start = info.data.get("sales_start_date")
        if v and start and v <= start:
            raise ValueError("sales_end_date must be after sales_start_date")
        return v


class TicketEventUpdateRequest(BaseModel):
    name:                 Optional[str]           = Field(None, min_length=3, max_length=255)
    description:          Optional[str]           = None
    banner_image:         Optional[str]           = None
    images:               Optional[List[str]]      = None
    features:             Optional[List[str]]      = None
    terms_and_conditions: Optional[str]           = None
    cancellation_policy:  Optional[str]           = None
    sales_start_date:     Optional[datetime]      = None
    sales_end_date:       Optional[datetime]      = None
    is_active:            Optional[bool]          = None


class TicketEventResponse(BaseModel):
    id:          UUID
    business_id: UUID
    # lga_id DELETED — Blueprint §2 HARD RULE: no LGA column anywhere
    event_type:  str
    name:        str
    description: Optional[str] = None
    category:    Optional[str] = None

    event_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time:   Optional[time] = None

    transport_type: Optional[str]  = None
    departure_date: Optional[date] = None
    departure_time: Optional[time] = None
    arrival_time:   Optional[time] = None

    venue_name:    Optional[str] = None
    venue_address: Optional[str] = None

    origin_city:      Optional[str] = None
    origin_terminal:  Optional[str] = None
    destination_city: Optional[str] = None
    destination_terminal: Optional[str] = None

    total_capacity:     int
    available_capacity: int

    organizer_name:    Optional[str] = None
    organizer_contact: Optional[str] = None
    banner_image:      Optional[str] = None
    images:            List[str]     = Field(default_factory=list)

    features:             List[str]     = Field(default_factory=list)
    age_restriction:      Optional[int] = None
    terms_and_conditions: Optional[str] = None
    cancellation_policy:  Optional[str] = None

    status:      str
    is_featured: bool
    is_active:   bool

    sales_start_date: Optional[datetime] = None
    sales_end_date:   Optional[datetime] = None

    total_tickets_sold: int
    total_revenue:      Decimal
    average_rating:     Decimal
    total_reviews:      int

    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def strip_geography(cls, data):
        return _strip_geography(data)

    model_config = ConfigDict(from_attributes=True)


class TicketEventListResponse(BaseModel):
    """Simplified event list item — used by POST /tickets/search."""
    id:         UUID
    event_type: str
    name:       str
    category:   Optional[str]  = None
    event_date: Optional[date] = None
    start_time: Optional[time] = None

    departure_date: Optional[date] = None
    departure_time: Optional[time] = None

    venue_name:       Optional[str] = None
    origin_city:      Optional[str] = None
    destination_city: Optional[str] = None

    available_capacity: int
    status:             str
    banner_image:       Optional[str] = None
    is_featured:        bool
    average_rating:     Decimal

    @model_validator(mode="before")
    @classmethod
    def strip_geography(cls, data):
        return _strip_geography(data)

    model_config = ConfigDict(from_attributes=True)


# ─── Tier schemas ─────────────────────────────────────────────────────────────

class TicketTierCreateRequest(BaseModel):
    name:            str     = Field(..., min_length=2, max_length=100)
    description:     Optional[str] = None
    price:           Decimal = Field(..., ge=0)
    total_quantity:  int     = Field(..., gt=0)
    has_seat_numbers: bool   = False
    seat_section:    Optional[str] = None
    benefits:        List[str]     = Field(default_factory=list)
    min_purchase:    int     = Field(default=1, gt=0)
    max_purchase:    int     = Field(default=10, gt=0)
    display_order:   int     = Field(default=0, ge=0)

    @field_validator("max_purchase")
    @classmethod
    def validate_max_gte_min(cls, v, info):
        min_val = info.data.get("min_purchase", 1)
        if v < min_val:
            raise ValueError("max_purchase must be >= min_purchase")
        return v


class TicketTierResponse(BaseModel):
    id:               UUID
    event_id:         UUID
    name:             str
    description:      Optional[str] = None
    price:            Decimal
    total_quantity:   int
    available_quantity: int
    has_seat_numbers: bool
    seat_section:     Optional[str] = None
    benefits:         List[str]
    min_purchase:     int
    max_purchase:     int
    is_active:        bool
    display_order:    int
    created_at:       datetime

    model_config = ConfigDict(from_attributes=True)


# ─── Booking schemas ──────────────────────────────────────────────────────────

class AttendeeInfo(BaseModel):
    name:  str   = Field(..., min_length=2, max_length=200)
    email: EmailStr
    phone: str   = Field(..., min_length=7, max_length=20)


class TicketBookingCreateRequest(BaseModel):
    event_id: UUID
    tier_id:  UUID
    quantity: int = Field(..., gt=0, le=20)

    attendee_name:  str      = Field(..., min_length=2, max_length=200)
    attendee_email: EmailStr
    attendee_phone: str      = Field(..., min_length=7, max_length=20)

    additional_attendees: List[AttendeeInfo] = Field(default_factory=list)
    preferred_seats:      List[str]          = Field(default_factory=list)
    special_requests:     Optional[str]      = Field(None, max_length=500)

    # Blueprint §5.1: "wallet" is the ONLY valid checkout method.
    # Paystack and Monnify are for FUNDING the wallet, not direct checkout.
    payment_method: str = Field(default="wallet")

    @field_validator("payment_method")
    @classmethod
    def validate_payment_method(cls, v: str) -> str:
        if v != "wallet":
            raise ValueError(
                "payment_method must be 'wallet'. "
                "Fund your wallet via Paystack or bank transfer first, "
                "then check out using your wallet balance."
            )
        return v


class TicketBookingResponse(BaseModel):
    id:                  UUID
    event_id:            UUID
    tier_id:             UUID
    customer_id:         UUID
    quantity:            int
    unit_price:          Decimal
    service_charge:      Decimal   # platform fee = ₦50 × quantity
    total_amount:        Decimal
    attendee_name:       str
    attendee_email:      str
    attendee_phone:      str
    assigned_seats:      List[str]
    booking_reference:   str
    qr_code_url:         Optional[str] = None
    status:              str
    payment_status:      str
    checked_in_at:       Optional[datetime] = None
    cancelled_at:        Optional[datetime] = None
    cancellation_reason: Optional[str]      = None
    created_at:          datetime

    event: Optional[TicketEventResponse]  = None
    tier:  Optional[TicketTierResponse]   = None

    model_config = ConfigDict(from_attributes=True)


class TicketBookingListResponse(BaseModel):
    id:                UUID
    booking_reference: str
    quantity:          int
    total_amount:      Decimal
    status:            str
    payment_status:    str
    created_at:        datetime

    event_name:     str
    tier_name:      str
    event_date:     Optional[date] = None
    departure_date: Optional[date] = None
    venue_name:     Optional[str]  = None
    qr_code_url:    Optional[str]  = None

    model_config = ConfigDict(from_attributes=True)


class CheckInRequest(BaseModel):
    booking_reference: str = Field(..., min_length=5)


# ─── Search filters ───────────────────────────────────────────────────────────

class TicketEventSearchFilters(BaseModel):
    """
    Filters for ticket/event discovery.
    Blueprint §4: radius-based only — no LGA filtering.
    lga_id has been removed.
    """
    query:      Optional[str]   = Field(None, max_length=200)
    event_type: Optional[str]   = None
    category:   Optional[str]   = None
    location:   Optional[LocationSchema] = None
    radius_km:  Optional[float] = Field(None, gt=0, le=500)
    # lga_id DELETED — Blueprint §2/§4 HARD RULE: no LGA anywhere

    event_date_from: Optional[date] = None
    event_date_to:   Optional[date] = None

    origin_city:      Optional[str]  = None
    destination_city: Optional[str]  = None
    departure_date:   Optional[date] = None
    transport_type:   Optional[str]  = None

    max_price:      Optional[Decimal] = Field(None, ge=0)
    available_only: bool              = True
    is_featured:    Optional[bool]    = None