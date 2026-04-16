"""
app/models/tickets_model.py

FIXES vs previous version:
  1. [HARD RULE] lga_name column DELETED from TicketEvent.
     Blueprint §4 / §2: "No LGA column in any database table."
     Events are discovered by venue GPS location + user radius (ST_DWithin).

  2. All LGA-related comments removed.

  3. platform_fee column added to TicketBooking — Blueprint §5.4:
     "Ticket purchases: ₦50 flat — from customer only."
     Stored per booking so it appears on the checkout summary and is reported
     in the admin financial dashboard (§11.3).

  4. Blueprint §6.7: "Live inventory — seat hold activates during checkout
     to prevent double-booking (Redis lock: seat_hold:{event_id}:{seat_id}
     TTL=600s during checkout)." — No DB change needed; documented here.

  5. Blueprint §6.7: "Digital ticket wallet: QR code in-app for scanning
     at venue." — qr_code_url kept on TicketBooking.
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

class EventTypeEnum(str, enum.Enum):
    EVENT     = "event"
    TRANSPORT = "transport"


class EventCategoryEnum(str, enum.Enum):
    CONCERT     = "concert"
    CONFERENCE  = "conference"
    SPORTS      = "sports"
    THEATER     = "theater"
    FESTIVAL    = "festival"
    WORKSHOP    = "workshop"
    EXHIBITION  = "exhibition"
    NETWORKING  = "networking"
    COMEDY      = "comedy"
    RELIGIOUS   = "religious"
    PARTY       = "party"
    OTHER       = "other"


class TransportTypeEnum(str, enum.Enum):
    BUS    = "bus"
    TRAIN  = "train"
    FLIGHT = "flight"
    FERRY  = "ferry"


class TicketStatusEnum(str, enum.Enum):
    AVAILABLE = "available"
    SOLD_OUT  = "sold_out"
    CANCELLED = "cancelled"


class BookingStatusEnum(str, enum.Enum):
    PENDING    = "pending"
    CONFIRMED  = "confirmed"
    CHECKED_IN = "checked_in"
    CANCELLED  = "cancelled"
    REFUNDED   = "refunded"
    NO_SHOW    = "no_show"


class PaymentStatusEnum(str, enum.Enum):
    PENDING  = "pending"
    PAID     = "paid"
    FAILED   = "failed"
    REFUNDED = "refunded"


# ─── Ticket Event ─────────────────────────────────────────────────────────────

class TicketEvent(BaseModel):
    """
    Events and transport schedules. Blueprint §6.7.

    Discovery: radius-based via venue_location (ST_DWithin).
    REMOVED: lga_name — Blueprint HARD RULE: no LGA column anywhere.
    """
    __tablename__ = "ticket_events"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # REMOVED: lga_name — Blueprint HARD RULE: no LGA column anywhere.
    # Discovery uses venue_location + ST_DWithin.

    event_type = Column(Enum(EventTypeEnum), nullable=False, index=True)

    name        = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    category    = Column(Enum(EventCategoryEnum), nullable=True)

    # Event dates
    event_date = Column(Date, nullable=True, index=True)
    start_time = Column(Time, nullable=True)
    end_time   = Column(Time, nullable=True)

    # Transport
    transport_type  = Column(Enum(TransportTypeEnum), nullable=True)
    departure_date  = Column(Date, nullable=True, index=True)
    departure_time  = Column(Time, nullable=True)
    arrival_time    = Column(Time, nullable=True)

    # Venue / Location (GPS — no LGA)
    venue_name    = Column(String(255), nullable=True)
    venue_address = Column(Text, nullable=True)
    venue_location = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=True
    )

    # Transport Route
    origin_city        = Column(String(100), nullable=True, index=True)
    origin_terminal    = Column(String(255), nullable=True)
    origin_location    = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=True)
    destination_city   = Column(String(100), nullable=True, index=True)
    destination_terminal = Column(String(255), nullable=True)
    destination_location = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=True)

    total_capacity     = Column(Integer, nullable=False)
    available_capacity = Column(Integer, nullable=False)

    organizer_name    = Column(String(255), nullable=True)
    organizer_contact = Column(String(100), nullable=True)

    banner_image = Column(Text, nullable=True)
    images       = Column(JSONB, default=list)

    terms_and_conditions  = Column(Text, nullable=True)
    cancellation_policy   = Column(Text, nullable=True)
    age_restriction       = Column(Integer, nullable=True)
    features              = Column(JSONB, default=list)

    status     = Column(Enum(TicketStatusEnum), default=TicketStatusEnum.AVAILABLE, nullable=False, index=True)
    is_featured = Column(Boolean, default=False)
    is_active   = Column(Boolean, default=True, nullable=False, index=True)

    sales_start_date = Column(DateTime(timezone=True), nullable=True)
    sales_end_date   = Column(DateTime(timezone=True), nullable=True)

    # Denormalised stats
    total_tickets_sold = Column(Integer, default=0)
    total_revenue      = Column(Numeric(12, 2), default=0.00)
    average_rating     = Column(Numeric(3, 2), default=0.00)
    total_reviews      = Column(Integer, default=0)

    business     = relationship("Business")
    ticket_tiers = relationship("TicketTier", back_populates="event", cascade="all, delete-orphan")
    bookings     = relationship("TicketBooking", back_populates="event")

    __table_args__ = (
        CheckConstraint("total_capacity > 0",     name="positive_ticket_capacity"),
        CheckConstraint("available_capacity >= 0", name="non_negative_available"),
    )

    def __repr__(self) -> str:
        return f"<TicketEvent {self.name}>"


# ─── Ticket Tier ──────────────────────────────────────────────────────────────

class TicketTier(BaseModel):
    """Pricing tiers — VIP, General Admission, Early Bird. Blueprint §6.7."""

    __tablename__ = "ticket_tiers"

    event_id = Column(UUID(as_uuid=True), ForeignKey("ticket_events.id", ondelete="CASCADE"), nullable=False, index=True)

    name        = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    price       = Column(Numeric(10, 2), nullable=False)

    total_quantity     = Column(Integer, nullable=False)
    available_quantity = Column(Integer, nullable=False)

    has_seat_numbers = Column(Boolean, default=False)
    seat_section     = Column(String(50), nullable=True)
    seat_rows        = Column(JSONB, default=list)

    benefits     = Column(JSONB, default=list)
    min_purchase = Column(Integer, default=1)
    max_purchase = Column(Integer, default=10)
    is_active    = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)

    event    = relationship("TicketEvent", back_populates="ticket_tiers")
    bookings = relationship("TicketBooking", back_populates="tier")

    __table_args__ = (
        CheckConstraint("price >= 0",             name="non_negative_tier_price"),
        CheckConstraint("total_quantity > 0",     name="positive_tier_quantity"),
        CheckConstraint("available_quantity >= 0", name="non_negative_tier_available"),
        CheckConstraint("min_purchase > 0",       name="positive_min_purchase"),
        CheckConstraint("max_purchase >= min_purchase", name="valid_purchase_limits"),
    )

    def __repr__(self) -> str:
        return f"<TicketTier {self.name} ₦{self.price}>"


# ─── Ticket Booking ───────────────────────────────────────────────────────────

class TicketBooking(BaseModel):
    """
    Ticket purchase record. Blueprint §6.7 / §5.4.

    Blueprint §5.4: ₦50 flat platform fee per ticket — from customer only.
    Blueprint §6.7: "Live inventory — seat hold during checkout via Redis
    lock: seat_hold:{event_id}:{seat_id} TTL=600s."
    """
    __tablename__ = "ticket_bookings"

    event_id    = Column(UUID(as_uuid=True), ForeignKey("ticket_events.id", ondelete="CASCADE"), nullable=False, index=True)
    tier_id     = Column(UUID(as_uuid=True), ForeignKey("ticket_tiers.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    quantity = Column(Integer, nullable=False)

    # Snapshot at time of purchase — never read from tier after booking
    # Blueprint §5.6: NUMERIC(12,2)
    unit_price     = Column(Numeric(12, 2), nullable=False)
    service_charge = Column(Numeric(12, 2), default=0.00)

    # Blueprint §5.4: ₦50 flat platform fee per ticket — from customer only
    platform_fee = Column(Numeric(12, 2), nullable=False, default=50.00)
    total_amount = Column(Numeric(12, 2), nullable=False)

    attendee_name  = Column(String(200), nullable=False)
    attendee_email = Column(String(255), nullable=False)
    attendee_phone = Column(String(20),  nullable=False)

    additional_attendees = Column(JSONB, default=list)
    assigned_seats       = Column(JSONB, default=list)

    booking_reference = Column(String(20), unique=True, nullable=False, index=True)

    # Blueprint §6.7: "Digital ticket wallet: QR code in-app for scanning at venue"
    qr_code_url = Column(Text, nullable=True)

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

    checked_in_at      = Column(DateTime(timezone=True), nullable=True)
    check_in_location  = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=True)

    cancelled_at        = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    refund_amount       = Column(Numeric(12, 2), nullable=True)

    special_requests = Column(Text, nullable=True)

    event    = relationship("TicketEvent", back_populates="bookings")
    tier     = relationship("TicketTier", back_populates="bookings")
    customer = relationship("User", foreign_keys=[customer_id])
    seat_maps = relationship("SeatMap", back_populates="booking")

    __table_args__ = (
        CheckConstraint("quantity > 0",       name="positive_booking_quantity"),
        CheckConstraint("total_amount >= 0",  name="non_negative_booking_amount"),
        CheckConstraint("platform_fee >= 0",  name="non_negative_ticket_platform_fee"),
    )

    def __repr__(self) -> str:
        return f"<TicketBooking {self.booking_reference}>"


# ─── Seat Map ─────────────────────────────────────────────────────────────────

class SeatMap(BaseModel):
    """Detailed seat mapping for venues with interactive seating charts."""

    __tablename__ = "seat_maps"

    event_id    = Column(UUID(as_uuid=True), ForeignKey("ticket_events.id", ondelete="CASCADE"), nullable=False, index=True)
    tier_id     = Column(UUID(as_uuid=True), ForeignKey("ticket_tiers.id", ondelete="CASCADE"), nullable=False, index=True)
    seat_number = Column(String(20), nullable=False)
    row_number  = Column(String(10), nullable=False)
    section     = Column(String(50), nullable=False)
    is_available = Column(Boolean, default=True)
    is_blocked   = Column(Boolean, default=False)
    booking_id   = Column(UUID(as_uuid=True), ForeignKey("ticket_bookings.id", ondelete="SET NULL"), nullable=True)

    event   = relationship("TicketEvent")
    tier    = relationship("TicketTier")
    booking = relationship("TicketBooking", back_populates="seat_maps")

    __table_args__ = (
        UniqueConstraint("event_id", "seat_number", name="unique_event_seat"),
    )

    def __repr__(self) -> str:
        return f"<SeatMap {self.section}-{self.seat_number}>"