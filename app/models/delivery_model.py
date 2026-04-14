from sqlalchemy import (
    Column, String, Boolean, Enum, Text, Integer,
    Numeric, ForeignKey, DateTime, CheckConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class DeliveryTypeEnum(str, enum.Enum):
    PRODUCT = "product"  # E-commerce delivery
    FOOD = "food"  # Restaurant delivery
    PARCEL = "parcel"  # General parcel/package
    DOCUMENT = "document"  # Document delivery
    PRESCRIPTION = "prescription"  # Pharmacy delivery


class DeliveryStatusEnum(str, enum.Enum):
    PENDING = "pending"  # Waiting for rider assignment
    ASSIGNED = "assigned"  # Rider assigned
    PICKED_UP = "picked_up"  # Rider picked up package
    IN_TRANSIT = "in_transit"  # On the way to destination
    ARRIVED = "arrived"  # Arrived at destination
    DELIVERED = "delivered"  # Successfully delivered
    FAILED = "failed"  # Delivery failed
    CANCELLED = "cancelled"  # Cancelled


class PaymentStatusEnum(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    COD_PENDING = "cod_pending"  # Cash on delivery pending
    COD_COLLECTED = "cod_collected"  # Cash collected by rider
    FAILED = "failed"


class VehicleTypeEnum(str, enum.Enum):
    BICYCLE = "bicycle"
    MOTORCYCLE = "motorcycle"
    CAR = "car"
    VAN = "van"
    TRUCK = "truck"


# ============================================
# DELIVERY MODEL
# ============================================

class Delivery(BaseModel):
    """Main delivery/logistics model"""

    __tablename__ = "deliveries"

    # Reference to order/booking
    order_id = Column(UUID(as_uuid=True), nullable=True, index=True)  # Links to product_orders
    order_type = Column(
        Enum(DeliveryTypeEnum),
        nullable=False,
        index=True
    )

    # Customer
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Pickup Details
    pickup_address = Column(Text, nullable=False)
    pickup_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=False), nullable=False)
    pickup_contact_name = Column(String(200), nullable=False)
    pickup_contact_phone = Column(String(20), nullable=False)
    pickup_instructions = Column(Text, nullable=True)

    # Dropoff Details
    dropoff_address = Column(Text, nullable=False)
    dropoff_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=False), nullable=False)
    dropoff_contact_name = Column(String(200), nullable=False)
    dropoff_contact_phone = Column(String(20), nullable=False)
    dropoff_instructions = Column(Text, nullable=True)

    # Package Details
    package_description = Column(Text, nullable=True)
    package_weight_kg = Column(Numeric(6, 2), nullable=True)
    package_value = Column(Numeric(10, 2), nullable=True)
    package_images = Column(JSONB, default=list)  # Photos of package

    # Special Requirements
    requires_cold_storage = Column(Boolean, default=False)
    is_fragile = Column(Boolean, default=False)
    required_vehicle_type = Column(Enum(VehicleTypeEnum), nullable=True)

    # Pricing
    base_fee = Column(Numeric(10, 2), nullable=False)
    distance_fee = Column(Numeric(10, 2), default=0.00)
    surge_multiplier = Column(Numeric(3, 2), default=1.00)  # Peak hour multiplier
    total_fee = Column(Numeric(10, 2), nullable=False)

    # Distance
    estimated_distance_km = Column(Numeric(6, 2), nullable=True)
    actual_distance_km = Column(Numeric(6, 2), nullable=True)

    # Time Estimates
    estimated_pickup_time = Column(DateTime(timezone=True), nullable=True)
    estimated_delivery_time = Column(DateTime(timezone=True), nullable=True)

    # Actual Times
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    picked_up_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    # Status
    status = Column(
        Enum(DeliveryStatusEnum),
        default=DeliveryStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    # Payment
    payment_method = Column(String(50), nullable=True)  # wallet, cod, business_account
    payment_status = Column(
        Enum(PaymentStatusEnum),
        default=PaymentStatusEnum.PENDING,
        nullable=False,
        index=True
    )
    cod_amount = Column(Numeric(10, 2), default=0.00)  # Cash on delivery amount

    # Rider Assignment
    rider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Tracking
    tracking_code = Column(String(20), unique=True, nullable=False, index=True)

    # Proof of Delivery
    delivery_photo = Column(Text, nullable=True)  # Photo proof
    recipient_signature = Column(Text, nullable=True)  # Digital signature
    delivery_notes = Column(Text, nullable=True)

    # Rating
    rating = Column(Integer, nullable=True)  # 1-5 stars
    review = Column(Text, nullable=True)

    # Cancellation
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    cancelled_by = Column(String(50), nullable=True)  # customer, rider, system

    # Relationships
    customer = relationship("User", foreign_keys=[customer_id])
    rider = relationship("Rider", back_populates="deliveries")
    tracking_updates = relationship(
        "DeliveryTracking",
        back_populates="delivery",
        cascade="all, delete-orphan",
        order_by="DeliveryTracking.created_at.desc()"
    )

    __table_args__ = (
        CheckConstraint('total_fee >= 0', name='non_negative_fee'),
        CheckConstraint('rating >= 1 AND rating <= 5', name='valid_rating'),
    )

    def __repr__(self):
        return f"<Delivery {self.tracking_code} - {self.status}>"


# ============================================
# DELIVERY TRACKING MODEL
# ============================================

class DeliveryTracking(BaseModel):
    """Real-time delivery tracking updates"""

    __tablename__ = "delivery_tracking"

    delivery_id = Column(
        UUID(as_uuid=True),
        ForeignKey("deliveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Status Update
    status = Column(
        Enum(DeliveryStatusEnum),
        nullable=False
    )

    # Location at time of update
    location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=False), nullable=True)
    address = Column(Text, nullable=True)

    # Update Details
    notes = Column(Text, nullable=True)
    updated_by = Column(String(50), nullable=True)  # rider, system, admin

    # Metadata
    meta_data = Column(JSONB, default=dict)  # Additional tracking info

    # Relationships
    delivery = relationship("Delivery", back_populates="tracking_updates")

    def __repr__(self):
        return f"<DeliveryTracking {self.delivery_id} - {self.status}>"


# ============================================
# RIDER EARNINGS MODEL
# ============================================

class RiderEarnings(BaseModel):
    """Track rider earnings per delivery"""

    __tablename__ = "rider_earnings"

    rider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    delivery_id = Column(
        UUID(as_uuid=True),
        ForeignKey("deliveries.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True
    )

    # Earnings Breakdown
    base_earning = Column(Numeric(10, 2), nullable=False)  # Base delivery fee
    distance_bonus = Column(Numeric(10, 2), default=0.00)
    tip = Column(Numeric(10, 2), default=0.00)
    peak_hour_bonus = Column(Numeric(10, 2), default=0.00)
    total_earning = Column(Numeric(10, 2), nullable=False)

    # Platform Commission
    platform_commission = Column(Numeric(10, 2), default=0.00)  # e.g., 20% of total
    net_earning = Column(Numeric(10, 2), nullable=False)  # After commission

    # COD Handling
    cod_collected = Column(Numeric(10, 2), default=0.00)
    cod_remitted = Column(Boolean, default=False)

    # Payout Status
    is_paid = Column(Boolean, default=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    payout_batch_id = Column(String(100), nullable=True)  # For batch payments

    # Relationships
    rider = relationship("Rider")
    delivery = relationship("Delivery")

    __table_args__ = (
        CheckConstraint('total_earning >= 0', name='non_negative_earning'),
    )

    def __repr__(self):
        return f"<RiderEarnings {self.rider_id} - ₦{self.net_earning}>"


# ============================================
# DELIVERY ZONE MODEL
# ============================================

class DeliveryZone(BaseModel):
    """Delivery zones with pricing"""

    __tablename__ = "delivery_zones"

    name = Column(String(100), nullable=False, unique=True)
    state = Column(String(100), nullable=False, index=True)
    local_government = Column(String(100), nullable=False, index=True)

    # Zone Center (for distance calculations)
    center_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=False), nullable=False)
    radius_km = Column(Numeric(6, 2), nullable=False)

    # Pricing
    base_fee = Column(Numeric(10, 2), nullable=False)
    per_km_fee = Column(Numeric(10, 2), nullable=False)

    # Peak Hours (stored as JSONB)
    peak_hours = Column(JSONB, default=list)
    # Example: [{"day": 1, "start": "17:00", "end": "20:00", "multiplier": 1.5}]

    # Availability
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f"<DeliveryZone {self.name}>"


# ============================================
# RIDER SHIFT MODEL
# ============================================

class RiderShift(BaseModel):
    """Track rider work shifts"""

    __tablename__ = "rider_shifts"

    rider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Shift Times
    shift_start = Column(DateTime(timezone=True), nullable=False)
    shift_end = Column(DateTime(timezone=True), nullable=True)

    # Start/End Locations
    start_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=False), nullable=True)
    end_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=False), nullable=True)

    # Shift Stats
    total_deliveries = Column(Integer, default=0)
    total_distance_km = Column(Numeric(10, 2), default=0.00)
    total_earnings = Column(Numeric(10, 2), default=0.00)

    # Relationships
    rider = relationship("Rider")

    def __repr__(self):
        return f"<RiderShift {self.rider_id} - {self.shift_start}>"