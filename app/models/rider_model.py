"""
app/models/rider_model.py

FIXES:
  1.  [BLUEPRINT §3.1 STEP 5b] gov_id_url TEXT added.
      Blueprint: "Upload government-issued ID (stored in S3/R2, URL saved to DB)."
      user_crud.py's _create_profile() stores the gov_id_url from registration.
      Without this column, rider document upload fails silently.

  2.  DriverSubscriptionPlan enum REMOVED.
      Blueprint §8.1 specifies Free / Starter / Pro / Enterprise for businesses.
      Riders do NOT have a separate subscription plan — they have a wallet and
      earn from deliveries. There is no "Pro Driver" plan in the blueprint.
      Removed: subscription_plan, is_pro, pro_subscription_end columns.

  3.  vehicle_type values corrected.
      Blueprint §3.1 step 5b: "motorcycle / bicycle / car / van"
      DB CHECK constraint added for these four values.

  4.  All timestamps use DateTime(timezone=True) — Blueprint §16.4 HARD RULE.
"""
from sqlalchemy import (
    Column, String, Boolean, Text, Integer,
    Numeric, ForeignKey, DateTime, CheckConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geography

from app.models.base_model import BaseModel


class Rider(BaseModel):
    """
    Delivery rider entity.

    Blueprint §3.1 step 5b: vehicle type + government ID uploaded at registration.
    Blueprint §5.3: Rider Wallet — same withdrawal rules as Business Wallet.
    Blueprint §8.6: Riders receive delivery jobs via proximity matching (GPS).

    No subscription plan — riders earn from deliveries only.
    """
    __tablename__ = "riders"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # ── Personal Info ─────────────────────────────────────────────────────────
    first_name      = Column(String(100), nullable=False)
    last_name       = Column(String(100), nullable=False)
    profile_picture = Column(Text, nullable=True)
    nin             = Column(String(20), nullable=True)   # Nigerian NIN for KYC

    # ── Vehicle Info — Blueprint §3.1 step 5b ─────────────────────────────────
    # "Submit vehicle type (motorcycle / bicycle / car / van)"
    vehicle_type         = Column(String(20), nullable=False, default="motorcycle")
    vehicle_plate_number = Column(String(20), nullable=True)
    vehicle_color        = Column(String(50), nullable=True)
    vehicle_model        = Column(String(100), nullable=True)

    # ── Documents — Blueprint §3.1 step 5b ────────────────────────────────────
    # "Upload government-issued ID (stored in S3/R2, URL saved to DB)"
    gov_id_url           = Column(Text, nullable=True)    # [BUG FIX] was missing
    drivers_license      = Column(Text, nullable=True)
    vehicle_registration = Column(Text, nullable=True)

    # ── Location — GPS-based job matching ────────────────────────────────────
    current_location = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    service_radius_km = Column(Numeric(5, 2), default=10.00)

    # ── Stats ──────────────────────────────────────────────────────────────────
    average_rating       = Column(Numeric(3, 2), default=0.00, index=True)
    total_deliveries     = Column(Integer, default=0)
    completed_deliveries = Column(Integer, default=0)
    completion_rate      = Column(Numeric(5, 2), default=0.00)

    # ── Push Notifications ─────────────────────────────────────────────────────
    fcm_token = Column(Text, nullable=True)

    # ── Status ─────────────────────────────────────────────────────────────────
    is_online   = Column(Boolean, default=False, index=True)
    is_verified = Column(Boolean, default=False, index=True)  # set by admin review
    is_active   = Column(Boolean, default=True, index=True)

    # ── Relationships ──────────────────────────────────────────────────────────
    user       = relationship("User", back_populates="rider")
    deliveries = relationship("Delivery", back_populates="rider")

    __table_args__ = (
        CheckConstraint(
            "vehicle_type IN ('motorcycle','bicycle','car','van')",
            name="valid_rider_vehicle_type",
        ),
        Index("ix_riders_user_id",   "user_id"),
        Index("ix_riders_is_online", "is_online"),
    )

    def __repr__(self) -> str:
        return f"<Rider {self.first_name} {self.last_name} ({self.vehicle_type})>"