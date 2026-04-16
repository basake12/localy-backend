"""
app/models/delivery_model.py

FIXES vs previous version:
  1. [HARD RULE] local_government column DELETED from DeliveryZone.
     Blueprint §4 / §2: "No LGA column in any database table."
     Delivery zones are defined by GPS center_location + radius_km (ST_DWithin),
     not by LGA boundaries. state column kept — that's a geography field, not LGA.

  2. All other models unchanged — structurally sound.
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
    DateTime,
    CheckConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import enum

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class DeliveryTypeEnum(str, enum.Enum):
    PRODUCT      = "product"
    FOOD         = "food"
    PARCEL       = "parcel"
    DOCUMENT     = "document"
    PRESCRIPTION = "prescription"


class DeliveryStatusEnum(str, enum.Enum):
    PENDING    = "pending"
    ASSIGNED   = "assigned"
    PICKED_UP  = "picked_up"
    IN_TRANSIT = "in_transit"
    ARRIVED    = "arrived"
    DELIVERED  = "delivered"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


class PaymentStatusEnum(str, enum.Enum):
    PENDING       = "pending"
    PAID          = "paid"
    COD_PENDING   = "cod_pending"
    COD_COLLECTED = "cod_collected"
    FAILED        = "failed"


class VehicleTypeEnum(str, enum.Enum):
    BICYCLE    = "bicycle"
    MOTORCYCLE = "motorcycle"
    CAR        = "car"
    VAN        = "van"
    TRUCK      = "truck"


# ─── Delivery ─────────────────────────────────────────────────────────────────

class Delivery(BaseModel):
    """Main delivery / logistics record. Blueprint §5.3 / §6."""

    __tablename__ = "deliveries"

    order_id   = Column(UUID(as_uuid=True), nullable=True, index=True)
    order_type = Column(Enum(DeliveryTypeEnum), nullable=False, index=True)

    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Pickup
    pickup_address      = Column(Text, nullable=False)
    pickup_location     = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False)
    pickup_contact_name = Column(String(200), nullable=False)
    pickup_contact_phone = Column(String(20), nullable=False)
    pickup_instructions = Column(Text, nullable=True)

    # Dropoff
    dropoff_address       = Column(Text, nullable=False)
    dropoff_location      = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False)
    dropoff_contact_name  = Column(String(200), nullable=False)
    dropoff_contact_phone = Column(String(20), nullable=False)
    dropoff_instructions  = Column(Text, nullable=True)

    # Package
    package_description    = Column(Text, nullable=True)
    package_weight_kg      = Column(Numeric(6, 2), nullable=True)
    package_value          = Column(Numeric(10, 2), nullable=True)
    package_images         = Column(JSONB, default=list)
    requires_cold_storage  = Column(Boolean, default=False)
    is_fragile             = Column(Boolean, default=False)
    required_vehicle_type  = Column(Enum(VehicleTypeEnum), nullable=True)

    # Pricing — Blueprint §5.6: NUMERIC(12,2)
    base_fee            = Column(Numeric(12, 2), nullable=False)
    distance_fee        = Column(Numeric(12, 2), default=0.00)
    surge_multiplier    = Column(Numeric(3, 2), default=1.00)
    total_fee           = Column(Numeric(12, 2), nullable=False)

    estimated_distance_km = Column(Numeric(6, 2), nullable=True)
    actual_distance_km    = Column(Numeric(6, 2), nullable=True)

    estimated_pickup_time   = Column(DateTime(timezone=True), nullable=True)
    estimated_delivery_time = Column(DateTime(timezone=True), nullable=True)
    assigned_at             = Column(DateTime(timezone=True), nullable=True)
    picked_up_at            = Column(DateTime(timezone=True), nullable=True)
    delivered_at            = Column(DateTime(timezone=True), nullable=True)

    status = Column(Enum(DeliveryStatusEnum), default=DeliveryStatusEnum.PENDING, nullable=False, index=True)

    payment_method = Column(String(50), nullable=True)
    payment_status = Column(Enum(PaymentStatusEnum), default=PaymentStatusEnum.PENDING, nullable=False, index=True)
    cod_amount     = Column(Numeric(12, 2), default=0.00)

    rider_id = Column(UUID(as_uuid=True), ForeignKey("riders.id", ondelete="SET NULL"), nullable=True, index=True)

    tracking_code       = Column(String(20), unique=True, nullable=False, index=True)
    delivery_photo      = Column(Text, nullable=True)
    recipient_signature = Column(Text, nullable=True)
    delivery_notes      = Column(Text, nullable=True)

    rating  = Column(Integer, nullable=True)
    review  = Column(Text, nullable=True)

    cancelled_at        = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    cancelled_by        = Column(String(50), nullable=True)

    customer         = relationship("User", foreign_keys=[customer_id])
    rider            = relationship("Rider", back_populates="deliveries")
    tracking_updates = relationship(
        "DeliveryTracking",
        back_populates="delivery",
        cascade="all, delete-orphan",
        order_by="DeliveryTracking.created_at.desc()",
    )

    __table_args__ = (
        CheckConstraint("total_fee >= 0",                            name="non_negative_fee"),
        CheckConstraint("rating IS NULL OR (rating >= 1 AND rating <= 5)", name="valid_rating"),
    )

    def __repr__(self) -> str:
        return f"<Delivery {self.tracking_code} {self.status}>"


# ─── Delivery Tracking ────────────────────────────────────────────────────────

class DeliveryTracking(BaseModel):
    """Real-time delivery status + location updates."""

    __tablename__ = "delivery_tracking"

    delivery_id = Column(UUID(as_uuid=True), ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, index=True)
    status      = Column(Enum(DeliveryStatusEnum), nullable=False)
    location    = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True)
    address     = Column(Text, nullable=True)
    notes       = Column(Text, nullable=True)
    updated_by  = Column(String(50), nullable=True)
    meta_data   = Column(JSONB, default=dict)

    delivery = relationship("Delivery", back_populates="tracking_updates")

    def __repr__(self) -> str:
        return f"<DeliveryTracking delivery={self.delivery_id} status={self.status}>"


# ─── Rider Earnings ───────────────────────────────────────────────────────────

class RiderEarnings(BaseModel):
    """Track rider earnings per delivery."""

    __tablename__ = "rider_earnings"

    rider_id    = Column(UUID(as_uuid=True), ForeignKey("riders.id", ondelete="CASCADE"), nullable=False, index=True)
    delivery_id = Column(UUID(as_uuid=True), ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    base_earning      = Column(Numeric(12, 2), nullable=False)
    distance_bonus    = Column(Numeric(12, 2), default=0.00)
    tip               = Column(Numeric(12, 2), default=0.00)
    peak_hour_bonus   = Column(Numeric(12, 2), default=0.00)
    total_earning     = Column(Numeric(12, 2), nullable=False)

    platform_commission = Column(Numeric(12, 2), default=0.00)
    net_earning         = Column(Numeric(12, 2), nullable=False)

    cod_collected = Column(Numeric(12, 2), default=0.00)
    cod_remitted  = Column(Boolean, default=False)

    is_paid         = Column(Boolean, default=False)
    paid_at         = Column(DateTime(timezone=True), nullable=True)
    payout_batch_id = Column(String(100), nullable=True)

    rider    = relationship("Rider")
    delivery = relationship("Delivery")

    __table_args__ = (
        CheckConstraint("total_earning >= 0", name="non_negative_earning"),
    )

    def __repr__(self) -> str:
        return f"<RiderEarnings rider={self.rider_id} ₦{self.net_earning}>"


# ─── Delivery Zone ────────────────────────────────────────────────────────────

class DeliveryZone(BaseModel):
    """
    Delivery zone definitions with GPS-based boundaries.

    Blueprint §4.1: "Delivery zones are per-business, separate from the
    discovery radius." Zones defined by GPS center + radius, not LGA.

    REMOVED: local_government — Blueprint HARD RULE: no LGA column anywhere.
    """
    __tablename__ = "delivery_zones"

    name   = Column(String(100), nullable=False, unique=True)
    # state is a geography label, NOT an LGA — acceptable
    state  = Column(String(100), nullable=False, index=True)

    # Zone center for distance calculations (GPS)
    center_location = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False)
    radius_km       = Column(Numeric(6, 2), nullable=False)

    base_fee    = Column(Numeric(12, 2), nullable=False)
    per_km_fee  = Column(Numeric(12, 2), nullable=False)
    peak_hours  = Column(JSONB, default=list)
    is_active   = Column(Boolean, default=True)

    def __repr__(self) -> str:
        return f"<DeliveryZone {self.name}>"


# ─── Rider Shift ──────────────────────────────────────────────────────────────

class RiderShift(BaseModel):
    """Track rider work shifts."""

    __tablename__ = "rider_shifts"

    rider_id    = Column(UUID(as_uuid=True), ForeignKey("riders.id", ondelete="CASCADE"), nullable=False, index=True)
    shift_start = Column(DateTime(timezone=True), nullable=False)
    shift_end   = Column(DateTime(timezone=True), nullable=True)

    start_location = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True)
    end_location   = Column(Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True)

    total_deliveries  = Column(Integer, default=0)
    total_distance_km = Column(Numeric(10, 2), default=0.00)
    total_earnings    = Column(Numeric(12, 2), default=0.00)

    rider = relationship("Rider")

    def __repr__(self) -> str:
        return f"<RiderShift rider={self.rider_id} start={self.shift_start}>"