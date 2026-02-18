from sqlalchemy import (
    Column, String, Boolean, Enum, Text, Integer,
    Numeric, ForeignKey, Time, Date, DateTime, CheckConstraint, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from datetime import time as dt_time, date
from app.models.base import BaseModel

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
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class PaymentStatusEnum(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
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

    # Hotel Details
    star_rating = Column(Integer, nullable=True)
    total_rooms = Column(Integer, nullable=False)

    # Check-in/out times
    check_in_time = Column(Time, default=dt_time(14, 0))  # 2:00 PM
    check_out_time = Column(Time, default=dt_time(11, 0))  # 11:00 AM

    # Facilities (stored as JSONB array)
    facilities = Column(JSONB, default=list)  # ['pool', 'gym', 'spa', 'restaurant', 'parking', 'wifi']

    # Policies
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

    # Basic Info
    name = Column(String(100), nullable=False)  # Single, Double, Suite
    description = Column(Text, nullable=True)

    # Configuration
    bed_configuration = Column(String(100), nullable=True)  # "1 King Bed", "2 Queen Beds"
    max_occupancy = Column(Integer, nullable=False)
    size_sqm = Column(Numeric(6, 2), nullable=True)
    floor_range = Column(String(50), nullable=True)  # "1-5", "10-15"
    view_type = Column(String(50), nullable=True)  # sea, city, garden, mountain

    # Amenities (stored as JSONB array)
    amenities = Column(JSONB, default=list)  # ['tv', 'minibar', 'safe', 'balcony']

    # Pricing
    base_price_per_night = Column(Numeric(10, 2), nullable=False)

    # Media
    images = Column(JSONB, default=list)  # Array of image URLs

    # Inventory
    total_rooms = Column(Integer, nullable=False)

    # Relationships
    hotel = relationship("Hotel", back_populates="room_types")
    rooms = relationship(
        "Room",
        back_populates="room_type",
        cascade="all, delete-orphan"
    )
    bookings = relationship(
        "HotelBooking",
        back_populates="room_type"
    )

    __table_args__ = (
        CheckConstraint('max_occupancy > 0', name='positive_max_occupancy'),
        CheckConstraint('base_price_per_night > 0', name='positive_price'),
        CheckConstraint('total_rooms > 0', name='positive_total_rooms'),
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

    room_number = Column(String(20), nullable=False)
    floor = Column(Integer, nullable=True)
    status = Column(
        Enum(RoomStatusEnum),
        default=RoomStatusEnum.VACANT,
        nullable=False,
        index=True
    )

    # Housekeeping
    last_cleaned = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    room_type = relationship("RoomType", back_populates="rooms")

    __table_args__ = (
        UniqueConstraint('room_type_id', 'room_number', name='unique_room_number_per_type'),
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

    # Booking Details
    check_in_date = Column(Date, nullable=False, index=True)
    check_out_date = Column(Date, nullable=False, index=True)
    number_of_rooms = Column(Integer, default=1, nullable=False)
    number_of_guests = Column(Integer, nullable=False)

    # Pricing
    base_price = Column(Numeric(10, 2), nullable=False)
    add_ons_price = Column(Numeric(10, 2), default=0.00)
    total_price = Column(Numeric(10, 2), nullable=False)

    # Add-ons (stored as JSONB)
    add_ons = Column(JSONB, default=list)  # [{'type': 'breakfast', 'price': 5000}, ...]
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

    # Digital Check-in
    check_in_form_completed = Column(Boolean, default=False)
    id_uploaded = Column(Boolean, default=False)
    id_document_url = Column(Text, nullable=True)

    # Actual check-in/out times
    actual_check_in = Column(DateTime(timezone=True), nullable=True)
    actual_check_out = Column(DateTime(timezone=True), nullable=True)

    # Cancellation
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    # Relationships
    hotel = relationship("Hotel", back_populates="bookings")
    room_type = relationship("RoomType", back_populates="bookings")
    customer = relationship("User", foreign_keys=[customer_id])
    services = relationship(
        "HotelService",
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
# HOTEL SERVICE MODEL
# ============================================

class HotelService(BaseModel):
    """In-stay service requests"""

    __tablename__ = "hotel_services"

    booking_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hotel_bookings.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    service_type = Column(String(100), nullable=False)  # room_service, housekeeping, maintenance, wake_up_call
    description = Column(Text, nullable=True)

    status = Column(
        Enum(ServiceStatusEnum),
        default=ServiceStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    requested_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Staff assignment
    assigned_to = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    booking = relationship("HotelBooking", back_populates="services")

    def __repr__(self):
        return f"<HotelService {self.service_type} - {self.status}>"