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

class ServiceLocationTypeEnum(str, enum.Enum):
    IN_HOME = "in_home"  # Provider comes to customer
    PROVIDER_LOCATION = "provider_location"  # Customer goes to salon/studio
    VIRTUAL = "virtual"  # Online service (tutoring, consultation)


class BookingStatusEnum(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class PaymentStatusEnum(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class PricingTypeEnum(str, enum.Enum):
    FIXED = "fixed"  # One-time fixed price
    HOURLY = "hourly"  # Charged per hour
    PACKAGE = "package"  # Bundled services


# ============================================
# SERVICE PROVIDER MODEL
# ============================================

class ServiceProvider(BaseModel):
    """Service provider business details"""

    __tablename__ = "service_providers"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Professional Details
    qualifications = Column(JSONB, default=list)  # ["Certified Beautician", "5 years experience"]
    certifications = Column(JSONB, default=list)  # ["ISO certified", "Licensed plumber"]
    portfolio_images = Column(JSONB, default=list)  # Portfolio/gallery
    years_of_experience = Column(Integer, nullable=True)

    # Service Settings
    service_location_types = Column(JSONB, default=list)  # ["in_home", "provider_location"]
    service_radius_km = Column(Numeric(5, 2), nullable=True)  # For in-home services
    travel_fee = Column(Numeric(10, 2), default=0.00)

    # Provider Location (if they have a physical location)
    provider_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=True), nullable=True)
    provider_address = Column(Text, nullable=True)

    # Availability Settings
    advance_booking_days = Column(Integer, default=30)  # How far ahead customers can book
    buffer_time_minutes = Column(Integer, default=15)  # Time between appointments

    # Stats
    total_bookings = Column(Integer, default=0)
    completion_rate = Column(Numeric(5, 2), default=0.00)

    # Relationships
    business = relationship("Business", back_populates="service_provider")
    services = relationship(
        "Service",
        back_populates="provider",
        cascade="all, delete-orphan"
    )
    availability = relationship(
        "ServiceAvailability",
        back_populates="provider",
        cascade="all, delete-orphan"
    )
    bookings = relationship(
        "ServiceBooking",
        back_populates="provider"
    )

    def __repr__(self):
        return f"<ServiceProvider {self.business_id}>"


# ============================================
# SERVICE MODEL
# ============================================

class Service(BaseModel):
    """Individual services offered by provider"""

    __tablename__ = "services"

    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Basic Info
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=False, index=True)  # beauty, cleaning, repairs, tutoring
    subcategory = Column(String(100), nullable=True, index=True)  # haircut, deep_cleaning, ac_repair

    # Pricing
    base_price = Column(Numeric(10, 2), nullable=False)
    pricing_type = Column(
        Enum(PricingTypeEnum),
        default=PricingTypeEnum.FIXED,
        nullable=False
    )
    duration_minutes = Column(Integer, nullable=True)  # Expected duration

    # Service Options/Variants
    service_options = Column(JSONB, default=list)
    # Example: [
    #   {"name": "Hair Length", "type": "select", "options": ["Short", "Medium", "Long"], "price_modifier": [0, 5000, 10000]},
    #   {"name": "Add Hair Treatment", "type": "addon", "price": 15000}
    # ]

    # Media
    images = Column(JSONB, default=list)
    videos = Column(JSONB, default=list)

    # Stats
    bookings_count = Column(Integer, default=0)
    average_rating = Column(Numeric(3, 2), default=0.00)
    total_reviews = Column(Integer, default=0)

    # Status
    is_active = Column(Boolean, default=True, index=True)

    # Relationships
    provider = relationship("ServiceProvider", back_populates="services")
    bookings = relationship(
        "ServiceBooking",
        back_populates="service"
    )

    __table_args__ = (
        CheckConstraint('base_price > 0', name='positive_service_price'),
        CheckConstraint('duration_minutes > 0', name='positive_duration'),
    )

    def __repr__(self):
        return f"<Service {self.name}>"


