from sqlalchemy import (
    Column, String, Boolean, Enum, Text, Integer,
    Numeric, ForeignKey, Date, Time, DateTime, CheckConstraint, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import enum

from app.models.base import BaseModel


# ============================================
# ENUMS
# ============================================

class EventCategoryEnum(str, enum.Enum):
    CONCERT = "concert"
    CONFERENCE = "conference"
    SPORTS = "sports"
    THEATER = "theater"
    FESTIVAL = "festival"
    WORKSHOP = "workshop"
    EXHIBITION = "exhibition"
    NETWORKING = "networking"
    COMEDY = "comedy"
    RELIGIOUS = "religious"
    OTHER = "other"


class TransportTypeEnum(str, enum.Enum):
    BUS = "bus"
    TRAIN = "train"
    FLIGHT = "flight"
    FERRY = "ferry"


class TicketStatusEnum(str, enum.Enum):
    AVAILABLE = "available"
    SOLD_OUT = "sold_out"
    CANCELLED = "cancelled"


class BookingStatusEnum(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    NO_SHOW = "no_show"


class PaymentStatusEnum(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


# ============================================
# TICKET EVENT MODEL
# ============================================

class TicketEvent(BaseModel):
    """Events and transport schedules"""

    __tablename__ = "ticket_events"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Event Type
    event_type = Column(String(50), nullable=False)  # event, transport

    # Basic Info
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    category = Column(
        Enum(EventCategoryEnum),
        nullable=True  # Only for events
    )

    # For Events
    event_date = Column(Date, nullable=True, index=True)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)

    # For Transport
    transport_type = Column(
        Enum(TransportTypeEnum),
        nullable=True
    )
    departure_date = Column(Date, nullable=True, index=True)
    departure_time = Column(Time, nullable=True)
    arrival_time = Column(Time, nullable=True)

    # Location/Route
    venue_name = Column(String(255), nullable=True)
    venue_address = Column(Text, nullable=True)
    venue_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)

    # For Transport - Route
    origin_city = Column(String(100), nullable=True, index=True)
    origin_terminal = Column(String(255), nullable=True)
    origin_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)

    destination_city = Column(String(100), nullable=True, index=True)
    destination_terminal = Column(String(255), nullable=True)
    destination_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)

    # Capacity
    total_capacity = Column(Integer, nullable=False)
    available_capacity = Column(Integer, nullable=False)

    # Organizer/Operator Info
    organizer_name = Column(String(255), nullable=True)
    organizer_contact = Column(String(100), nullable=True)

    # Media
    banner_image = Column(Text, nullable=True)
    images = Column(JSONB, default=list)

    # Additional Info
    terms_and_conditions = Column(Text, nullable=True)
    cancellation_policy = Column(Text, nullable=True)
    age_restriction = Column(Integer, nullable=True)  # Minimum age

    # Features (for events)
    features = Column(JSONB, default=list)  # ["parking", "vip_lounge", "food_included"]

    # Status
    status = Column(
        Enum(TicketStatusEnum),
        default=TicketStatusEnum.AVAILABLE,
        nullable=False,
        index=True
    )
    is_featured = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True, index=True)

    # Sales Period
    sales_start_date = Column(DateTime(timezone=True), nullable=True)
    sales_end_date = Column(DateTime(timezone=True), nullable=True)

    # Stats
    total_tickets_sold = Column(Integer, default=0)
    total_revenue = Column(Numeric(12, 2), default=0.00)
    average_rating = Column(Numeric(3, 2), default=0.00)
    total_reviews = Column(Integer, default=0)

    # Relationships
    business = relationship("Business")
    ticket_tiers = relationship(
        "TicketTier",
        back_populates="event",
        cascade="all, delete-orphan"
    )
    bookings = relationship(
        "TicketBooking",
        back_populates="event"
    )

    __table_args__ = (
        CheckConstraint('total_capacity > 0', name='positive_ticket_capacity'),
        CheckConstraint('available_capacity >= 0', name='non_negative_available'),
    )

    def __repr__(self):
        return f"<TicketEvent {self.name}>"


# ============================================
# TICKET TIER MODEL
# ============================================

