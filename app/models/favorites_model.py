from sqlalchemy import Column, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID

from app.models.base_model import BaseModel


# ============================================
# FAVORITES MODEL
# ============================================

class Favorite(BaseModel):
    """
    User favorites/wishlist across all categories.
    Polymorphic - can favorite hotels, products, restaurants, etc.
    """

    __tablename__ = "favorites"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Polymorphic favoritable (hotel, product, service, restaurant, etc.)
    favoritable_type = Column(String(50), nullable=False, index=True)  # "hotel", "product", "restaurant"
    favoritable_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    # Relationships
    user = relationship("User")

    __table_args__ = (
        UniqueConstraint('user_id', 'favoritable_type', 'favoritable_id', name='unique_favorite'),
    )