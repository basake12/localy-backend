"""
app/crud/user_crud.py

Blueprint v2.0 changes:
- OAuth methods deprecated (not removed for data compatibility)
- PIN CRUD operations added
- PIN lockout tracking added
- Biometric enable/disable added
- Terms acceptance tracking added
- date_of_birth capture added
"""
from __future__ import annotations

from typing import Optional, Dict, Any
from sqlalchemy.orm import Session, joinedload
from uuid import UUID
from datetime import datetime, timedelta, timezone
import json

from app.crud.base_crud import CRUDBase
from app.models.user_model import User, CustomerProfile, Admin
from app.models.business_model import Business
from app.models.rider_model import Rider
from app.schemas.auth_schema import RegisterRequest
from app.core.security import (
    hash_password, verify_password, generate_otp,
    hash_pin, verify_pin, validate_pin
)
from app.core.exceptions import (
    AlreadyExistsException,
    NotFoundException,
    InvalidCredentialsException,
    ValidationException,
)
from app.core.constants import UserType, UserStatus

try:
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point
    GEO_AVAILABLE = True
except ImportError:
    GEO_AVAILABLE = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CRUDUser(CRUDBase[User, RegisterRequest, Dict[str, Any]]):

    # ── Lookups ──────────────────────────────────────────────────────────────

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

    # ── Create (standard) ────────────────────────────────────────────────────

    def create_user(self, db: Session, *, obj_in: RegisterRequest) -> User:
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
            # Blueprint v2.0: Terms acceptance tracking
            terms_accepted_at=_now() if obj_in.terms_accepted else None,
            terms_version=obj_in.terms_version if obj_in.terms_accepted else None,
        )
        db.add(user)
        db.flush()

        self._create_profile(db, user=user, obj_in=obj_in)
        self._set_otp(db, user=user)

        db.commit()
        db.refresh(user)
        return user

    # ── Profile factory ──────────────────────────────────────────────────────

    def _create_profile(
        self,
        db: Session,
        *,
        user: User,
        obj_in: RegisterRequest,
        avatar: Optional[str] = None,
    ):
        if obj_in.user_type == UserType.CUSTOMER:
            db.add(CustomerProfile(
                user_id=user.id,
                first_name=obj_in.first_name or "",
                last_name=obj_in.last_name or "",
                date_of_birth=obj_in.date_of_birth,  # Blueprint v2.0
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
                opening_hours=json.dumps(obj_in.opening_hours)
                    if obj_in.opening_hours else None,
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

    # ── OTP ──────────────────────────────────────────────────────────────────

    def _set_otp(
        self, db: Session, *, user: User, expiry_minutes: int = 10
    ) -> str:
        otp = generate_otp()
        user.phone_verification_otp = otp
        user.otp_expires_at = _now() + timedelta(minutes=expiry_minutes)
        return otp

    def regenerate_otp(self, db: Session, *, user: User) -> str:
        otp = self._set_otp(db, user=user)
        db.commit()
        return otp

    def verify_otp_code(
        self,
        db: Session,
        *,
        user: User,
        otp: str,
        channel: str,
    ) -> bool:
        if user.phone_verification_otp != otp:
            return False
        if user.otp_expires_at and user.otp_expires_at < _now():
            return False

        if channel == "email":
            user.is_email_verified = True
        elif channel == "phone":
            user.is_phone_verified = True

        if user.is_email_verified and user.is_phone_verified:
            user.phone_verification_otp = None
            user.otp_expires_at         = None
            user.status                 = UserStatus.ACTIVE

        db.commit()
        return True

    # ── Password reset OTP ────────────────────────────────────────────────────

    def set_password_reset_otp(self, db: Session, *, user: User) -> str:
        otp = generate_otp()
        user.password_reset_otp     = otp
        user.password_reset_expires = _now() + timedelta(minutes=30)
        db.commit()
        return otp

    def check_password_reset_otp(
        self, db: Session, *, user: User, otp: str
    ) -> bool:
        if not user.password_reset_otp or user.password_reset_otp != otp:
            return False
        if user.password_reset_expires and user.password_reset_expires < _now():
            return False
        user.password_reset_otp     = None
        user.password_reset_expires = None
        db.commit()
        return True

    def reset_password(
        self, db: Session, *, user: User, new_password: str
    ) -> None:
        """Apply a new hashed password."""
        user.password_hash = hash_password(new_password)
        db.commit()

    # ── Authentication ────────────────────────────────────────────────────────

    def authenticate(
        self,
        db: Session,
        *,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        password: str,
    ) -> Optional[User]:
        user: Optional[User] = None
        if email:
            user = self.get_by_email(db, email=email)
        elif phone:
            user = self.get_by_phone(db, phone=phone)

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

    # ── PIN Management (Blueprint v2.0) ───────────────────────────────────────

    def set_pin(self, db: Session, *, user: User, pin: str) -> None:
        """
        Set or update user's 4-digit PIN.
        
        Blueprint: "Set 4-digit transaction PIN (mandatory — enables wallet and payments)"
        """
        if not validate_pin(pin):
            raise ValidationException("PIN must be exactly 4 digits")
        
        user.pin_hash = hash_pin(pin)
        # Reset lockout on PIN change
        user.failed_pin_attempts = 0
        user.pin_locked_until = None
        db.commit()

    def verify_pin_auth(
        self, db: Session, *, user: User, pin: str
    ) -> bool:
        """
        Verify PIN and handle lockout logic.
        
        Blueprint: "5 wrong PIN attempts → 30-minute lockout → SMS unlock code"
        
        Returns True if PIN is correct, False otherwise.
        Raises ValidationException if account is locked.
        """
        # Check if account is locked
        if user.pin_locked_until and user.pin_locked_until > _now():
            remaining = (user.pin_locked_until - _now()).seconds // 60
            raise ValidationException(
                f"PIN locked. Try again in {remaining} minutes or request unlock code via SMS."
            )

        # Clear expired lockout
        if user.pin_locked_until and user.pin_locked_until <= _now():
            user.pin_locked_until = None
            user.failed_pin_attempts = 0
            db.commit()

        # Verify PIN
        if not user.pin_hash:
            raise ValidationException("PIN not set")

        if verify_pin(pin, user.pin_hash):
            # Success — reset failed attempts
            if user.failed_pin_attempts > 0:
                user.failed_pin_attempts = 0
                db.commit()
            return True
        else:
            # Failed attempt
            user.failed_pin_attempts += 1
            
            # Lock after 5 failed attempts
            if user.failed_pin_attempts >= 5:
                user.pin_locked_until = _now() + timedelta(minutes=30)
                db.commit()
                raise ValidationException(
                    "Too many failed attempts. PIN locked for 30 minutes. "
                    "Request unlock code via SMS."
                )
            
            db.commit()
            return False

    def change_pin(
        self, db: Session, *, user: User, old_pin: str, new_pin: str
    ) -> None:
        """Change PIN — requires old PIN for security."""
        if not user.pin_hash:
            raise ValidationException("No PIN set")
        
        if not verify_pin(old_pin, user.pin_hash):
            raise InvalidCredentialsException("Incorrect old PIN")
        
        if not validate_pin(new_pin):
            raise ValidationException("New PIN must be exactly 4 digits")
        
        user.pin_hash = hash_pin(new_pin)
        user.failed_pin_attempts = 0
        user.pin_locked_until = None
        db.commit()

    def unlock_pin_with_otp(
        self, db: Session, *, user: User, otp: str
    ) -> None:
        """
        Unlock PIN using SMS OTP.
        
        Blueprint: "5 wrong PIN attempts → 30-minute lockout → SMS unlock code"
        """
        if not self.check_password_reset_otp(db, user=user, otp=otp):
            raise ValidationException("Invalid or expired unlock code")
        
        user.pin_locked_until = None
        user.failed_pin_attempts = 0
        db.commit()

    # ── Biometric (Blueprint v2.0) ────────────────────────────────────────────

    def enable_biometric(self, db: Session, *, user: User) -> None:
        """
        Enable biometric authentication.
        
        Blueprint: "Optional: enable biometric authentication (Face ID / fingerprint) 
        after PIN is set"
        
        Note: Biometric is only enabled after PIN is set.
        """
        if not user.pin_hash:
            raise ValidationException(
                "PIN must be set before enabling biometric authentication"
            )
        
        user.biometric_enabled = True
        db.commit()

    def disable_biometric(self, db: Session, *, user: User) -> None:
        """Disable biometric authentication."""
        user.biometric_enabled = False
        db.commit()

    # ── Profile update ────────────────────────────────────────────────────────

    def update_customer_profile(
        self,
        db: Session,
        *,
        user: User,
        update_data: Dict[str, Any],
    ) -> "CustomerProfile":
        """Partial update for customer profile."""
        profile = user.customer_profile
        if profile is None:
            raise NotFoundException("Customer profile not found")

        # Phone updates go on User
        phone = update_data.pop("phone", None)
        if phone:
            user.phone = phone

        # Geo point
        lat = update_data.pop("latitude", None)
        lng = update_data.pop("longitude", None)
        if GEO_AVAILABLE and lat is not None and lng is not None:
            profile.default_location = from_shape(Point(lng, lat), srid=4326)

        for field, value in update_data.items():
            if hasattr(profile, field) and value is not None:
                setattr(profile, field, value)

        db.commit()
        db.refresh(profile)
        return profile

    # ── OAuth (DEPRECATED — kept for backward compatibility) ─────────────────

    def create_oauth_user(
        self,
        db: Session,
        *,
        obj_in: RegisterRequest,
        avatar: Optional[str] = None,
    ) -> User:
        """
        DEPRECATED: OAuth removed in Blueprint v2.0.
        Kept for backward compatibility with existing OAuth users.
        """
        if self.get_by_email(db, email=obj_in.email):
            raise AlreadyExistsException("Email already registered")

        user = User(
            user_type=obj_in.user_type,
            email=obj_in.email,
            phone=obj_in.phone,
            password_hash=hash_password(obj_in.password),
            status=UserStatus.ACTIVE,
            is_email_verified=True,
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
        """
        DEPRECATED: OAuth removed in Blueprint v2.0.
        Kept for backward compatibility.
        """
        if not user.oauth_provider:
            user.oauth_provider    = provider
            user.oauth_provider_id = provider_id
            db.commit()
            db.refresh(user)
        return user

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_active(self, user: User) -> bool:
        return user.status == UserStatus.ACTIVE

    def is_fully_verified(self, user: User) -> bool:
        return user.is_email_verified and user.is_phone_verified


user_crud = CRUDUser(User)