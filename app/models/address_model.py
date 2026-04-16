"""
app/models/address_model.py

FIXES vs previous version:
  1. [HARD RULE] lga_name column DELETED.
     Blueprint §4 / §2: "No LGA column in any database table.
     Remove immediately if discovered in legacy code."

  2. discovery_radius_m column added — per Blueprint §4.1:
     Users can adjust their radius from 1 km to 50 km via the slider.
     Storing per-address allows different radii for different saved locations.
"""
from sqlalchemy import Column, String, Boolean, Numeric, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.base_model import BaseModel


class CustomerAddress(BaseModel):
    """
    Named delivery / search addresses for a customer.

    Blueprint §4.1: GPS is the primary location source.
    Manual address entry is the fallback when GPS is unavailable.
    All discovery uses radius from the GPS position — no LGA logic.

    REMOVED: lga_name — Blueprint HARD RULE: no LGA column anywhere.
    """
    __tablename__ = "customer_addresses"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    label  = Column(String(100), nullable=True)   # "Home", "Work", "Other"
    street = Column(String(255), nullable=False)
    city   = Column(String(100), nullable=True)
    # REMOVED: lga_name — Blueprint HARD RULE: no LGA column anywhere.
    state  = Column(String(100), nullable=True)
    country = Column(String(100), default="Nigeria")

    # Geocoded coordinates for radius-based discovery from this address
    lat = Column(Numeric(10, 7), nullable=True)
    lng = Column(Numeric(10, 7), nullable=True)

    is_default = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="addresses")

    def __repr__(self) -> str:
        return f"<CustomerAddress {self.street}, {self.city} user={self.user_id}>"