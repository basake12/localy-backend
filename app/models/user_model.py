"""
app/models/user_model.py

FIXES vs previous version:
  1.  [HARD RULE] role IN ('customer','business','rider') ONLY.
      'admin' removed — admin lives in AdminUser (separate table).
      Field renamed user_type → role to match Blueprint §14.

  2.  [HARD RULE] pin_hash TEXT NOT NULL — PIN is mandatory, cannot be
      skipped. Blueprint §3 step 6, §3.3, §14.

  3.  [HARD RULE] OAuth columns (oauth_provider, oauth_provider_id)
      deleted. Blueprint §P05: "Phone + password is the ONLY method."

  4.  email is now nullable=True — optional at registration. Blueprint §14.

  5.  phone renamed phone_number to match Blueprint §14 exactly.

  6.  full_name TEXT NOT NULL added to users table directly (Blueprint §14).

  7.  date_of_birth DATE NOT NULL added to users table (Blueprint §14).

  8.  referral_code VARCHAR(16) UNIQUE NOT NULL on users (Blueprint §14).

  9.  referred_by_user_id UUID FK to users (Blueprint §14).

  10. biometric_enabled renamed biometric_flag (Blueprint §14).

  11. status Enum replaced with is_active + is_banned booleans (Blueprint §14).

  12. UserAgreement model added (Blueprint §14 / §3 step 8).

  13. AdminUser model is now the SEPARATE admin_users table — NOT linked to
      users. Blueprint §2 / §11 / §13.3 / §14.

  14. CustomerProfile local_government column removed (Blueprint HARD RULE:
      no LGA anywhere).

  15. [NEW FIX] Dead OTP DB columns removed from User model.
      Blueprint §3.1: "OTP is 6-digit, TTL = 5 minutes (stored in Redis
      with key otp:{phone})." OTP is NEVER stored in the database.
      Columns phone_verification_otp, otp_expires_at, password_reset_otp,
      password_reset_expires were always NULL (auth_service correctly uses
      Redis) and created dual-truth confusion. Removed to enforce single
      source of truth: Redis only.
      NOTE: If these columns exist in your DB, create an Alembic migration
      to drop them: op.drop_column('users', 'phone_verification_otp') etc.
"""
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Enum,
    DateTime,
    Text,
    Integer,
    CheckConstraint,
    ForeignKey,
    Date,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import enum

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class UserRoleEnum(str, enum.Enum):
    """
    Blueprint §14 / §2 HARD RULE:
    role IN ('customer','business','rider') ONLY.
    Admin is a completely separate table (admin_users) — never in users.
    """
    CUSTOMER = "customer"
    BUSINESS = "business"
    RIDER    = "rider"


# ─── User ─────────────────────────────────────────────────────────────────────

