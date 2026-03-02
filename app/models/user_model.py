from sqlalchemy import (
    Column, String, Boolean, Enum, DateTime, Text,
    Integer, Numeric, CheckConstraint, ForeignKey, Date
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geography
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class UserTypeEnum(str, enum.Enum):
    CUSTOMER = "customer"
    BUSINESS = "business"
    RIDER = "rider"
    ADMIN = "admin"


class UserStatusEnum(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING_VERIFICATION = "pending_verification"
    BANNED = "banned"


# ============================================
# USER MODEL
# ============================================

class User(BaseModel):
    """Main users table - all user types"""

    __tablename__ = "users"

    # Basic Info
    user_type = Column(Enum(UserTypeEnum), nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)

    # Verification
    is_email_verified = Column(Boolean, default=False)
    is_phone_verified = Column(Boolean, default=False)
    email_verification_token = Column(String(255), nullable=True)
    phone_verification_otp = Column(String(10), nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Password reset
    password_reset_otp = Column(String(10), nullable=True)
    password_reset_expires = Column(DateTime(timezone=True), nullable=True)

    # OAuth (Google / Apple - customers only)
    oauth_provider = Column(String(20), nullable=True)    # "google" | "apple"
    oauth_provider_id = Column(String(255), nullable=True)

    # Status
    status = Column(
        Enum(UserStatusEnum),
        default=UserStatusEnum.PENDING_VERIFICATION,
        nullable=False,
        index=True
    )
    last_login = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    customer_profile = relationship(
        "CustomerProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    business = relationship(
        "Business",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    rider = relationship(
        "Rider",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    admin = relationship(
        "Admin",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    wallet = relationship(
        "Wallet",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    notifications = relationship(
        "Notification",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    notification_preferences = relationship(
        "NotificationPreference",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    device_tokens = relationship(
        "DeviceToken",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    job_applications = relationship(
        "JobApplication",
        back_populates="applicant",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User {self.email} ({self.user_type})>"


# ============================================
# CUSTOMER PROFILE
# ============================================

class CustomerProfile(BaseModel):
    """Customer-specific profile data"""

    __tablename__ = "customer_profiles"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Personal Info
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    date_of_birth = Column(Date, nullable=True)
    gender = Column(String(20), nullable=True)
    profile_picture = Column(Text, nullable=True)
    bio = Column(Text, nullable=True)

    # Location
    default_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)
    current_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)
    local_government = Column(String(100), nullable=True, index=True)
    state = Column(String(100), nullable=True, index=True)
    country = Column(String(100), default="Nigeria")

    # Relationships
    user = relationship("User", back_populates="customer_profile")

    def __repr__(self):
        return f"<CustomerProfile {self.first_name} {self.last_name}>"


# ============================================
# ADMIN MODEL
# ============================================

class Admin(BaseModel):
    """Admin-specific data"""

    __tablename__ = "admins"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    full_name = Column(String(200), nullable=False)
    role = Column(String(50), default="admin")
    permissions = Column(Text, nullable=True)  # JSON stored as text

    # Relationships
    user = relationship("User", back_populates="admin")

    def __repr__(self):
        return f"<Admin {self.full_name}>"