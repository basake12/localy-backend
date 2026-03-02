from sqlalchemy import (
    Column, String, Boolean, Text, Integer,
    Numeric, ForeignKey, DateTime
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geography

from app.models.base_model import BaseModel


class Rider(BaseModel):
    """Delivery riders/drivers"""

    __tablename__ = "riders"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Personal Info
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    profile_picture = Column(Text, nullable=True)

    # Vehicle Info
    vehicle_type = Column(String(50), nullable=False)  # bike, car, van
    vehicle_plate_number = Column(String(20), nullable=True)
    vehicle_color = Column(String(50), nullable=True)
    vehicle_model = Column(String(100), nullable=True)

    # Documents
    drivers_license = Column(Text, nullable=True)
    vehicle_registration = Column(Text, nullable=True)

    # Service Areas
    current_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)
    service_radius_km = Column(Numeric(5, 2), default=10.00)

    # Subscription
    is_pro = Column(Boolean, default=False)
    pro_subscription_end = Column(DateTime(timezone=True), nullable=True)

    # Stats
    average_rating = Column(Numeric(3, 2), default=0.00, index=True)
    total_deliveries = Column(Integer, default=0)
    completion_rate = Column(Numeric(5, 2), default=0.00)

    # Status
    is_online = Column(Boolean, default=False, index=True)
    is_verified = Column(Boolean, default=False, index=True)
    is_active = Column(Boolean, default=True, index=True)

    # Relationships
    user = relationship("User", back_populates="rider")
    deliveries = relationship(
        "Delivery",
        back_populates="rider"
    )
    # Add to Rider model relationships


    def __repr__(self):
        return f"<Rider {self.first_name} {self.last_name} ({self.vehicle_type})>"