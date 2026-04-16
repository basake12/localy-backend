"""
app/models/business_model.py

FIXES vs previous version:
  1. [HARD RULE] local_government column deleted — no LGA anywhere.
     Blueprint §4 / §2: "No LGA column in any database table."

  2. location is now NOT NULL — a business with no geocoded point cannot
     appear in any ST_DWithin query. Blueprint §14.

  3. spatial_index=True on location — creates the required GIST index for
     ST_DWithin performance. Without this every radius query is a full table
     scan. Blueprint §4.3.

  4. address renamed registered_address to match Blueprint §14 exactly.

  5. service_radius_m INTEGER NOT NULL DEFAULT 5000 added. Blueprint §14.

  6. subscription_tier_rank INTEGER NOT NULL DEFAULT 1 added.
     Required for discovery ORDER BY: subscription_tier_rank DESC, distance_m ASC.

  7. subscription_status + subscription_expires_at added. Blueprint §14 / §8.1
     (7-day grace period logic reads subscription_status).

  8. product_limit_override + product_limit_override_value added.
     Blueprint §2 / §6.4 HARD RULE — admin override of 20-product free limit.

  9. bank_account_number, bank_code, bank_name added. Blueprint §14 / §5.2
     (business wallet withdrawal requires registered bank account).

  10. verification_reviewed_by references admin_users.id (not users.id).
      verification_reviewed_at added. Blueprint §14.

  11. subscription_tier CHECK constraint enforced at DB level.

  12. VerificationBadgeEnum removed — subscription_tier drives badge display,
      no separate badge enum needed per blueprint.
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
    Time,
    CheckConstraint,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geography
from geoalchemy2.shape import to_shape
import enum

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class BusinessCategoryEnum(str, enum.Enum):
    """
    Blueprint §1 / §6 — exactly seven categories, immutable after registration
    without admin override.
    """
    LODGES         = "lodges"
    FOOD           = "food"
    SERVICES       = "services"
    PRODUCTS       = "products"
    HEALTH         = "health"
    PROPERTY_AGENT = "property_agent"
    TICKET_SALES   = "ticket_sales"


# ─── Business ─────────────────────────────────────────────────────────────────

class Business(BaseModel):
    """
    Business entity. Blueprint §14.

    Discovery is RADIUS-ONLY (PostGIS ST_DWithin).
    No LGA column. No LGA filter anywhere in the codebase.
    """
    __tablename__ = "businesses"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # ── Basic info ────────────────────────────────────────────────────────────
    business_name = Column(String(255), nullable=False, index=True)

    # Blueprint §2 HARD RULE: category is immutable post-registration without
    # admin override. Stored as VARCHAR per Blueprint §14.
    category    = Column(String(64), nullable=False, index=True)
    subcategory = Column(String(100), nullable=True, index=True)
    description = Column(Text, nullable=True)
    logo        = Column(Text, nullable=True)
    banner_image = Column(Text, nullable=True)

    # ── Location — Blueprint §14 ──────────────────────────────────────────────
    # NOT NULL — a business must be geocoded at registration.
    # spatial_index=True creates the mandatory GIST index for ST_DWithin.
    # Blueprint §4.3: CREATE INDEX idx_businesses_location ON businesses USING GIST (location)
    location = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True),
        nullable=False,
    )

    # Blueprint §14: registered_address TEXT NOT NULL
    registered_address = Column(Text, nullable=False)

    # REMOVED: local_government — Blueprint HARD RULE: no LGA column anywhere.
    city    = Column(String(100), nullable=True, index=True)
    state   = Column(String(100), nullable=True, index=True)
    country = Column(String(100), default="Nigeria")

    # ── Service radius — Blueprint §14 ───────────────────────────────────────
    # Business sets custom service radius. Does NOT override user discovery
    # radius — adds a secondary filter for service availability (§4.1).
    service_radius_m = Column(Integer, nullable=False, default=5000)

    # ── Contact ───────────────────────────────────────────────────────────────
    business_phone = Column(String(20), nullable=True)
    business_email = Column(String(255), nullable=True)
    website        = Column(Text, nullable=True)
    instagram      = Column(String(100), nullable=True)
    facebook       = Column(String(255), nullable=True)
    whatsapp       = Column(String(20), nullable=True)

    # ── Verification — Blueprint §14 ──────────────────────────────────────────
    is_verified = Column(Boolean, default=False, nullable=False, index=True)

    # FK to admin_users.id — not users.id (admin is a separate table)
    verification_reviewed_by = Column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    verification_reviewed_at = Column(DateTime(timezone=True), nullable=True)

    # ── Subscription — Blueprint §14 / §8.1 ──────────────────────────────────
    # subscription_tier: 'free' | 'starter' | 'pro' | 'enterprise'
    subscription_tier = Column(String(20), nullable=False, default="free")

    # Rank used in ORDER BY for all discovery queries.
    # Enterprise=4, Pro=3, Starter=2, Free=1
    # Blueprint §7.2: "Tier rank mapping (stored as integer for ORDER BY)"
    subscription_tier_rank = Column(Integer, nullable=False, default=1)

    # Blueprint §8.1 / §14: subscription_status for grace period handling
    # 'active' | 'grace_period' | 'expired' | 'cancelled'
    subscription_status    = Column(String(20), nullable=False, default="active")
    subscription_expires_at = Column(DateTime(timezone=True), nullable=True)

    # ── Product Limit Override — Blueprint §2 / §6.4 HARD RULE ───────────────
    # Admin can override the 20-product free plan limit for a specific business.
    # If product_limit_override=TRUE: use product_limit_override_value instead of 20.
    product_limit_override       = Column(Boolean, nullable=False, default=False)
    product_limit_override_value = Column(Integer, nullable=True)

    # ── Bank Account — Blueprint §14 / §5.2 ──────────────────────────────────
    # Required for business wallet withdrawal via Monnify.
    bank_account_number = Column(String(20), nullable=True)
    bank_code           = Column(String(10), nullable=True)
    bank_name           = Column(String(100), nullable=True)

    # ── Featured ──────────────────────────────────────────────────────────────
    is_featured    = Column(Boolean, default=False, nullable=False, index=True)
    featured_until = Column(DateTime(timezone=True), nullable=True)

    # ── Stats (denormalised for feed ranking) ─────────────────────────────────
    average_rating        = Column(Numeric(3, 2), default=0.00, index=True)
    total_reviews         = Column(Integer, default=0)
    total_orders          = Column(Integer, default=0)
    response_time_minutes = Column(Integer, nullable=True)

    # ── Status ────────────────────────────────────────────────────────────────
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    user           = relationship("User", back_populates="business")
    business_hours = relationship(
        "BusinessHours", back_populates="business", cascade="all, delete-orphan"
    )
    hotel = relationship(
        "Hotel", back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    restaurant = relationship(
        "Restaurant", back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    service_provider = relationship(
        "ServiceProvider", back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    product_vendor = relationship(
        "ProductVendor", back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    doctor = relationship(
        "Doctor", back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    pharmacy = relationship(
        "Pharmacy", back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    lab_center = relationship(
        "LabCenter", back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    property_agent = relationship(
        "PropertyAgent", back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    stories = relationship(
        "Story", back_populates="business", cascade="all, delete-orphan",
        order_by="desc(Story.created_at)",
    )
    reels = relationship(
        "Reel", back_populates="business", cascade="all, delete-orphan",
        order_by="desc(Reel.created_at)",
    )
    job_postings = relationship(
        "JobPosting", back_populates="business", cascade="all, delete-orphan"
    )
    products = relationship(
        "Product", back_populates="business", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('lodges','food','services','products','health','property_agent','ticket_sales')",
            name="valid_business_category",
        ),
        CheckConstraint(
            "subscription_tier IN ('free','starter','pro','enterprise')",
            name="valid_subscription_tier",
        ),
        CheckConstraint(
            "subscription_tier_rank BETWEEN 1 AND 4",
            name="valid_subscription_tier_rank",
        ),
        # Note: GIST spatial index is created by spatial_index=True on the
        # Geography column above — equivalent to:
        # CREATE INDEX idx_businesses_location ON businesses USING GIST (location);
    )

    @property
    def latitude(self) -> float:
        if self.location:
            return to_shape(self.location).y
        return 0.0

    @property
    def longitude(self) -> float:
        if self.location:
            return to_shape(self.location).x
        return 0.0

    def __repr__(self) -> str:
        return f"<Business {self.business_name} ({self.category})>"


# ─── Business Hours ────────────────────────────────────────────────────────────

class BusinessHours(BaseModel):
    """Operating hours per day of week. Blueprint §6 (all modules)."""

    __tablename__ = "business_hours"

    business_id  = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_of_week = Column(Integer, nullable=False)   # 0=Monday, 6=Sunday
    is_open     = Column(Boolean, default=True, nullable=False)
    open_time   = Column(Time, nullable=True)
    close_time  = Column(Time, nullable=True)

    business = relationship("Business", back_populates="business_hours")

    __table_args__ = (
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="valid_day_of_week"),
        UniqueConstraint("business_id", "day_of_week", name="unique_business_day"),
    )

    def __repr__(self) -> str:
        return f"<BusinessHours {self.business_id} day={self.day_of_week}>"