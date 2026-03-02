from sqlalchemy import (
    Column, String, Boolean, Enum, Text, Integer,
    Numeric, ForeignKey, Date, Time, DateTime, CheckConstraint, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class PropertyTypeEnum(str, enum.Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    LAND = "land"
    INDUSTRIAL = "industrial"


class PropertySubtypeEnum(str, enum.Enum):
    # Residential
    APARTMENT = "apartment"
    HOUSE = "house"
    DUPLEX = "duplex"
    BUNGALOW = "bungalow"
    TOWNHOUSE = "townhouse"
    VILLA = "villa"
    PENTHOUSE = "penthouse"
    STUDIO = "studio"

    # Commercial
    OFFICE = "office"
    SHOP = "shop"
    WAREHOUSE = "warehouse"
    HOTEL = "hotel"
    RESTAURANT = "restaurant"
    PLAZA = "plaza"

    # Land
    RESIDENTIAL_LAND = "residential_land"
    COMMERCIAL_LAND = "commercial_land"
    AGRICULTURAL_LAND = "agricultural_land"
    MIXED_USE_LAND = "mixed_use_land"


class ListingTypeEnum(str, enum.Enum):
    FOR_SALE = "for_sale"
    FOR_RENT = "for_rent"
    FOR_LEASE = "for_lease"
    SHORT_TERM_RENTAL = "short_term_rental"


class PropertyStatusEnum(str, enum.Enum):
    AVAILABLE = "available"
    UNDER_OFFER = "under_offer"
    SOLD = "sold"
    RENTED = "rented"
    OFF_MARKET = "off_market"


class ViewingStatusEnum(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class OfferStatusEnum(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COUNTERED = "countered"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


# ============================================
# PROPERTY AGENT MODEL
# ============================================

class PropertyAgent(BaseModel):
    """Real estate agents"""

    __tablename__ = "property_agents"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Agent Details
    agent_license_number = Column(String(100), nullable=True)
    years_of_experience = Column(Integer, default=0)

    # Specializations
    specializations = Column(JSONB, default=list)  # ["residential", "commercial", "luxury"]
    service_areas = Column(JSONB, default=list)  # Cities/areas covered

    # Languages Spoken
    languages = Column(JSONB, default=list)  # ["english", "yoruba", "igbo"]

    # Media
    profile_video_url = Column(Text, nullable=True)
    certifications = Column(JSONB, default=list)  # Certification documents

    # Stats
    total_properties = Column(Integer, default=0)
    properties_sold = Column(Integer, default=0)
    properties_rented = Column(Integer, default=0)
    total_value_transacted = Column(Numeric(15, 2), default=0.00)

    # Relationships
    business = relationship("Business", back_populates="property_agent")
    properties = relationship(
        "Property",
        back_populates="agent",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<PropertyAgent {self.business_id}>"


# ============================================
# PROPERTY MODEL
# ============================================

class Property(BaseModel):
    """Property listings"""

    __tablename__ = "properties"

    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("property_agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Basic Info
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=False)

    # Property Type
    property_type = Column(
        Enum(PropertyTypeEnum),
        nullable=False,
        index=True
    )
    property_subtype = Column(
        Enum(PropertySubtypeEnum),
        nullable=False,
        index=True
    )

    # Listing Type
    listing_type = Column(
        Enum(ListingTypeEnum),
        nullable=False,
        index=True
    )

    # Pricing
    price = Column(Numeric(15, 2), nullable=False)  # Sale price or annual rent
    price_per_sqm = Column(Numeric(10, 2), nullable=True)
    monthly_rent = Column(Numeric(12, 2), nullable=True)  # For rentals
    service_charge = Column(Numeric(10, 2), nullable=True)  # Monthly service charge

    # Payment Terms (for rent/lease)
    payment_frequency = Column(String(50), nullable=True)  # monthly, quarterly, annually
    security_deposit = Column(Numeric(12, 2), nullable=True)
    lease_duration_months = Column(Integer, nullable=True)

    # Location
    address = Column(Text, nullable=False, index=True)
    city = Column(String(100), nullable=False, index=True)
    state = Column(String(100), nullable=False, index=True)
    local_government = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=True)
    location = Column(Geography(geometry_type='POINT', srid=4326), nullable=False, index=True)

    # Property Details
    bedrooms = Column(Integer, nullable=True)
    bathrooms = Column(Integer, nullable=True)
    toilets = Column(Integer, nullable=True)
    living_rooms = Column(Integer, nullable=True)

    # Size
    plot_size_sqm = Column(Numeric(10, 2), nullable=True)  # Land area
    building_size_sqm = Column(Numeric(10, 2), nullable=True)  # Built-up area

    # Building Details
    year_built = Column(Integer, nullable=True)
    floors = Column(Integer, nullable=True)
    floor_number = Column(Integer, nullable=True)  # For apartments
    parking_spaces = Column(Integer, default=0)

    # Condition
    condition = Column(String(50), nullable=True)  # new, excellent, good, fair, needs_renovation
    furnishing_status = Column(String(50), nullable=True)  # furnished, semi_furnished, unfurnished

    # Features & Amenities
    features = Column(JSONB, default=list)
    # ["swimming_pool", "gym", "security", "generator", "borehole", "AC", "fitted_kitchen"]

    # Documentation
    title_document_type = Column(String(100), nullable=True)  # C of O, Deed, Allocation
    has_survey_plan = Column(Boolean, default=False)
    has_building_plan = Column(Boolean, default=False)

    # Media
    images = Column(JSONB, default=list)  # Image URLs
    videos = Column(JSONB, default=list)  # Video URLs
    virtual_tour_url = Column(Text, nullable=True)
    floor_plan_images = Column(JSONB, default=list)

    # Nearby Landmarks
    nearby_landmarks = Column(JSONB, default=list)
    # [{"name": "Shoprite", "distance_km": 2.5, "type": "shopping"}]

    # Availability
    available_from = Column(Date, nullable=True)
    is_negotiable = Column(Boolean, default=True)

    # Status
    status = Column(
        Enum(PropertyStatusEnum),
        default=PropertyStatusEnum.AVAILABLE,
        nullable=False,
        index=True
    )
    is_featured = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True, index=True)

    # SEO
    slug = Column(String(300), unique=True, nullable=True, index=True)
    meta_title = Column(String(255), nullable=True)
    meta_description = Column(Text, nullable=True)

    # Stats
    views_count = Column(Integer, default=0)
    saves_count = Column(Integer, default=0)
    inquiries_count = Column(Integer, default=0)

    # Relationships
    agent = relationship("PropertyAgent", back_populates="properties")
    viewings = relationship(
        "PropertyViewing",
        back_populates="property",
        cascade="all, delete-orphan"
    )
    offers = relationship(
        "PropertyOffer",
        back_populates="property",
        cascade="all, delete-orphan"
    )
    saved_properties = relationship(
        "SavedProperty",
        back_populates="property",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint('price > 0', name='positive_property_price'),
        CheckConstraint('bedrooms >= 0', name='non_negative_bedrooms'),
        CheckConstraint('bathrooms >= 0', name='non_negative_bathrooms'),
    )

    def __repr__(self):
        return f"<Property {self.title}>"


# ============================================
# PROPERTY VIEWING MODEL
# ============================================

class PropertyViewing(BaseModel):
    """Property viewing/tour appointments"""

    __tablename__ = "property_viewings"

    property_id = Column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Viewing Details
    viewing_date = Column(Date, nullable=False, index=True)
    viewing_time = Column(Time, nullable=False)
    viewing_type = Column(String(50), nullable=False)  # in_person, virtual

    # Contact Info
    customer_name = Column(String(200), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    customer_email = Column(String(255), nullable=True)

    # Additional Info
    number_of_people = Column(Integer, default=1)
    special_requests = Column(Text, nullable=True)

    # Status
    status = Column(
        Enum(ViewingStatusEnum),
        default=ViewingStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    # Confirmation
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmation_code = Column(String(20), unique=True, nullable=True)

    # Completion
    completed_at = Column(DateTime(timezone=True), nullable=True)
    agent_notes = Column(Text, nullable=True)
    customer_feedback = Column(Text, nullable=True)

    # Cancellation
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    # Relationships
    property = relationship("Property", back_populates="viewings")
    customer = relationship("User", foreign_keys=[customer_id])

    def __repr__(self):
        return f"<PropertyViewing {self.id}>"


# ============================================
# PROPERTY OFFER MODEL
# ============================================

class PropertyOffer(BaseModel):
    """Offers/bids on properties"""

    __tablename__ = "property_offers"

    property_id = Column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Offer Details
    offer_amount = Column(Numeric(15, 2), nullable=False)
    original_price = Column(Numeric(15, 2), nullable=False)

    # Payment Terms (for rent/lease)
    proposed_payment_plan = Column(Text, nullable=True)
    proposed_lease_duration = Column(Integer, nullable=True)  # months

    # Message
    message = Column(Text, nullable=True)

    # Proof of Funds
    has_proof_of_funds = Column(Boolean, default=False)
    proof_documents = Column(JSONB, default=list)  # Document URLs

    # Status
    status = Column(
        Enum(OfferStatusEnum),
        default=OfferStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    # Counter Offer
    counter_offer_amount = Column(Numeric(15, 2), nullable=True)
    counter_message = Column(Text, nullable=True)

    # Acceptance
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)

    # Expiry
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    property = relationship("Property", back_populates="offers")
    customer = relationship("User", foreign_keys=[customer_id])

    __table_args__ = (
        CheckConstraint('offer_amount > 0', name='positive_offer_amount'),
    )

    def __repr__(self):
        return f"<PropertyOffer {self.id} - ₦{self.offer_amount}>"


# ============================================
# SAVED PROPERTY MODEL
# ============================================

class SavedProperty(BaseModel):
    """User's saved/favorite properties"""

    __tablename__ = "saved_properties"

    property_id = Column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Notes
    notes = Column(Text, nullable=True)

    # Relationships
    property = relationship("Property", back_populates="saved_properties")
    customer = relationship("User", foreign_keys=[customer_id])

    __table_args__ = (
        UniqueConstraint('property_id', 'customer_id', name='unique_saved_property'),
    )

    def __repr__(self):
        return f"<SavedProperty {self.property_id}>"


# ============================================
# PROPERTY INQUIRY MODEL
# ============================================

class PropertyInquiry(BaseModel):
    """General inquiries about properties"""

    __tablename__ = "property_inquiries"

    property_id = Column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Inquiry Details
    subject = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)

    # Contact Info
    customer_name = Column(String(200), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    customer_email = Column(String(255), nullable=False)

    # Response
    is_responded = Column(Boolean, default=False)
    response_message = Column(Text, nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    property = relationship("Property")
    customer = relationship("User", foreign_keys=[customer_id])

    def __repr__(self):
        return f"<PropertyInquiry {self.subject}>"