# ============================================
# SERVICE AVAILABILITY MODEL
# ============================================

class ServiceAvailability(BaseModel):
    """Provider's working hours and availability"""

    __tablename__ = "service_availability"

    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    is_available = Column(Boolean, default=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    # Break times
    break_start = Column(Time, nullable=True)
    break_end = Column(Time, nullable=True)

    # Slot Settings
    slot_duration_minutes = Column(Integer, default=60)
    max_bookings_per_slot = Column(Integer, default=1)

    # Relationships
    provider = relationship("ServiceProvider", back_populates="availability")

    __table_args__ = (
        CheckConstraint('day_of_week >= 0 AND day_of_week <= 6', name='valid_day'),
        CheckConstraint('end_time > start_time', name='valid_time_range'),
        UniqueConstraint('provider_id', 'day_of_week', name='unique_provider_day'),
    )

    def __repr__(self):
        return f"<ServiceAvailability {self.provider_id} Day {self.day_of_week}>"


# ============================================
# SERVICE BOOKING MODEL
# ============================================

class ServiceBooking(BaseModel):
    """Customer service bookings"""

    __tablename__ = "service_bookings"

    service_id = Column(
        UUID(as_uuid=True),
        ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Booking Details
    booking_date = Column(Date, nullable=False, index=True)
    booking_time = Column(Time, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    number_of_people = Column(Integer, default=1)

    # Location
    service_location_type = Column(
        Enum(ServiceLocationTypeEnum),
        nullable=False
    )
    service_address = Column(Text, nullable=True)
    service_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=True), nullable=True)

    # Pricing
    base_price = Column(Numeric(10, 2), nullable=False)
    add_ons_price = Column(Numeric(10, 2), default=0.00)
    travel_fee = Column(Numeric(10, 2), default=0.00)
    total_price = Column(Numeric(10, 2), nullable=False)

    # Selected Options
    selected_options = Column(JSONB, default=list)
    # Example: [{"name": "Hair Length", "value": "Long", "price": 10000}]

    # Special Requests
    special_requests = Column(Text, nullable=True)

    # Status
    status = Column(
        Enum(BookingStatusEnum),
        default=BookingStatusEnum.PENDING,
        nullable=False,
        index=True
    )
    payment_status = Column(
        Enum(PaymentStatusEnum),
        default=PaymentStatusEnum.PENDING,
        nullable=False,
        index=True
    )
    payment_reference = Column(String(100), nullable=True)

    # Completion
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Cancellation
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    # Relationships
    service = relationship("Service", back_populates="bookings")
    provider = relationship("ServiceProvider", back_populates="bookings")
    customer = relationship("User", foreign_keys=[customer_id])

    __table_args__ = (
        CheckConstraint('total_price >= 0', name='non_negative_total'),
        CheckConstraint('number_of_people > 0', name='positive_people'),
    )

    def __repr__(self):
        return f"<ServiceBooking {self.id} - {self.status}>"


# ============================================
# SERVICE PACKAGE MODEL (Optional)
# ============================================

class ServicePackage(BaseModel):
    """Bundled service packages"""

    __tablename__ = "service_packages"

    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Included Services
    included_services = Column(JSONB, nullable=False)
    # Example: [{"service_id": "uuid", "quantity": 1}, ...]

    # Pricing
    regular_price = Column(Numeric(10, 2), nullable=False)
    package_price = Column(Numeric(10, 2), nullable=False)
    savings = Column(Numeric(10, 2), nullable=False)  # Auto-calculated

    # Validity
    valid_for_days = Column(Integer, default=30)  # Package validity

    # Status
    is_active = Column(Boolean, default=True)

    # Relationships
    provider = relationship("ServiceProvider")

    __table_args__ = (
        CheckConstraint('package_price < regular_price', name='package_is_discounted'),
    )

    def __repr__(self):
        return f"<ServicePackage {self.name}>"