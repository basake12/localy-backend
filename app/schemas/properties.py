from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID

from app.schemas.common import LocationSchema


# ============================================
# PROPERTY AGENT SCHEMAS
# ============================================

class PropertyAgentCreateRequest(BaseModel):
    """Create property agent request"""
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


class PropertyAgentResponse(BaseModel):
    """Property agent response"""
    id: UUID
    business_id: UUID
    agent_license_number: Optional[str]
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
    """Create property listing"""
    title: str = Field(..., min_length=10, max_length=255)
    description: str = Field(..., min_length=50)
    property_type: str
    property_subtype: str
    listing_type: str

    # Pricing
    price: Decimal = Field(..., gt=0)
    monthly_rent: Optional[Decimal] = Field(None, gt=0)
    service_charge: Optional[Decimal] = Field(None, ge=0)
    payment_frequency: Optional[str] = None
    security_deposit: Optional[Decimal] = Field(None, ge=0)
    lease_duration_months: Optional[int] = Field(None, gt=0)

    # Location
    address: str = Field(..., min_length=10)
    city: str = Field(..., min_length=2)
    state: str = Field(..., min_length=2)
    local_government: Optional[str] = None
    postal_code: Optional[str] = None
    location: LocationSchema

    # Property Details
    bedrooms: Optional[int] = Field(None, ge=0)
    bathrooms: Optional[int] = Field(None, ge=0)
    toilets: Optional[int] = Field(None, ge=0)
    living_rooms: Optional[int] = Field(None, ge=0)

    # Size
    plot_size_sqm: Optional[Decimal] = Field(None, gt=0)
    building_size_sqm: Optional[Decimal] = Field(None, gt=0)

    # Building Details
    year_built: Optional[int] = Field(None, ge=1900, le=2030)
    floors: Optional[int] = Field(None, ge=1)
    floor_number: Optional[int] = Field(None, ge=0)
    parking_spaces: int = Field(default=0, ge=0)

    # Condition
    condition: Optional[str] = None
    furnishing_status: Optional[str] = None

    # Features
    features: List[str] = Field(default_factory=list)

    # Documentation
    title_document_type: Optional[str] = None
    has_survey_plan: bool = False
    has_building_plan: bool = False

    # Media
    images: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)
    virtual_tour_url: Optional[str] = None
    floor_plan_images: List[str] = Field(default_factory=list)

    # Nearby Landmarks
    nearby_landmarks: List[Dict[str, Any]] = Field(default_factory=list)

    # Availability
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


class PropertyResponse(BaseModel):
    """Property response"""
    id: UUID
    agent_id: UUID
    title: str
    description: str
    property_type: str
    property_subtype: str
    listing_type: str

    price: Decimal
    monthly_rent: Optional[Decimal]
    service_charge: Optional[Decimal]

    address: str
    city: str
    state: str

    bedrooms: Optional[int]
    bathrooms: Optional[int]
    parking_spaces: int

    plot_size_sqm: Optional[Decimal]
    building_size_sqm: Optional[Decimal]

    condition: Optional[str]
    furnishing_status: Optional[str]

    features: List[str]
    images: List[str]
    virtual_tour_url: Optional[str]

    status: str
    is_featured: bool
    is_verified: bool
    is_active: bool

    views_count: int
    saves_count: int

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PropertyListResponse(BaseModel):
    """Simplified property list"""
    id: UUID
    title: str
    property_type: str
    property_subtype: str
    listing_type: str
    price: Decimal
    city: str
    state: str
    bedrooms: Optional[int]
    bathrooms: Optional[int]
    images: List[str]
    status: str
    is_featured: bool


# ============================================
# VIEWING SCHEMAS
# ============================================

class ViewingCreateRequest(BaseModel):
    """Create viewing request"""
    property_id: UUID
    viewing_date: date
    viewing_time: time
    viewing_type: str  # in_person, virtual
    customer_name: str = Field(..., min_length=2)
    customer_phone: str = Field(..., min_length=10)
    customer_email: Optional[str] = None
    number_of_people: int = Field(default=1, ge=1, le=10)
    special_requests: Optional[str] = None

    @field_validator('viewing_date')
    @classmethod
    def validate_future_date(cls, v):
        from datetime import date as dt_date
        if v < dt_date.today():
            raise ValueError('Viewing date must be in the future')
        return v

    @field_validator('viewing_type')
    @classmethod
    def validate_viewing_type(cls, v):
        valid_types = ["in_person", "virtual"]
        if v not in valid_types:
            raise ValueError(f'Invalid viewing type. Must be one of: {valid_types}')
        return v


class ViewingResponse(BaseModel):
    """Viewing response"""
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
    confirmation_code: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# OFFER SCHEMAS
# ============================================

class OfferCreateRequest(BaseModel):
    """Create property offer"""
    property_id: UUID
    offer_amount: Decimal = Field(..., gt=0)
    proposed_payment_plan: Optional[str] = None
    proposed_lease_duration: Optional[int] = Field(None, gt=0)
    message: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "property_id": "123e4567-e89b-12d3-a456-426614174000",
            "offer_amount": 80000000.00,
            "message": "I'm interested in this property and would like to make an offer"
        }
    })


class OfferResponse(BaseModel):
    """Offer response"""
    id: UUID
    property_id: UUID
    customer_id: UUID
    offer_amount: Decimal
    original_price: Decimal
    message: Optional[str]
    status: str
    counter_offer_amount: Optional[Decimal]
    counter_message: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# SEARCH FILTERS
# ============================================

class PropertySearchFilters(BaseModel):
    """Property search filters"""
    query: Optional[str] = None
    property_type: Optional[str] = None
    property_subtype: Optional[str] = None
    listing_type: Optional[str] = None

    # Location
    city: Optional[str] = None
    state: Optional[str] = None
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0)

    # Price Range
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)

    # Property Details
    min_bedrooms: Optional[int] = Field(None, ge=0)
    max_bedrooms: Optional[int] = Field(None, ge=0)
    min_bathrooms: Optional[int] = Field(None, ge=0)

    # Size
    min_plot_size: Optional[Decimal] = Field(None, gt=0)
    max_plot_size: Optional[Decimal] = Field(None, gt=0)

    # Filters
    furnishing_status: Optional[str] = None
    features: List[str] = Field(default_factory=list)
    is_featured: Optional[bool] = None
    is_verified: Optional[bool] = None

    # Sort
    sort_by: str = Field(default="created_at")  # created_at, price_asc, price_desc