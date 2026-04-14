"""
app/models/address_model.py

CustomerAddress model — stores named delivery addresses for a customer.
Fields match CustomerAddressOut schema and Flutter's CustomerAddress.fromJson().
"""
from sqlalchemy import Column, String, Boolean, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.base_model import BaseModel


class CustomerAddress(BaseModel):
    __tablename__ = "customer_addresses"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    label      = Column(String(100), nullable=True)   # e.g. "Home", "Work"
    street     = Column(String(255), nullable=False)
    city       = Column(String(100), nullable=True)
    lga_name   = Column(String(100), nullable=True)
    lat        = Column(Numeric(10, 7), nullable=True)
    lng        = Column(Numeric(10, 7), nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)

    # Relationship back to User (optional — add to User model if needed)
    user = relationship("User", back_populates="addresses")

    def __repr__(self):
        return f"<CustomerAddress {self.street}, {self.city} (user={self.user_id})>"