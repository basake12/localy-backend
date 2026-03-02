from sqlalchemy import (
    Column, String, Boolean, Enum, Text, Integer,
    Numeric, ForeignKey, Time, CheckConstraint, DateTime, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geography
from geoalchemy2.shape import to_shape
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class BusinessCategoryEnum(str, enum.Enum):
    LODGES = "lodges"
    FOOD = "food"
    SERVICES = "services"
    PRODUCTS = "products"
    HEALTH = "health"
    PROPERTY_AGENT = "property_agent"
    TICKET_SALES = "ticket_sales"


class VerificationBadgeEnum(str, enum.Enum):
    NONE = "none"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# ============================================
# BUSINESS MODEL
# ============================================

class Business(BaseModel):
    """Business accounts"""

    __tablename__ = "businesses"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Basic Info
    business_name = Column(String(255), nullable=False, index=True)
    category = Column(Enum(BusinessCategoryEnum), nullable=False, index=True)
    subcategory = Column(String(100), nullable=True, index=True)
    description = Column(Text, nullable=True)
    logo = Column(Text, nullable=True)
    banner_image = Column(Text, nullable=True)

    # Location
    location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)
    address = Column(Text, nullable=False)
    city = Column(String(100), nullable=True, index=True)
    local_government = Column(String(100), nullable=False, index=True)
    state = Column(String(100), nullable=False, index=True)
    country = Column(String(100), default="Nigeria")

    # Contact
    business_phone = Column(String(20), nullable=True)
    business_email = Column(String(255), nullable=True)
    website = Column(Text, nullable=True)
    instagram = Column(String(100), nullable=True)     # handle without @
    facebook = Column(String(255), nullable=True)
    whatsapp = Column(String(20), nullable=True)
    opening_hours = Column(Text, nullable=True)        # JSON string

    # Verification & Subscription
    verification_badge = Column(
        Enum(VerificationBadgeEnum),
        default=VerificationBadgeEnum.NONE,
        nullable=False
    )
    subscription_tier = Column(String(50), default="free")
    subscription_start_date = Column(DateTime(timezone=True), nullable=True)
    subscription_end_date = Column(DateTime(timezone=True), nullable=True)
    is_featured = Column(Boolean, default=False, index=True)
    featured_until = Column(DateTime(timezone=True), nullable=True)

    # Stats
    average_rating = Column(Numeric(3, 2), default=0.00, index=True)
    total_reviews = Column(Integer, default=0)
    total_orders = Column(Integer, default=0)
    response_time_minutes = Column(Integer, nullable=True)

    # Status
    is_verified = Column(Boolean, default=False, index=True)
    is_active = Column(Boolean, default=True, index=True)

    # Relationships
    user = relationship("User", back_populates="business")
    business_hours = relationship(
        "BusinessHours",
        back_populates="business",
        cascade="all, delete-orphan"
    )
    hotel = relationship(
        "Hotel",
        back_populates="business",
        uselist=False,
        cascade="all, delete-orphan"
    )
    restaurant = relationship(
        "Restaurant",
        back_populates="business",
        uselist=False,
        cascade="all, delete-orphan"
    )
    service_provider = relationship(
        "ServiceProvider",
        back_populates="business",
        uselist=False,
        cascade="all, delete-orphan"
    )
    product_vendor = relationship(
        "ProductVendor",
        back_populates="business",
        uselist=False,
        cascade="all, delete-orphan"
    )
    # REMOVED: health_provider relationship - doesn't exist
    # Health entities (Doctor, Pharmacy, LabCenter) already have their own relationships
    doctor = relationship(
        "Doctor",
        back_populates="business",
        uselist=False,
        cascade="all, delete-orphan"
    )
    pharmacy = relationship(
        "Pharmacy",
        back_populates="business",
        uselist=False,
        cascade="all, delete-orphan"
    )
    lab_center = relationship(
        "LabCenter",
        back_populates="business",
        uselist=False,
        cascade="all, delete-orphan"
    )
    # ADD THIS NEW BLOCK HERE ↓
    property_agent = relationship(
        "PropertyAgent",
        back_populates="business",
        uselist=False,
        cascade="all, delete-orphan"
    )
    stories = relationship(
        "Story",
        back_populates="business",
        cascade="all, delete-orphan",
        order_by="desc(Story.created_at)"
    )
    reels = relationship(
        "Reel",
        back_populates="business",
        cascade="all, delete-orphan",
        order_by="desc(Reel.created_at)"
    )
    job_postings = relationship(
        "JobPosting",
        back_populates="business",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            category.in_([
                'lodges', 'food', 'services', 'products',
                'health', 'property_agent', 'ticket_sales'
            ]),
            name='valid_business_category'
        ),
    )

    # Properties for Pydantic serialization
    @property
    def latitude(self) -> float:
        """Extract latitude from Geography point"""
        if self.location:
            point = to_shape(self.location)
            return point.y
        return 0.0

    @property
    def longitude(self) -> float:
        """Extract longitude from Geography point"""
        if self.location:
            point = to_shape(self.location)
            return point.x
        return 0.0

    def __repr__(self):
        return f"<Business {self.business_name} ({self.category})>"


# ============================================
# BUSINESS HOURS
# ============================================

class BusinessHours(BaseModel):
    """Operating hours for businesses"""

    __tablename__ = "business_hours"

    business_id = Column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    is_open = Column(Boolean, default=True)
    open_time = Column(Time, nullable=True)
    close_time = Column(Time, nullable=True)

    # Relationships
    business = relationship("Business", back_populates="business_hours")

    __table_args__ = (
        CheckConstraint('day_of_week >= 0 AND day_of_week <= 6', name='valid_day_of_week'),
        UniqueConstraint('business_id', 'day_of_week', name='unique_business_day'),
    )

    def __repr__(self):
        return f"<BusinessHours {self.business_id} Day {self.day_of_week}>"