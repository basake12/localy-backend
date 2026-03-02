"""
CRUD operations for User model.
Handles creation, authentication, OTP verification, OAuth, and password reset.
"""
from __future__ import annotations

from typing import Optional, Dict, Any
from sqlalchemy.orm import Session, joinedload
from uuid import UUID
from datetime import datetime, timedelta, timezone

def _now() -> datetime:
    """UTC now — always timezone-aware. Use instead of datetime.utcnow()."""
    return datetime.now(timezone.utc)
import json

from app.crud.base_crud import CRUDBase
from app.models.user_model import User, CustomerProfile, Admin
from app.models.business_model import Business
from app.models.rider_model import Rider
from app.schemas.auth_schema import RegisterRequest
from app.core.security import hash_password, verify_password, generate_otp
from app.core.exceptions import AlreadyExistsException, NotFoundException, InvalidCredentialsException
from app.core.constants import UserType, UserStatus

try:
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point
    GEO_AVAILABLE = True
except ImportError:
    GEO_AVAILABLE = False


class CRUDUser(CRUDBase[User, RegisterRequest, Dict[str, Any]]):
    """Full CRUD for the User model."""

    # ──────────────────────────────────────────
    # LOOKUPS
    # ──────────────────────────────────────────

    def get_by_email(self, db: Session, *, email: str) -> Optional[User]:
        return db.query(User).filter(User.email == email).first()

    def get_by_phone(self, db: Session, *, phone: str) -> Optional[User]:
        return db.query(User).filter(User.phone == phone).first()

    def get_with_profile(self, db: Session, *, user_id: UUID) -> Optional[User]:
        return (
            db.query(User)
            .options(
                joinedload(User.customer_profile),
                joinedload(User.business),
                joinedload(User.rider),
                joinedload(User.admin),
                joinedload(User.wallet),
            )
            .filter(User.id == user_id)
            .first()
        )

    # ──────────────────────────────────────────
    # CREATE — standard (email + password)
    # ──────────────────────────────────────────

    def create_user(self, db: Session, *, obj_in: RegisterRequest) -> User:
        """
        Create a new user with the appropriate profile.

        Raises:
            AlreadyExistsException: email or phone already registered
        """
        if self.get_by_email(db, email=obj_in.email):
            raise AlreadyExistsException("Email already registered")
        if self.get_by_phone(db, phone=obj_in.phone):
            raise AlreadyExistsException("Phone number already registered")

        user = User(
            user_type=obj_in.user_type,
            email=obj_in.email,
            phone=obj_in.phone,
            password_hash=hash_password(obj_in.password),
            status=UserStatus.PENDING_VERIFICATION,
            is_email_verified=False,
            is_phone_verified=False,
        )
        db.add(user)
        db.flush()  # get user.id

        self._create_profile(db, user=user, obj_in=obj_in)
        self._set_otp(db, user=user)

        db.commit()
        db.refresh(user)
        return user

    # ──────────────────────────────────────────
    # CREATE — OAuth (Google / Apple)
    # ──────────────────────────────────────────

    def create_oauth_user(
        self,
        db: Session,
        *,
        obj_in: RegisterRequest,
        avatar: Optional[str] = None,
    ) -> User:
        """
        Create user from OAuth sign-in.
        Email is pre-verified; phone is required later.
        """
        if self.get_by_email(db, email=obj_in.email):
            raise AlreadyExistsException("Email already registered")

        user = User(
            user_type=obj_in.user_type,
            email=obj_in.email,
            phone=obj_in.phone,
            password_hash=hash_password(obj_in.password),  # random password
            status=UserStatus.ACTIVE,
            is_email_verified=True,   # OAuth email is already verified
            is_phone_verified=False,
            oauth_provider=obj_in.oauth_provider,
            oauth_provider_id=obj_in.oauth_provider_id,
        )
        db.add(user)
        db.flush()

        self._create_profile(db, user=user, obj_in=obj_in, avatar=avatar)

        db.commit()
        db.refresh(user)
        return user

    def link_oauth(
        self,
        db: Session,
        *,
        user: User,
        provider: str,
        provider_id: str,
    ) -> User:
        """Link an OAuth provider to an existing user account."""
        if not user.oauth_provider:
            user.oauth_provider    = provider
            user.oauth_provider_id = provider_id
            db.commit()
            db.refresh(user)
        return user

    # ──────────────────────────────────────────
    # PROFILE FACTORY
    # ──────────────────────────────────────────

    def _create_profile(
        self,
        db: Session,
        *,
        user: User,
        obj_in: RegisterRequest,
        avatar: Optional[str] = None,
    ):
        """Create the type-specific profile row."""

        if obj_in.user_type == UserType.CUSTOMER:
            db.add(CustomerProfile(
                user_id=user.id,
                first_name=obj_in.first_name or "",
                last_name=obj_in.last_name or "",
                profile_picture=avatar,
            ))

        elif obj_in.user_type == UserType.BUSINESS:
            location = None
            if GEO_AVAILABLE and obj_in.latitude and obj_in.longitude:
                location = from_shape(
                    Point(obj_in.longitude, obj_in.latitude), srid=4326
                )

            db.add(Business(
                user_id=user.id,
                business_name=obj_in.business_name,
                category=obj_in.business_category,
                subcategory=obj_in.business_subcategory,
                address=obj_in.address,
                city=obj_in.city,
                local_government=obj_in.local_government,
                state=obj_in.state,
                location=location,
                description=obj_in.description,
                website=obj_in.website,
                instagram=obj_in.instagram,
                facebook=obj_in.facebook,
                whatsapp=obj_in.whatsapp,
                opening_hours=json.dumps(obj_in.opening_hours) if obj_in.opening_hours else None,
            ))

        elif obj_in.user_type == UserType.RIDER:
            db.add(Rider(
                user_id=user.id,
                first_name=obj_in.first_name or "",
                last_name=obj_in.last_name or "",
                vehicle_type=obj_in.vehicle_type,
                vehicle_plate_number=obj_in.vehicle_plate_number,
                vehicle_color=obj_in.vehicle_color,
                vehicle_model=obj_in.vehicle_model,
            ))

        elif obj_in.user_type == UserType.ADMIN:
            db.add(Admin(
                user_id=user.id,
                full_name=obj_in.full_name or "",
                role=obj_in.role or "admin",
            ))

    # ──────────────────────────────────────────
    # OTP MANAGEMENT
    # ──────────────────────────────────────────

    def _set_otp(self, db: Session, *, user: User, expiry_minutes: int = 10) -> str:
        """Generate and attach a fresh OTP to the user (does NOT commit)."""
        otp = generate_otp()
        user.phone_verification_otp = otp
        user.otp_expires_at = _now() + timedelta(minutes=expiry_minutes)
        return otp

    def regenerate_otp(self, db: Session, *, user: User) -> str:
        """Generate a new OTP, commit, and return the code."""
        otp = self._set_otp(db, user=user)
        db.commit()
        return otp

    def verify_otp_code(
        self,
        db: Session,
        *,
        user: User,
        otp: str,
        channel: str,   # "email" | "phone"
    ) -> bool:
        """
        Validate OTP and mark the corresponding channel as verified.
        Both email and phone share the same OTP code (sent via their channel).
        """
        # Check code
        if user.phone_verification_otp != otp:
            return False
        # Check expiry
        if user.otp_expires_at and user.otp_expires_at < _now():
            return False

        if channel == "email":
            user.is_email_verified = True
        elif channel == "phone":
            user.is_phone_verified = True

        # Clear OTP only when both channels are done
        if user.is_email_verified and user.is_phone_verified:
            user.phone_verification_otp = None
            user.otp_expires_at = None
            user.status = UserStatus.ACTIVE

        db.commit()
        return True

    # ──────────────────────────────────────────
    # PASSWORD RESET OTP
    # ──────────────────────────────────────────

    def set_password_reset_otp(self, db: Session, *, user: User) -> str:
        """Generate and store a password-reset OTP (30-minute expiry)."""
        otp = generate_otp()
        user.password_reset_otp     = otp
        user.password_reset_expires = _now() + timedelta(minutes=30)
        db.commit()
        return otp

    def check_password_reset_otp(
        self, db: Session, *, user: User, otp: str
    ) -> bool:
        """Return True if the reset OTP is valid and not expired."""
        if not user.password_reset_otp or user.password_reset_otp != otp:
            return False
        if user.password_reset_expires and user.password_reset_expires < _now():
            return False
        # Consume OTP immediately so it can't be reused
        user.password_reset_otp     = None
        user.password_reset_expires = None
        db.commit()
        return True

    # ──────────────────────────────────────────
    # AUTHENTICATION
    # ──────────────────────────────────────────

    def authenticate(
        self, db: Session, *, email: str, password: str
    ) -> Optional[User]:
        user = self.get_by_email(db, email=email)
        if not user:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def update_last_login(self, db: Session, *, user: User) -> User:
        user.last_login = _now()
        db.commit()
        db.refresh(user)
        return user

    def change_password(
        self,
        db: Session,
        *,
        user: User,
        old_password: str,
        new_password: str,
    ) -> bool:
        if not verify_password(old_password, user.password_hash):
            raise InvalidCredentialsException()
        user.password_hash = hash_password(new_password)
        db.commit()
        return True

    # ──────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────

    def is_active(self, user: User) -> bool:
        return user.status == UserStatus.ACTIVE

    def is_fully_verified(self, user: User) -> bool:
        return user.is_email_verified and user.is_phone_verified


# Singleton
user_crud = CRUDUser(User)