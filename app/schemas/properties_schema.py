"""
app/schemas/properties_schema.py

BUG FIX — ResponseValidationError on POST /properties/search:

Root cause: Property ORM model has a Geography column (location) stored as
a GeoAlchemy2 WKBElement. When FastAPI's jsonable_encoder walks the ORM
object through PropertyListResponse (which has from_attributes=True),
it hits WKBElement and raises ResponseValidationError because WKBElement
is not JSON-serialisable.

Fix: add model_validator(mode="before") to PropertyListResponse and
PropertyResponse that calls _strip_geography() — the same helper used in
health_schema.py. This converts WKBElement → {"latitude": ..., "longitude": ...}
or drops it, before Pydantic reads any field from the ORM object.
"""

from pydantic import BaseModel, Field, field_validator, ConfigDict, model_validator
from typing import Optional, List, Dict, Any, Literal
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
# PROPERTY AGENT SCHEMAS
# ============================================

class PropertyAgentCreateRequest(BaseModel):
    agent_license_number: Optional[str] = None
    years_of_experience: int = Field(default=0, ge=0)
    specializations: List[str] = Field(default_factory=list)
    service_areas: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    profile_video_url: Optional[str] = None
    certifications: List[str] = Field(default_factory=list)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "agent_license_number": "REA/2024/12345",
            "years_of_experience": 5,
            "specializations": ["residential", "luxury"],
            "service_areas": ["Lagos", "Abuja"],
            "languages": ["english", "yoruba"]
        }
    })


class PropertyAgentUpdateRequest(BaseModel):
    agent_license_number: Optional[str] = None
    years_of_experience: Optional[int] = Field(None, ge=0)
    specializations: Optional[List[str]] = None
    service_areas: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    profile_video_url: Optional[str] = None
    certifications: Optional[List[str]] = None


class PropertyAgentResponse(BaseModel):
    id: UUID
    business_id: UUID
    agent_license_number: Optional[str] = None
    years_of_experience: int
    specializations: List[str]
    service_areas: List[str]
    languages: List[str]
    total_properties: int
    properties_sold: int
    properties_rented: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# PROPERTY SCHEMAS
# ============================================

class PropertyCreateRequest(BaseModel):
    title: str = Field(..., min_length=10, max_length=255)
    description: str = Field(..., min_length=50)
    property_type: str
    property_subtype: str
    listing_type: str

    price: Decimal = Field(..., gt=0)
    monthly_rent: Optional[Decimal] = Field(None, gt=0)
    service_charge: Optional[Decimal] = Field(None, ge=0)
    payment_frequency: Optional[str] = None
    security_deposit: Optional[Decimal] = Field(None, ge=0)
    lease_duration_months: Optional[int] = Field(None, gt=0)

    address: str = Field(..., min_length=10)
    city: str = Field(..., min_length=2)
    state: str = Field(..., min_length=2)
    local_government: Optional[str] = None
    postal_code: Optional[str] = None
    location: LocationSchema

    bedrooms: Optional[int] = Field(None, ge=0)
    bathrooms: Optional[int] = Field(None, ge=0)
    toilets: Optional[int] = Field(None, ge=0)
    living_rooms: Optional[int] = Field(None, ge=0)

    plot_size_sqm: Optional[Decimal] = Field(None, gt=0)
    building_size_sqm: Optional[Decimal] = Field(None, gt=0)

    year_built: Optional[int] = Field(None, ge=1900, le=2030)
    floors: Optional[int] = Field(None, ge=1)
    floor_number: Optional[int] = Field(None, ge=0)
    parking_spaces: int = Field(default=0, ge=0)

    condition: Optional[str] = None
    furnishing_status: Optional[str] = None

    features: List[str] = Field(default_factory=list)

    title_document_type: Optional[str] = None
    has_survey_plan: bool = False
    has_building_plan: bool = False

    images: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)
    virtual_tour_url: Optional[str] = None
    floor_plan_images: List[str] = Field(default_factory=list)

    nearby_landmarks: List[Dict[str, Any]] = Field(default_factory=list)

    available_from: Optional[date] = None
    is_negotiable: bool = True

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "title": "Luxury 4 Bedroom Duplex in Lekki Phase 1",
            "description": "Beautifully finished 4 bedroom duplex with modern amenities...",
            "property_type": "residential",
            "property_subtype": "duplex",
            "listing_type": "for_sale",
            "price": 85000000.00,
            "address": "15 Admiralty Way, Lekki Phase 1",
            "city": "Lagos",
            "state": "Lagos",
            "location": {"latitude": 6.4474, "longitude": 3.4700},
            "bedrooms": 4,
            "bathrooms": 5,
            "parking_spaces": 2,
            "plot_size_sqm": 500,
            "building_size_sqm": 350,
            "features": ["swimming_pool", "security", "generator", "borehole"],
            "images": ["https://example.com/image1.jpg"]
        }
    })


class PropertyUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=10, max_length=255)
    description: Optional[str] = Field(None, min_length=50)
    price: Optional[Decimal] = Field(None, gt=0)
    monthly_rent: Optional[Decimal] = Field(None, gt=0)
    service_charge: Optional[Decimal] = Field(None, ge=0)
    condition: Optional[str] = None
    furnishing_status: Optional[str] = None
    features: Optional[List[str]] = None
    images: Optional[List[str]] = None
    videos: Optional[List[str]] = None
    virtual_tour_url: Optional[str] = None
    available_from: Optional[date] = None
    is_negotiable: Optional[bool] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None


