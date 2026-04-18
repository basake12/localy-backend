from sqlalchemy import (
    Column, String, Boolean, Enum, Text, Integer,
    Numeric, ForeignKey, Time, Date, DateTime, CheckConstraint, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from decimal import Decimal
from datetime import time as dt_time
from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class RoomStatusEnum(str, enum.Enum):
    VACANT = "vacant"
    OCCUPIED = "occupied"
    DIRTY = "dirty"
    BLOCKED = "blocked"
    OUT_OF_ORDER = "out_of_order"


class BookingStatusEnum(str, enum.Enum):
    # FIX (original): Values changed from UPPERCASE to lowercase to match every
    # other module.  All other models (services, tickets, products, food) use
    # lowercase enum values.  Uppercase values made cross-module status
    # comparisons and admin analytics impossible.
    # MIGRATION REQUIRED — run spatial_and_enum_migration.sql before deploying.
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class PaymentStatusEnum(str, enum.Enum):
    # FIX (original): Same lowercase fix as BookingStatusEnum above.
    PENDING = "pending"
    PAID = "paid"
    COD_PENDING = "cod_pending"
    COD_COLLECTED = "cod_collected"
    FAILED = "failed"
    REFUNDED = "refunded"


class ServiceStatusEnum(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# ============================================
# HOTEL MODEL
# ============================================

class Hotel(BaseModel):
    """Hotel-specific business data"""

    __tablename__ = "hotels"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    star_rating = Column(Integer, nullable=True)
    total_rooms = Column(Integer, nullable=False)

    check_in_time = Column(Time, default=dt_time(14, 0))   # 2:00 PM
    check_out_time = Column(Time, default=dt_time(11, 0))  # 11:00 AM

    # Facilities stored as JSONB array e.g. ['pool', 'gym', 'spa', 'wifi']
    facilities = Column(JSONB, default=list)

    policies = Column(Text, nullable=True)
    cancellation_policy = Column(Text, nullable=True)

    # Relationships
    business = relationship("Business", back_populates="hotel")
    room_types = relationship(
        "RoomType",
        back_populates="hotel",
        cascade="all, delete-orphan"
    )
    bookings = relationship(
        "HotelBooking",
        back_populates="hotel",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint('star_rating >= 1 AND star_rating <= 5', name='valid_star_rating'),
        CheckConstraint('total_rooms > 0', name='positive_total_rooms'),
    )

    def __repr__(self):
        return f"<Hotel {self.business_id} - {self.star_rating}★>"


# ============================================
# ROOM TYPE MODEL
# ============================================

class RoomType(BaseModel):
    """Different room categories in a hotel"""

    __tablename__ = "room_types"

    hotel_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hotels.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    bed_configuration = Column(String(100), nullable=True)
    max_occupancy = Column(Integer, nullable=False)
    size_sqm = Column(Numeric(6, 2), nullable=True)
    floor_range = Column(String(50), nullable=True)
    view_type = Column(String(50), nullable=True)

    # Amenities as JSONB array e.g. ['tv', 'minibar', 'safe', 'balcony']
    amenities = Column(JSONB, default=list)

    base_price_per_night = Column(Numeric(10, 2), nullable=False)
    images = Column(JSONB, default=list)
    total_rooms = Column(Integer, nullable=False)

    # Relationships
    hotel = relationship("Hotel", back_populates="room_types")
    rooms = relationship(
        "Room",
        back_populates="room_type",
        cascade="all, delete-orphan"
    )
    bookings = relationship("HotelBooking", back_populates="room_type")

    __table_args__ = (
        CheckConstraint('max_occupancy > 0', name='positive_max_occupancy'),
        CheckConstraint('base_price_per_night > 0', name='positive_price'),
        CheckConstraint('total_rooms > 0', name='positive_room_type_total'),
    )

    def __repr__(self):
        return f"<RoomType {self.name} - {self.hotel_id}>"


# ============================================
# ROOM MODEL
# ============================================

class Room(BaseModel):
    """Individual room instances"""

    __tablename__ = "rooms"

    room_type_id = Column(
        UUID(as_uuid=True),
        ForeignKey("room_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    # hotel_id stored directly so room number uniqueness is enforced at hotel
    # scope rather than room_type scope — "101" must be unique across the whole
    # hotel, not just within a room type.
    hotel_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hotels.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    room_number = Column(String(20), nullable=False)
    floor = Column(Integer, nullable=True)
    status = Column(
        Enum(RoomStatusEnum),
        default=RoomStatusEnum.VACANT,
        nullable=False,
        index=True
    )
    last_cleaned = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    room_type = relationship("RoomType", back_populates="rooms")

    __table_args__ = (
        # Uniqueness scoped to hotel, not room_type — a room number identifies a
        # physical room in a building, not a category.
        UniqueConstraint('hotel_id', 'room_number', name='unique_room_number_per_hotel'),
    )

    def __repr__(self):
        return f"<Room {self.room_number} - {self.status}>"


# ============================================
# HOTEL BOOKING MODEL
# ============================================

class HotelBooking(BaseModel):
    """Hotel reservations"""

    __tablename__ = "hotel_bookings"

    hotel_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hotels.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    room_type_id = Column(
        UUID(as_uuid=True),
        ForeignKey("room_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    check_in_date = Column(Date, nullable=False, index=True)
    check_out_date = Column(Date, nullable=False, index=True)
    number_of_rooms = Column(Integer, default=1, nullable=False)
    number_of_guests = Column(Integer, nullable=False)

    base_price = Column(Numeric(10, 2), nullable=False)
    # FIX: Use Decimal("0.00") as the Python-side default so the column default
    # is always a Decimal — not a float — matching the NUMERIC(10,2) column type.
    add_ons_price = Column(Numeric(10, 2), default=Decimal("0.00"))
    total_price = Column(Numeric(10, 2), nullable=False)

    # Add-ons as JSONB e.g. [{'type': 'breakfast', 'quantity': 2, 'price': 5000}]
    add_ons = Column(JSONB, default=list)
    special_requests = Column(Text, nullable=True)

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

    check_in_form_completed = Column(Boolean, default=False)
    id_uploaded = Column(Boolean, default=False)
    id_document_url = Column(Text, nullable=True)

    actual_check_in = Column(DateTime(timezone=True), nullable=True)
    actual_check_out = Column(DateTime(timezone=True), nullable=True)

    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    # Relationships
    hotel = relationship("Hotel", back_populates="bookings")
    room_type = relationship("RoomType", back_populates="bookings")
    customer = relationship("User", foreign_keys=[customer_id])
    services = relationship(
        "HotelInStayRequest",
        back_populates="booking",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint('check_out_date > check_in_date', name='valid_date_range'),
        CheckConstraint('number_of_rooms > 0', name='positive_rooms'),
        CheckConstraint('number_of_guests > 0', name='positive_guests'),
        CheckConstraint('total_price >= 0', name='non_negative_price'),
    )

    def __repr__(self):
        return f"<HotelBooking {self.id} - {self.status}>"


# ============================================
# IN-STAY SERVICE REQUEST MODEL
# ============================================

# BUG-13 FIX: Renamed from HotelService → HotelInStayRequest.
#
# Root cause: the original name "HotelService" directly collided with the
# HotelService orchestration class in hotel_service.py.  The router worked
# around this with `from app.models.hotels_model import HotelService as
# HotelServiceModel`, but that alias is a symptom of the naming conflict, not
# a fix.  Renaming the model to HotelInStayRequest (which accurately describes
# what it is — a guest request during a stay) eliminates the ambiguity and
# removes the need for the import alias.
#
# MIGRATION: ALTER TABLE hotel_services RENAME TO hotel_in_stay_requests;
# (or keep the __tablename__ = "hotel_services" if the DB table name is fine —
# the tablename below is preserved so no SQL migration is required for the table
# itself, only the Python symbol changes.)

class HotelInStayRequest(BaseModel):
    """In-stay service requests (room service, housekeeping, maintenance, etc.)"""

    __tablename__ = "hotel_services"  # preserved — no DB migration needed

    booking_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hotel_bookings.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    service_type = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)

    status = Column(
        Enum(ServiceStatusEnum),
        default=ServiceStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    requested_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    assigned_to = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)

    booking = relationship("HotelBooking", back_populates="services")

    def __repr__(self):
        return f"<HotelInStayRequest {self.service_type} - {self.status}>"