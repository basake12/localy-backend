"""
app/models/services_model.py

FIXES vs previous version:
  1. ALL enum stored values changed from UPPERCASE to lowercase.
     Blueprint §14 / all other modules use lowercase enum values.
     UPPERCASE values made cross-module status comparisons, admin analytics,
     and Celery task filters fail silently.

     Before: PENDING="PENDING", CONFIRMED="CONFIRMED" …
     After:  PENDING="pending", CONFIRMED="confirmed" …

     MIGRATION REQUIRED: UPDATE service_bookings SET status = LOWER(status)
     before deploying. Same for payment_status.

  2. Platform fee column added to ServiceBooking — Blueprint §5.4:
     ₦100 flat fee on confirmed service bookings.

  3. No other structural changes — service module is otherwise
     well-constructed.
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


# ─── Enums — ALL lowercase stored values ──────────────────────────────────────

class ServiceLocationTypeEnum(str, enum.Enum):
    IN_HOME           = "in_home"
    PROVIDER_LOCATION = "provider_location"
    VIRTUAL           = "virtual"


class BookingStatusEnum(str, enum.Enum):
    PENDING     = "pending"
    CONFIRMED   = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    CANCELLED   = "cancelled"
    NO_SHOW     = "no_show"


class PaymentStatusEnum(str, enum.Enum):
    PENDING  = "pending"
    PAID     = "paid"
    FAILED   = "failed"
    REFUNDED = "refunded"


class PricingTypeEnum(str, enum.Enum):
    FIXED   = "fixed"
    HOURLY  = "hourly"
    PACKAGE = "package"


# ─── Service Provider ─────────────────────────────────────────────────────────

class ServiceProvider(BaseModel):
    """Service provider business details. Blueprint §6.3."""

    __tablename__ = "service_providers"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    qualifications        = Column(JSONB, default=list)
    certifications        = Column(JSONB, default=list)
    portfolio_images      = Column(JSONB, default=list)
    years_of_experience   = Column(Integer, nullable=True)

    service_location_types = Column(JSONB, default=list)  # ["in_home", "provider_location"]
    service_radius_km      = Column(Numeric(5, 2), nullable=True)
    travel_fee             = Column(Numeric(10, 2), default=0.00)

    provider_location = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=True
    )
    provider_address = Column(Text, nullable=True)

    advance_booking_days  = Column(Integer, default=30)
    buffer_time_minutes   = Column(Integer, default=15)

    total_bookings  = Column(Integer, default=0)
    completion_rate = Column(Numeric(5, 2), default=0.00)

    business     = relationship("Business", back_populates="service_provider")
    services     = relationship("Service", back_populates="provider", cascade="all, delete-orphan")
    availability = relationship("ServiceAvailability", back_populates="provider", cascade="all, delete-orphan")
    bookings     = relationship("ServiceBooking", back_populates="provider")

    def __repr__(self) -> str:
        return f"<ServiceProvider business={self.business_id}>"


# ─── Service ──────────────────────────────────────────────────────────────────

class Service(BaseModel):
    """Individual service offered by a provider. Blueprint §6.3."""

    __tablename__ = "services"

    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name        = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    category    = Column(String(100), nullable=False, index=True)
    subcategory = Column(String(100), nullable=True, index=True)

    base_price   = Column(Numeric(10, 2), nullable=False)
    pricing_type = Column(Enum(PricingTypeEnum), default=PricingTypeEnum.FIXED, nullable=False)
    duration_minutes = Column(Integer, nullable=True)

    service_options = Column(JSONB, default=list)
    images          = Column(JSONB, default=list)
    videos          = Column(JSONB, default=list)

    bookings_count = Column(Integer, default=0)
    average_rating = Column(Numeric(3, 2), default=0.00)
    total_reviews  = Column(Integer, default=0)

    is_active = Column(Boolean, default=True, nullable=False, index=True)

    provider = relationship("ServiceProvider", back_populates="services")
    bookings = relationship("ServiceBooking", back_populates="service")

    __table_args__ = (
        CheckConstraint("base_price > 0",        name="positive_service_price"),
        CheckConstraint("duration_minutes > 0",  name="positive_duration"),
    )

    def __repr__(self) -> str:
        return f"<Service {self.name}>"


# ─── Service Availability ─────────────────────────────────────────────────────

class ServiceAvailability(BaseModel):
    """Provider working hours and slot availability. Blueprint §6.3."""

    __tablename__ = "service_availability"

    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    day_of_week  = Column(Integer, nullable=False)
    is_available = Column(Boolean, default=True, nullable=False)
    start_time   = Column(Time, nullable=False)
    end_time     = Column(Time, nullable=False)

    break_start = Column(Time, nullable=True)
    break_end   = Column(Time, nullable=True)

    slot_duration_minutes  = Column(Integer, default=60)
    max_bookings_per_slot  = Column(Integer, default=1)

    provider = relationship("ServiceProvider", back_populates="availability")

    __table_args__ = (
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="valid_day"),
        CheckConstraint("end_time > start_time", name="valid_time_range"),
        UniqueConstraint("provider_id", "day_of_week", name="unique_provider_day"),
    )

    def __repr__(self) -> str:
        return f"<ServiceAvailability provider={self.provider_id} day={self.day_of_week}>"


# ─── Service Booking ──────────────────────────────────────────────────────────

class ServiceBooking(BaseModel):
    """Customer service booking. Blueprint §6.3 / §5.4."""

    __tablename__ = "service_bookings"

    service_id  = Column(UUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("service_providers.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    booking_date     = Column(Date, nullable=False, index=True)
    booking_time     = Column(Time, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    number_of_people = Column(Integer, default=1)

    service_location_type = Column(Enum(ServiceLocationTypeEnum), nullable=False)
    service_address       = Column(Text, nullable=True)
    service_location      = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=True
    )

    # Blueprint §5.6 HARD RULE: NUMERIC(12,2)
    base_price    = Column(Numeric(12, 2), nullable=False)
    add_ons_price = Column(Numeric(12, 2), default=0.00)
    travel_fee    = Column(Numeric(12, 2), default=0.00)

    # Blueprint §5.4: ₦100 flat platform fee on service bookings
    platform_fee = Column(Numeric(12, 2), nullable=False, default=100.00)
    total_price  = Column(Numeric(12, 2), nullable=False)

    selected_options = Column(JSONB, default=list)
    special_requests = Column(Text, nullable=True)

    # Blueprint — lowercase enum values enforced here
    status = Column(
        Enum(BookingStatusEnum),
        default=BookingStatusEnum.PENDING,
        nullable=False,
        index=True,
    )
    payment_status = Column(
        Enum(PaymentStatusEnum),
        default=PaymentStatusEnum.PENDING,
        nullable=False,
        index=True,
    )
    payment_reference = Column(String(100), nullable=True)

    started_at         = Column(DateTime(timezone=True), nullable=True)
    completed_at       = Column(DateTime(timezone=True), nullable=True)
    cancelled_at       = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    service  = relationship("Service", back_populates="bookings")
    provider = relationship("ServiceProvider", back_populates="bookings")
    customer = relationship("User", foreign_keys=[customer_id])

    __table_args__ = (
        CheckConstraint("total_price >= 0",    name="non_negative_total"),
        CheckConstraint("number_of_people > 0", name="positive_people"),
        CheckConstraint("platform_fee >= 0",    name="non_negative_platform_fee"),
    )

    def __repr__(self) -> str:
        return f"<ServiceBooking {self.id} {self.status}>"


# ─── Service Package ──────────────────────────────────────────────────────────

class ServicePackage(BaseModel):
    """Bundled service packages. Blueprint §6.3."""

    __tablename__ = "service_packages"

    provider_id = Column(UUID(as_uuid=True), ForeignKey("service_providers.id", ondelete="CASCADE"), nullable=False, index=True)

    name             = Column(String(255), nullable=False)
    description      = Column(Text, nullable=True)
    included_services = Column(JSONB, nullable=False)

    regular_price = Column(Numeric(10, 2), nullable=False)
    package_price = Column(Numeric(10, 2), nullable=False)
    savings       = Column(Numeric(10, 2), nullable=False)

    valid_for_days = Column(Integer, default=30)
    is_active      = Column(Boolean, default=True)

    provider = relationship("ServiceProvider")

    __table_args__ = (
        CheckConstraint("package_price < regular_price", name="package_is_discounted"),
    )

    def __repr__(self) -> str:
        return f"<ServicePackage {self.name}>"