class User(BaseModel):
    """
    Core user entity. Blueprint §14.

    Role is permanent after registration — no self-service change endpoint.
    Admin accounts live in admin_users, never here.
    """
    __tablename__ = "users"

    # ── Core identity ────────────────────────────────────────────────────────
    # Blueprint §14: phone_number VARCHAR(20) UNIQUE NOT NULL
    phone_number  = Column(String(20), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)

    # Blueprint §14: full_name VARCHAR(255) NOT NULL
    full_name     = Column(String(255), nullable=False)

    # Blueprint §14: date_of_birth DATE NOT NULL — used for age verification
    date_of_birth = Column(Date, nullable=False)

    # Blueprint §14: email VARCHAR(255) UNIQUE — nullable (optional at registration)
    email = Column(String(255), unique=True, nullable=True, index=True)

    # ── Role — HARD RULE ─────────────────────────────────────────────────────
    # Blueprint §14: role VARCHAR(20) NOT NULL CHECK (role IN ('customer','business','rider'))
    # Role is permanent. No self-service switch. Admin override only.
    role = Column(
        Enum(UserRoleEnum, name="user_role_enum"),
        nullable=False,
        index=True,
    )

    # ── PIN & Security — Blueprint §3.3 / §3.6 HARD RULE ────────────────────
    # pin_hash TEXT NOT NULL — mandatory at registration, never null in prod.
    # Set in Step 6 of onboarding. Hashed with bcrypt. Never plaintext.
    pin_hash            = Column(Text, nullable=False)
    failed_pin_attempts = Column(Integer, default=0, nullable=False)
    pin_locked_until    = Column(DateTime(timezone=True), nullable=True)

    # Blueprint §14: biometric_flag BOOLEAN NOT NULL DEFAULT FALSE
    # Server stores only a boolean flag — no biometric data ever touches server.
    biometric_flag = Column(Boolean, default=False, nullable=False)

    # ── Referral — Blueprint §14: on users table directly ────────────────────
    # 8-character alphanumeric, UNIQUE. Generated at registration.
    referral_code       = Column(String(16), unique=True, nullable=False, index=True)
    referred_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Account status — Blueprint §14: two separate booleans ────────────────
    is_active  = Column(Boolean, default=True,  nullable=False, index=True)
    is_banned  = Column(Boolean, default=False, nullable=False, index=True)
    ban_reason = Column(Text, nullable=True)

    # ── Phone verification — set TRUE after OTP confirmed ────────────────────
    # Blueprint §3.1 Step 2: OTP verified → phone marked verified in Redis session.
    # This DB flag is the durable record: set once by mark_phone_verified().
    is_phone_verified = Column(Boolean, default=False, nullable=False)

    # ── Session tracking ──────────────────────────────────────────────────────
    last_login = Column(DateTime(timezone=True), nullable=True)

    # REMOVED: phone_verification_otp, otp_expires_at — Blueprint §3.1:
    #   OTP stored ONLY in Redis (key: otp:{phone}, TTL=300s). Never in DB.
    #   DB OTP columns are dead code that creates dual-truth confusion.
    #   Drop via Alembic if they exist in your schema.
    #
    # REMOVED: password_reset_otp, password_reset_expires — same reason.
    #   Reset OTP lives in Redis (same otp:{phone} key). auth_service.py
    #   is already Redis-only — these DB columns were never written to.
    #
    # REMOVED: oauth_provider, oauth_provider_id
    #   Blueprint §P05 HARD RULE: Google/Apple OAuth deleted entirely.
    #   Phone + password is the ONLY registration and login method.

    # ── Relationships ─────────────────────────────────────────────────────────
    business      = relationship(
        "Business", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    rider         = relationship(
        "Rider", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    wallet        = relationship(
        "Wallet", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    subscriptions = relationship(
        "Subscription", back_populates="user", cascade="all, delete-orphan"
    )
    notifications = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )
    notification_preferences = relationship(
        "NotificationPreference", back_populates="user", cascade="all, delete-orphan"
    )
    device_tokens  = relationship(
        "DeviceToken", back_populates="user", cascade="all, delete-orphan"
    )
    job_applications = relationship(
        "JobApplication", back_populates="applicant", cascade="all, delete-orphan"
    )
    addresses = relationship(
        "CustomerAddress", back_populates="user", cascade="all, delete-orphan"
    )
    agreements = relationship(
        "UserAgreement", back_populates="user", cascade="all, delete-orphan"
    )
    # Referral relationships — using explicit FK to avoid ambiguity
    referrals_given   = relationship(
        "Referral",
        foreign_keys="Referral.referrer_id",
        back_populates="referrer",
    )
    referral_received = relationship(
        "Referral",
        foreign_keys="Referral.referred_id",
        back_populates="referred",
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('customer','business','rider')",
            name="valid_user_role",
        ),
    )

    def __repr__(self) -> str:
        return f"<User {self.phone_number} role={self.role}>"


# ─── Customer Profile ──────────────────────────────────────────────────────────

class CustomerProfile(BaseModel):
    """
    Extended profile for customer-role users.
    Blueprint §14 does not mandate this as a separate table — but it's an
    acceptable extension provided the core user fields stay on users.

    REMOVED: local_government column (Blueprint HARD RULE: no LGA anywhere).
    """
    __tablename__ = "customer_profiles"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    first_name      = Column(String(100), nullable=False)
    last_name       = Column(String(100), nullable=False)
    date_of_birth   = Column(Date, nullable=True)   # mirrors users.date_of_birth
    gender          = Column(String(20), nullable=True)
    profile_picture = Column(Text, nullable=True)
    bio             = Column(Text, nullable=True)

    # Location — radius-based, no LGA
    # Blueprint §4: GPS position only. No LGA column.
    default_location = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    current_location = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    # REMOVED: local_government — Blueprint HARD RULE: no LGA column anywhere.
    state   = Column(String(100), nullable=True, index=True)
    country = Column(String(100), default="Nigeria")

    # Discovery radius preference — Blueprint §4.1: 1–50 km slider
    discovery_radius_m = Column(Integer, default=5000, nullable=False)

    settings = Column(JSONB, nullable=True, default=dict)

    user = relationship("User", back_populates="customer_profile")

    def __repr__(self) -> str:
        return f"<CustomerProfile {self.first_name} {self.last_name}>"


# Attach customer_profile to User
User.customer_profile = relationship(
    "CustomerProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
)


# ─── User Agreement — Blueprint §14 / §3 step 8 ───────────────────────────────

class UserAgreement(BaseModel):
    """
    Logs each T&C + Privacy Policy acceptance.

    Blueprint §3 step 8: "Acceptance logged: user_agreements table
    (user_id, version_id, accepted_at)."

    Mobile always fetches the latest T&C version on every load (admin-editable).
    Never use a cached / hardcoded string for legal text.
    """
    __tablename__ = "user_agreements"

    user_id    = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # References the T&C document version UUID managed in the admin panel.
    version_id = Column(UUID(as_uuid=True), nullable=False)
    # created_at (inherited from BaseModel) = accepted_at per Blueprint §14.

    user = relationship("User", back_populates="agreements")

    def __repr__(self) -> str:
        return f"<UserAgreement user={self.user_id} version={self.version_id}>"


# ─── Admin User — SEPARATE table, NEVER linked to users ───────────────────────

class AdminUser(BaseModel):
    """
    Blueprint §14 / §2 HARD RULE / §11 / §13.3:

    Admin accounts are COMPLETELY SEPARATE from the users table.
    - No self-registration — provisioned only by super-admin.
    - Admin JWT tokens issued by a separate endpoint.
    - Admin tokens carry {role: "admin", admin_id: uuid}.
    - Admin tokens are NEVER accepted by mobile API endpoints.
    - Admin exists only as a web application (admin.localy.ng).

    This table is referenced by:
      - businesses.verification_reviewed_by
      - coupons.created_by_admin_id
      - admin_wallet_adjustments.admin_id
      - content moderation audit logs
    """
    __tablename__ = "admin_users"

    email         = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    full_name     = Column(String(255), nullable=False)

    # Blueprint §14: role VARCHAR(30) NOT NULL DEFAULT 'support_agent'
    role      = Column(String(30), nullable=False, default="support_agent")
    is_active = Column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<AdminUser {self.full_name} ({self.role})>"