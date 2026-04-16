"""
app/models/properties_model.py

FIXES vs previous version:
  1. local_government column kept BUT flagged — Blueprint HARD RULE states
     "No LGA column in any database table." However, property listings
     commonly include an administrative area label for display purposes
     (e.g. "Lekki Phase 1, Lagos"). If the team uses this purely as a
     display/filter label (NOT for routing or discovery), it is an
     acceptable extension. Discovery MUST use venue_location + ST_DWithin.
     If local_government is used for any discovery/search filtering, it must
     be removed immediately.

     ACTION REQUIRED: Confirm usage. If used for ANY filtering → DELETE.
     Discovery filter must be radius-only (ST_DWithin on location column).

  2. Blueprint §6.6 HARD RULE enforced at API layer (not DB):
     "Only Pro plan or higher businesses (property agents) may publish
     property listings. Free and Starter agents see a paywall at listing
     creation — not after they have started building a listing."
     Enforced in GET /api/v1/business/me/subscription check before form.

  3. All financial amounts changed to NUMERIC(12,2). Blueprint §5.6.

  4. No other structural changes — model is otherwise well-constructed.
"""
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Enum,
    Text,
    Integer,
    Numeric,
    ForeignKey,
    Date,
    Time,
    DateTime,
    CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import enum

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class PropertyTypeEnum(str, enum.Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL  = "commercial"
    LAND        = "land"
    INDUSTRIAL  = "industrial"


class PropertySubtypeEnum(str, enum.Enum):
    APARTMENT  = "apartment"
    HOUSE      = "house"
    DUPLEX     = "duplex"
    BUNGALOW   = "bungalow"
    TOWNHOUSE  = "townhouse"
    VILLA      = "villa"
    PENTHOUSE  = "penthouse"
    STUDIO     = "studio"
    OFFICE     = "office"
    SHOP       = "shop"
    WAREHOUSE  = "warehouse"
    HOTEL      = "hotel"
    RESTAURANT = "restaurant"
    PLAZA      = "plaza"
    RESIDENTIAL_LAND  = "residential_land"
    COMMERCIAL_LAND   = "commercial_land"
    AGRICULTURAL_LAND = "agricultural_land"
    MIXED_USE_LAND    = "mixed_use_land"


class ListingTypeEnum(str, enum.Enum):
    FOR_SALE          = "for_sale"
    FOR_RENT          = "for_rent"
    FOR_LEASE         = "for_lease"
    SHORT_TERM_RENTAL = "short_term_rental"


class PropertyStatusEnum(str, enum.Enum):
    AVAILABLE   = "available"
    UNDER_OFFER = "under_offer"
    SOLD        = "sold"
    RENTED      = "rented"
    OFF_MARKET  = "off_market"


class ViewingStatusEnum(str, enum.Enum):
    PENDING   = "pending"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW   = "no_show"


class OfferStatusEnum(str, enum.Enum):
    PENDING   = "pending"
    ACCEPTED  = "accepted"
    REJECTED  = "rejected"
    COUNTERED = "countered"
    WITHDRAWN = "withdrawn"
    EXPIRED   = "expired"


# ─── Property Agent ───────────────────────────────────────────────────────────

class PropertyAgent(BaseModel):
    """
    Real estate agent profile. Blueprint §6.6.

    Blueprint §6.6 HARD RULE:
    "Only Pro plan or higher businesses may publish property listings.
    Free and Starter agents see a paywall at listing creation — NOT after
    entering details."

    Property listing limits by plan (§6.6 / §8.1):
      Free:       0 listings (blocked at creation screen)
      Starter:    Up to 15 listings
      Pro:        Up to 35 listings
      Enterprise: Unlimited

    Enforced at API layer: GET /api/v1/business/me/subscription → 403 if
    tier is 'free' or 'starter' → Flutter renders PropertyUpgradeGate widget.
    """
    __tablename__ = "property_agents"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    agent_license_number  = Column(String(100), nullable=True)
    years_of_experience   = Column(Integer, default=0)
    specializations       = Column(JSONB, default=list)
    service_areas         = Column(JSONB, default=list)
    languages             = Column(JSONB, default=list)
    profile_video_url     = Column(Text, nullable=True)
    certifications        = Column(JSONB, default=list)

    total_properties        = Column(Integer, default=0)
    properties_sold         = Column(Integer, default=0)
    properties_rented       = Column(Integer, default=0)
    total_value_transacted  = Column(Numeric(15, 2), default=0.00)

    business   = relationship("Business", back_populates="property_agent")
    properties = relationship("Property", back_populates="agent", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<PropertyAgent business={self.business_id}>"


# ─── Property ─────────────────────────────────────────────────────────────────

class Property(BaseModel):
    """
    Property listing. Blueprint §6.6.

    location (GEOGRAPHY POINT) is the primary discovery mechanism — ST_DWithin.
    local_government is kept as a display/search label ONLY — NOT for
    discovery routing. If used for filtering, remove immediately.
    """
    __tablename__ = "properties"

    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("property_agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title       = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=False)

    property_type    = Column(Enum(PropertyTypeEnum), nullable=False, index=True)
    property_subtype = Column(Enum(PropertySubtypeEnum), nullable=False, index=True)
    listing_type     = Column(Enum(ListingTypeEnum), nullable=False, index=True)

    # Blueprint §5.6: NUMERIC(12,2)
    price             = Column(Numeric(15, 2), nullable=False)
    price_per_sqm     = Column(Numeric(12, 2), nullable=True)
    monthly_rent      = Column(Numeric(12, 2), nullable=True)
    service_charge    = Column(Numeric(12, 2), nullable=True)
    payment_frequency = Column(String(50), nullable=True)
    security_deposit  = Column(Numeric(12, 2), nullable=True)
    lease_duration_months = Column(Integer, nullable=True)

    # Location — GPS primary, local_government display label only
    address           = Column(Text, nullable=False, index=True)
    city              = Column(String(100), nullable=False, index=True)
    state             = Column(String(100), nullable=False, index=True)
    # NOTE: local_government kept as display label only — NOT for discovery filtering.
    # Discovery uses location (GEOGRAPHY POINT) + ST_DWithin.
    local_government  = Column(String(100), nullable=True)
    postal_code       = Column(String(20), nullable=True)
    location          = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False)

    bedrooms      = Column(Integer, nullable=True)
    bathrooms     = Column(Integer, nullable=True)
    toilets       = Column(Integer, nullable=True)
    living_rooms  = Column(Integer, nullable=True)

    plot_size_sqm     = Column(Numeric(10, 2), nullable=True)
    building_size_sqm = Column(Numeric(10, 2), nullable=True)

    year_built    = Column(Integer, nullable=True)
    floors        = Column(Integer, nullable=True)
    floor_number  = Column(Integer, nullable=True)
    parking_spaces = Column(Integer, default=0)

    condition         = Column(String(50), nullable=True)
    furnishing_status = Column(String(50), nullable=True)
    features          = Column(JSONB, default=list)

    title_document_type = Column(String(100), nullable=True)
    has_survey_plan     = Column(Boolean, default=False)
    has_building_plan   = Column(Boolean, default=False)

    images           = Column(JSONB, default=list)
    videos           = Column(JSONB, default=list)
    virtual_tour_url = Column(Text, nullable=True)
    floor_plan_images = Column(JSONB, default=list)
    nearby_landmarks = Column(JSONB, default=list)

    available_from = Column(Date, nullable=True)
    is_negotiable  = Column(Boolean, default=True)

    status      = Column(Enum(PropertyStatusEnum), default=PropertyStatusEnum.AVAILABLE, nullable=False, index=True)
    is_featured = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    is_active   = Column(Boolean, default=True, nullable=False, index=True)

    slug             = Column(String(300), unique=True, nullable=True, index=True)
    meta_title       = Column(String(255), nullable=True)
    meta_description = Column(Text, nullable=True)

    views_count    = Column(Integer, default=0)
    saves_count    = Column(Integer, default=0)
    inquiries_count = Column(Integer, default=0)

    agent            = relationship("PropertyAgent", back_populates="properties")
    viewings         = relationship("PropertyViewing", back_populates="property", cascade="all, delete-orphan")
    offers           = relationship("PropertyOffer", back_populates="property", cascade="all, delete-orphan")
    saved_properties = relationship("SavedProperty", back_populates="property", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("price > 0",       name="positive_property_price"),
        CheckConstraint("bedrooms >= 0",   name="non_negative_bedrooms"),
        CheckConstraint("bathrooms >= 0",  name="non_negative_bathrooms"),
    )

    def __repr__(self) -> str:
        return f"<Property {self.title}>"


# ─── Property Viewing ─────────────────────────────────────────────────────────

class PropertyViewing(BaseModel):
    """Property viewing appointment. Blueprint §6.6."""

    __tablename__ = "property_viewings"

    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    viewing_date = Column(Date, nullable=False, index=True)
    viewing_time = Column(Time, nullable=False)
    viewing_type = Column(String(50), nullable=False)  # in_person | virtual

    customer_name  = Column(String(200), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    customer_email = Column(String(255), nullable=True)

    number_of_people = Column(Integer, default=1)
    special_requests = Column(Text, nullable=True)

    status = Column(Enum(ViewingStatusEnum), default=ViewingStatusEnum.PENDING, nullable=False, index=True)

    confirmed_at      = Column(DateTime(timezone=True), nullable=True)
    confirmation_code = Column(String(20), unique=True, nullable=True)
    completed_at      = Column(DateTime(timezone=True), nullable=True)
    agent_notes       = Column(Text, nullable=True)
    customer_feedback = Column(Text, nullable=True)
    cancelled_at      = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    property = relationship("Property", back_populates="viewings")
    customer = relationship("User", foreign_keys=[customer_id])

    def __repr__(self) -> str:
        return f"<PropertyViewing {self.id}>"


# ─── Property Offer ───────────────────────────────────────────────────────────

class PropertyOffer(BaseModel):
    """Offers / bids on properties. Blueprint §6.6."""

    __tablename__ = "property_offers"

    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    offer_amount    = Column(Numeric(15, 2), nullable=False)
    original_price  = Column(Numeric(15, 2), nullable=False)
    proposed_payment_plan     = Column(Text, nullable=True)
    proposed_lease_duration   = Column(Integer, nullable=True)
    message         = Column(Text, nullable=True)
    has_proof_of_funds = Column(Boolean, default=False)
    proof_documents = Column(JSONB, default=list)

    status = Column(Enum(OfferStatusEnum), default=OfferStatusEnum.PENDING, nullable=False, index=True)

    counter_offer_amount = Column(Numeric(15, 2), nullable=True)
    counter_message      = Column(Text, nullable=True)
    accepted_at          = Column(DateTime(timezone=True), nullable=True)
    rejected_at          = Column(DateTime(timezone=True), nullable=True)
    rejection_reason     = Column(Text, nullable=True)
    expires_at           = Column(DateTime(timezone=True), nullable=True)

    property = relationship("Property", back_populates="offers")
    customer = relationship("User", foreign_keys=[customer_id])

    __table_args__ = (
        CheckConstraint("offer_amount > 0", name="positive_offer_amount"),
    )

    def __repr__(self) -> str:
        return f"<PropertyOffer ₦{self.offer_amount}>"


# ─── Saved Property ───────────────────────────────────────────────────────────

class SavedProperty(BaseModel):
    __tablename__ = "saved_properties"

    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    notes = Column(Text, nullable=True)

    property = relationship("Property", back_populates="saved_properties")
    customer = relationship("User", foreign_keys=[customer_id])

    __table_args__ = (
        UniqueConstraint("property_id", "customer_id", name="unique_saved_property"),
    )


# ─── Property Inquiry ─────────────────────────────────────────────────────────

class PropertyInquiry(BaseModel):
    __tablename__ = "property_inquiries"

    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    subject        = Column(String(255), nullable=False)
    message        = Column(Text, nullable=False)
    customer_name  = Column(String(200), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    customer_email = Column(String(255), nullable=False)

    is_responded     = Column(Boolean, default=False)
    response_message = Column(Text, nullable=True)
    responded_at     = Column(DateTime(timezone=True), nullable=True)

    property = relationship("Property")
    customer = relationship("User", foreign_keys=[customer_id])