class PropertyResponse(BaseModel):
    id: UUID
    agent_id: UUID
    title: str
    description: str
    property_type: str
    property_subtype: str
    listing_type: str

    price: Decimal
    monthly_rent: Optional[Decimal] = None
    service_charge: Optional[Decimal] = None

    address: str
    city: str
    state: str
    local_government: Optional[str] = None

    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    toilets: Optional[int] = None
    parking_spaces: int

    plot_size_sqm: Optional[Decimal] = None
    building_size_sqm: Optional[Decimal] = None

    year_built: Optional[int] = None
    condition: Optional[str] = None
    furnishing_status: Optional[str] = None

    features: List[str]
    images: List[str]
    videos: List[str]
    virtual_tour_url: Optional[str] = None
    floor_plan_images: List[str]

    nearby_landmarks: List[Dict[str, Any]]
    available_from: Optional[date] = None
    is_negotiable: bool

    status: str
    is_featured: bool
    is_verified: bool
    is_active: bool

    views_count: int
    saves_count: int

    created_at: datetime

    # FIX: strip GeoAlchemy2 Geography column (location) before Pydantic
    # serialization. WKBElement on the ORM object causes ResponseValidationError.
    @model_validator(mode="before")
    @classmethod
    def strip_geography(cls, data):
        return _strip_geography(data)

    model_config = ConfigDict(from_attributes=True)


class PropertyListResponse(BaseModel):
    """Lightweight response for list views"""
    id: UUID
    title: str
    property_type: str
    property_subtype: str
    listing_type: str
    price: Decimal
    monthly_rent: Optional[Decimal] = None
    city: str
    state: str
    local_government: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    building_size_sqm: Optional[Decimal] = None
    images: List[str]
    status: str
    is_featured: bool
    is_verified: bool

    # FIX: strip GeoAlchemy2 Geography column (location) before Pydantic
    # serialization. This is the schema used by POST /properties/search —
    # the endpoint that was crashing with ResponseValidationError.
    @model_validator(mode="before")
    @classmethod
    def strip_geography(cls, data):
        return _strip_geography(data)

    model_config = ConfigDict(from_attributes=True)


# ============================================
# VIEWING SCHEMAS
# ============================================

class ViewingCreateRequest(BaseModel):
    property_id: UUID
    viewing_date: date
    viewing_time: time
    viewing_type: Literal["in_person", "virtual"]
    customer_name: str = Field(..., min_length=2, max_length=200)
    customer_phone: str = Field(..., min_length=10, max_length=20)
    customer_email: Optional[str] = None
    number_of_people: int = Field(default=1, ge=1, le=10)
    special_requests: Optional[str] = None

    @field_validator("viewing_date")
    @classmethod
    def validate_future_date(cls, v: date) -> date:
        from datetime import date as dt_date
        if v <= dt_date.today():
            raise ValueError("Viewing date must be at least tomorrow")
        return v


class ViewingResponse(BaseModel):
    id: UUID
    property_id: UUID
    customer_id: UUID
    viewing_date: date
    viewing_time: time
    viewing_type: str
    customer_name: str
    customer_phone: str
    number_of_people: int
    status: str
    confirmation_code: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# OFFER SCHEMAS
# ============================================

class OfferCreateRequest(BaseModel):
    property_id: UUID
    offer_amount: Decimal = Field(..., gt=0)
    proposed_payment_plan: Optional[str] = None
    proposed_lease_duration: Optional[int] = Field(None, gt=0)
    message: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "property_id": "123e4567-e89b-12d3-a456-426614174000",
            "offer_amount": 80000000.00,
            "message": "I'm interested and would like to make an offer"
        }
    })


class CounterOfferRequest(BaseModel):
    counter_amount: Decimal = Field(..., gt=0)
    counter_message: Optional[str] = None


class RejectOfferRequest(BaseModel):
    reason: Optional[str] = None


class OfferResponse(BaseModel):
    id: UUID
    property_id: UUID
    customer_id: UUID
    offer_amount: Decimal
    original_price: Decimal
    message: Optional[str] = None
    status: str
    counter_offer_amount: Optional[Decimal] = None
    counter_message: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# INQUIRY SCHEMAS
# ============================================

class InquiryCreateRequest(BaseModel):
    subject: str = Field(..., min_length=5, max_length=255)
    message: str = Field(..., min_length=10)
    customer_name: str = Field(..., min_length=2, max_length=200)
    customer_phone: str = Field(..., min_length=10, max_length=20)
    customer_email: str = Field(..., min_length=5)


class InquiryRespondRequest(BaseModel):
    response_message: str = Field(..., min_length=5)


# ============================================
# SEARCH FILTERS
# ============================================

_VALID_SORT = {"created_at", "price_asc", "price_desc", "newest", "popular"}


class PropertySearchFilters(BaseModel):
    query: Optional[str] = None
    property_type: Optional[str] = None
    property_subtype: Optional[str] = None
    listing_type: Optional[str] = None

    city: Optional[str] = None
    state: Optional[str] = None
    # local_government DELETED from search filters — Blueprint §2/§4 HARD RULE:
    # "No LGA filtering anywhere in the codebase."
    # local_government may appear in response schemas for display only.
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0, le=200)

    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)

    min_bedrooms: Optional[int] = Field(None, ge=0)
    max_bedrooms: Optional[int] = Field(None, ge=0)
    min_bathrooms: Optional[int] = Field(None, ge=0)

    min_plot_size: Optional[Decimal] = Field(None, gt=0)
    max_plot_size: Optional[Decimal] = Field(None, gt=0)

    furnishing_status: Optional[str] = None
    features: List[str] = Field(default_factory=list)
    is_featured: Optional[bool] = None
    is_verified: Optional[bool] = None

    sort_by: str = Field(default="created_at")

    @field_validator("sort_by")
    @classmethod
    def validate_sort_by(cls, v: str) -> str:
        if v not in _VALID_SORT:
            raise ValueError(f"sort_by must be one of: {sorted(_VALID_SORT)}")
        return v