class TicketTier(BaseModel):
    """Ticket pricing tiers (VIP, Regular, Economy, etc.)"""

    __tablename__ = "ticket_tiers"

    event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ticket_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Tier Info
    name = Column(String(100), nullable=False)  # VIP, Regular, Economy
    description = Column(Text, nullable=True)

    # Pricing
    price = Column(Numeric(10, 2), nullable=False)

    # Capacity
    total_quantity = Column(Integer, nullable=False)
    available_quantity = Column(Integer, nullable=False)

    # Seat Assignment
    has_seat_numbers = Column(Boolean, default=False)
    seat_section = Column(String(50), nullable=True)  # Section A, Section B
    seat_rows = Column(JSONB, default=list)  # ["1", "2", "3"] or seat map

    # Benefits
    benefits = Column(JSONB, default=list)  # ["Free drink", "VIP lounge access"]

    # Sales Control
    min_purchase = Column(Integer, default=1)
    max_purchase = Column(Integer, default=10)

    # Status
    is_active = Column(Boolean, default=True)

    # Display Order
    display_order = Column(Integer, default=0)

    # Relationships
    event = relationship("TicketEvent", back_populates="ticket_tiers")
    bookings = relationship(
        "TicketBooking",
        back_populates="tier"
    )

    __table_args__ = (
        CheckConstraint('price >= 0', name='non_negative_tier_price'),
        CheckConstraint('total_quantity > 0', name='positive_tier_quantity'),
        CheckConstraint('available_quantity >= 0', name='non_negative_tier_available'),
        CheckConstraint('min_purchase > 0', name='positive_min_purchase'),
        CheckConstraint('max_purchase >= min_purchase', name='valid_purchase_limits'),
    )

    def __repr__(self):
        return f"<TicketTier {self.name} - ₦{self.price}>"


# ============================================
# TICKET BOOKING MODEL
# ============================================

class TicketBooking(BaseModel):
    """Ticket purchases/bookings"""

    __tablename__ = "ticket_bookings"

    event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ticket_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    tier_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ticket_tiers.id", ondelete="CASCADE"),
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
    quantity = Column(Integer, nullable=False)

    # Pricing
    unit_price = Column(Numeric(10, 2), nullable=False)
    service_charge = Column(Numeric(10, 2), default=0.00)
    total_amount = Column(Numeric(10, 2), nullable=False)

    # Attendee Info
    attendee_name = Column(String(200), nullable=False)
    attendee_email = Column(String(255), nullable=False)
    attendee_phone = Column(String(20), nullable=False)

    # Additional Attendees (for group bookings)
    additional_attendees = Column(JSONB, default=list)
    # Example: [{"name": "John Doe", "email": "john@example.com", "phone": "+234..."}]

    # Seat Assignment (if applicable)
    assigned_seats = Column(JSONB, default=list)  # ["A1", "A2", "A3"]

    # Ticket Information
    booking_reference = Column(String(20), unique=True, nullable=False, index=True)
    qr_code_url = Column(Text, nullable=True)  # URL to QR code image

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

    # Check-in
    checked_in_at = Column(DateTime(timezone=True), nullable=True)
    check_in_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)

    # Cancellation
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    refund_amount = Column(Numeric(10, 2), nullable=True)

    # Special Requests
    special_requests = Column(Text, nullable=True)

    # Relationships
    event = relationship("TicketEvent", back_populates="bookings")
    tier = relationship("TicketTier", back_populates="bookings")
    customer = relationship("User", foreign_keys=[customer_id])

    __table_args__ = (
        CheckConstraint('quantity > 0', name='positive_booking_quantity'),
        CheckConstraint('total_amount >= 0', name='non_negative_booking_amount'),
    )

    def __repr__(self):
        return f"<TicketBooking {self.booking_reference}>"


# ============================================
# SEAT MAP MODEL (Optional - for venues with specific seating)
# ============================================

class SeatMap(BaseModel):
    """Detailed seat mapping for venues"""

    __tablename__ = "seat_maps"

    event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ticket_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    tier_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ticket_tiers.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Seat Info
    seat_number = Column(String(20), nullable=False)
    row_number = Column(String(10), nullable=False)
    section = Column(String(50), nullable=False)

    # Status
    is_available = Column(Boolean, default=True)
    is_blocked = Column(Boolean, default=False)  # For maintenance/reserved

    # Booking Reference (if booked)
    booking_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ticket_bookings.id", ondelete="SET NULL"),
        nullable=True
    )

    # Relationships
    event = relationship("TicketEvent")
    tier = relationship("TicketTier")
    booking = relationship("TicketBooking")

    __table_args__ = (
        UniqueConstraint('event_id', 'seat_number', name='unique_event_seat'),
    )

    def __repr__(self):
        return f"<SeatMap {self.section}-{self.seat_number}>"