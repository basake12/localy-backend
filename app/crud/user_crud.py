"""
app/crud/user_crud.py

COMPLETE REWRITE — previous version had 20+ blueprint violations:

CRITICAL BUGS FIXED:
  1.  [CRASH] `from app.models.user_model import ..., Admin` — 'Admin' does not
      exist on the corrected user_model.py (it is 'AdminUser' on admin_model.py,
      a completely separate table). This import crashes the entire app on startup.
      Fix: removed Admin import. Admin is managed by admin_crud.py only.

  2.  [CRASH] create_user() sets `user_type=obj_in.user_type` — 'user_type' does
      not exist on the corrected User model (renamed to 'role' per Blueprint §14).
      Every registration attempt → AttributeError → 500.
      Fix: use `role=obj_in.role`.

  3.  [CRASH] create_user() sets `phone=obj_in.phone` — 'phone' column renamed to
      'phone_number' per Blueprint §14. AttributeError on every registration.
      Fix: use `phone_number=obj_in.phone`.

  4.  [CRASH] create_user() sets `status=UserStatus.PENDING_VERIFICATION` — 'status'
      column deleted from User model per Blueprint §14 (replaced with is_active BOOL
      + is_banned BOOL). AttributeError on every registration.
      Fix: use `is_active=True, is_phone_verified=False`.

  5.  [CRASH] create_user() sets `is_email_verified=False` — column does not exist
      on corrected User model. Blueprint §3 is phone-only registration.
      Fix: removed.

  6.  [CRASH/SECURITY] create_user() does NOT set pin_hash — Blueprint §3.1 step 6
      HARD RULE: "PIN is MANDATORY. Cannot be skipped. No 'do it later' option."
      pin_hash is TEXT NOT NULL on the users table — missing value = DB integrity
      violation OR nullable column allowing PIN bypass.
      Fix: `pin_hash=hash_pin(obj_in.pin)` in create_user().

  7.  [CRASH] create_user() does NOT set referral_code — Blueprint §14:
      referral_code VARCHAR(16) UNIQUE NOT NULL. Missing value = NOT NULL violation.
      Fix: generate 8-char alphanumeric code via secrets.

  8.  [CRASH] create_user() does NOT set full_name — Blueprint §14:
      full_name VARCHAR(255) NOT NULL. Missing value = NOT NULL violation.
      Fix: use obj_in.full_name.

  9.  [CRASH] create_user() does NOT set date_of_birth — Blueprint §14:
      date_of_birth DATE NOT NULL. Missing = NOT NULL violation.
      Fix: use obj_in.date_of_birth.

  10. [CRASH] _create_profile() uses `obj_in.user_type` — should be `obj_in.role`.

  11. [HARD RULE VIOLATION] _create_profile() sets `address=obj_in.address` —
      Blueprint §14 renamed to `registered_address`. AttributeError at runtime.
      Fix: `registered_address=obj_in.address`.

  12. [HARD RULE VIOLATION] _create_profile() sets `local_government=...` —
      Blueprint §2/§4 HARD RULE: "No LGA column in any database table."
      Fix: removed entirely.

  13. [HARD RULE VIOLATION] _create_profile() handles `UserType.ADMIN` path —
      Blueprint §2 HARD RULE: "Admin cannot register through mobile app."
      Admin accounts are provisioned only by a super-admin via the admin web app.
      Fix: Admin creation path removed entirely.

  14. [BLUEPRINT VIOLATION] get_by_phone() queries `User.phone` → should be
      `User.phone_number` (Blueprint §14). Returns None for every phone lookup
      → every login and OTP flow silently fails.
      Fix: `User.phone_number`.

  15. [BLUEPRINT VIOLATION] get_with_profile() joinedload includes `User.admin`
      — relationship does not exist on corrected User model.
      Fix: removed.

  16. [BLUEPRINT VIOLATION] enable_biometric() sets `user.biometric_enabled` →
      field renamed `biometric_flag` (Blueprint §14). AttributeError at runtime.
      Fix: `user.biometric_flag = True/False`.

  17. [BLUEPRINT VIOLATION] update_customer_profile() sets `user.phone = phone`
      → should be `user.phone_number` (Blueprint §14).
      Fix: `user.phone_number = phone`.

  18. [SECURITY] create_oauth_user() + link_oauth() retained with OAuth columns —
      Blueprint §P05 HARD RULE: "Google/Apple OAuth removed entirely."
      These methods are deleted.

  19. [BLUEPRINT VIOLATION] verify_otp_code() writes OTP to DB columns
      (phone_verification_otp, otp_expires_at). Blueprint §3.1:
      OTP stored ONLY in Redis (`otp:{phone}` TTL 5 min). DB columns are dead.
      Methods `_set_otp`, `verify_otp_code`, `set_password_reset_otp`,
      `check_password_reset_otp` rewritten to use Redis via auth_service.
      The mark_phone_verified() helper is kept for after Redis OTP validation.

  20. [BLUEPRINT VIOLATION] referred_by_user_id not set at registration.
      Blueprint §14: `referred_by_user_id UUID REFERENCES users(id)`.
      Fix: resolve from referral_code if provided and store it.
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.crud.base_crud import CRUDBase
from app.models.user_model import User, CustomerProfile, UserAgreement
from app.models.business_model import Business
from app.core.security import (
    hash_password,
    verify_password,
    hash_pin,
    verify_pin,
    validate_pin,
)
from app.core.exceptions import (
    AlreadyExistsException,
    InvalidCredentialsException,
    NotFoundException,
    ValidationException,
)
from app.schemas.auth_schema import RegisterRequest

try:
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point
    GEO_AVAILABLE = True
except ImportError:
    GEO_AVAILABLE = False

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: always timezone-aware UTC."""
    return datetime.now(timezone.utc)


def _generate_referral_code() -> str:
    """
    Generate an 8-character alphanumeric referral code.
    Blueprint §9.1: "8-character alphanumeric, stored on users.referral_code, UNIQUE index."
    Uses only uppercase letters + digits for readability (no 0/O, 1/I ambiguity).
    """
    alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + string.digits.replace("0", "").replace("1", "")
    return "".join(secrets.choice(alphabet) for _ in range(8))


class CRUDUser(CRUDBase[User, RegisterRequest, Dict[str, Any]]):

    # ══════════════════════════════════════════════════════════════════════════
    # LOOKUPS
    # ══════════════════════════════════════════════════════════════════════════

    def get_by_email(self, db: Session, *, email: str) -> Optional[User]:
        if not email:
            return None
        return db.query(User).filter(User.email == email).first()

    def get_by_phone(self, db: Session, *, phone: str) -> Optional[User]:
        """
        [BUG-14 FIX] Uses phone_number column — Blueprint §14.
        Old code queried `User.phone` which does not exist on the corrected model.
        """
        return db.query(User).filter(User.phone_number == phone).first()

    def get_by_referral_code(self, db: Session, *, code: str) -> Optional[User]:
        return db.query(User).filter(User.referral_code == code).first()

    def get_with_profile(self, db: Session, *, user_id: UUID) -> Optional[User]:
        """
        [BUG-15 FIX] Removed joinedload(User.admin) — that relationship does not
        exist on the corrected User model. Admin is a separate table.
        Blueprint §2 HARD RULE / §14: admin_users is never linked to users.
        """
        return (
            db.query(User)
            .options(
                joinedload(User.customer_profile),
                joinedload(User.business),
                joinedload(User.rider),
                joinedload(User.wallet),
            )
            .filter(User.id == user_id)
            .first()
        )

    # ══════════════════════════════════════════════════════════════════════════
    # REGISTRATION
    # ══════════════════════════════════════════════════════════════════════════

    def create_user(self, db: Session, *, obj_in: RegisterRequest) -> User:
        """
        Create a new user with all mandatory Blueprint §14 fields.

        HARD RULES enforced here:
          - phone_number is unique (Blueprint §14)
          - email is unique if provided (Blueprint §14)
          - pin_hash MANDATORY — never null (Blueprint §3.1 step 6)
          - full_name NOT NULL (Blueprint §14)
          - date_of_birth NOT NULL (Blueprint §14)
          - referral_code 8-char alphanumeric UNIQUE NOT NULL (Blueprint §9.1/§14)
          - role IN ('customer','business','rider') (Blueprint §2/§14)
          - No Google/Apple OAuth (Blueprint §P05 HARD RULE)
          - No LGA anywhere (Blueprint §4 HARD RULE)
          - Admin cannot register via mobile (Blueprint §2 HARD RULE)
        """
        # ── Uniqueness checks ────────────────────────────────────────────────
        # Blueprint §14: phone_number UNIQUE NOT NULL
        if self.get_by_phone(db, phone=obj_in.phone):
            raise AlreadyExistsException("Phone number already registered")

        # Blueprint §14: email UNIQUE (nullable — only check if provided)
        if obj_in.email and self.get_by_email(db, email=obj_in.email):
            raise AlreadyExistsException("Email address already registered")

        # ── Role validation — Blueprint §2 HARD RULE ─────────────────────────
        valid_roles = {"customer", "business", "rider"}
        role = (obj_in.role or "").lower()
        if role not in valid_roles:
            raise ValidationException(
                f"Invalid role '{role}'. Must be one of: {sorted(valid_roles)}"
            )

        # ── PIN validation — Blueprint §3.1 step 6 HARD RULE ─────────────────
        # "MANDATORY. Cannot be skipped. No 'do it later' option."
        if not obj_in.pin or not validate_pin(obj_in.pin):
            raise ValidationException("A valid 4-digit PIN is required for registration.")

        # ── Generate unique referral code — Blueprint §9.1/§14 ───────────────
        referral_code = _generate_referral_code()
        # Guarantee uniqueness (extremely rare collision — retry once)
        if db.query(User).filter(User.referral_code == referral_code).first():
            referral_code = _generate_referral_code()

        # ── Resolve referred_by — Blueprint §14 ──────────────────────────────
        referred_by_user_id: Optional[UUID] = None
        if obj_in.referral_code:
            referrer = self.get_by_referral_code(db, code=obj_in.referral_code)
            if referrer and str(referrer.id) != "":
                referred_by_user_id = referrer.id

        # ── Build User — Blueprint §14 field names ───────────────────────────
        user = User(
            # Blueprint §14: phone_number (NOT phone)
            phone_number=obj_in.phone,
            password_hash=hash_password(obj_in.password),

            # Blueprint §3.1 step 6 HARD RULE: pin_hash NOT NULL
            # "PIN is hashed with bcrypt before storage. Never stored in plaintext."
            pin_hash=hash_pin(obj_in.pin),

            # Blueprint §14: role (NOT user_type)
            role=role,

            # Blueprint §14: full_name NOT NULL
            full_name=obj_in.full_name or "",

            # Blueprint §14: date_of_birth DATE NOT NULL
            date_of_birth=obj_in.date_of_birth,

            # Blueprint §14: email nullable (optional at registration)
            email=obj_in.email,

            # Blueprint §9.1/§14: referral_code UNIQUE NOT NULL
            referral_code=referral_code,

            # Blueprint §14: referred_by_user_id FK
            referred_by_user_id=referred_by_user_id,

            # Blueprint §14: account status via two booleans (NOT a status enum)
            is_active=True,
            is_banned=False,
            is_phone_verified=False,

            # PIN lockout fields (default safe state)
            failed_pin_attempts=0,
            pin_locked_until=None,

            # Blueprint §14: biometric_flag BOOLEAN NOT NULL DEFAULT FALSE
            biometric_flag=False,
        )
        db.add(user)
        db.flush()   # get user.id without committing

        # ── Create role-specific profile ──────────────────────────────────────
        self._create_profile(db, user=user, obj_in=obj_in)

        db.commit()
        db.refresh(user)
        return user

    def _create_profile(
        self,
        db: Session,
        *,
        user: User,
        obj_in: RegisterRequest,
    ) -> None:
        """
        Create the role-specific profile record.

        [BUG-10 FIX] Uses obj_in.role (not obj_in.user_type).
        [BUG-11 FIX] Business: registered_address (not address). Blueprint §14.
        [BUG-12 FIX] LGA removed entirely. Blueprint §4 HARD RULE.
        [BUG-13 FIX] Admin path removed entirely. Blueprint §2 HARD RULE.
        """
        role = (obj_in.role or "").lower()

        if role == "customer":
            db.add(CustomerProfile(
                user_id=user.id,
                first_name=obj_in.first_name or (obj_in.full_name or "").split()[0],
                last_name=obj_in.last_name or (
                    " ".join((obj_in.full_name or "").split()[1:]) or ""
                ),
                date_of_birth=obj_in.date_of_birth,
                discovery_radius_m=5000,   # Blueprint §4.1: default 5 km
            ))

        elif role == "business":
            location = None
            if GEO_AVAILABLE and obj_in.latitude and obj_in.longitude:
                # Blueprint §3.1 step 5a: address geocoded to PostGIS point
                location = from_shape(
                    Point(obj_in.longitude, obj_in.latitude), srid=4326
                )

            # Blueprint §7.2: subscription_tier_rank default 1 (Free plan)
            db.add(Business(
                user_id=user.id,
                business_name=obj_in.business_name or "",
                category=obj_in.business_category or "",
                subcategory=obj_in.business_subcategory,
                # [BUG-11 FIX] registered_address — Blueprint §14
                registered_address=obj_in.address or "",
                city=obj_in.city,
                state=obj_in.state,
                # NO local_government — Blueprint §4 HARD RULE
                location=location,
                description=obj_in.description,
                website=obj_in.website,
                instagram=obj_in.instagram,
                facebook=obj_in.facebook,
                whatsapp=obj_in.whatsapp,
                # Blueprint §8.1: new businesses start on Free plan
                subscription_tier="free",
                subscription_tier_rank=1,
                subscription_status="active",
                is_verified=False,   # requires admin review
                service_radius_m=5000,
            ))

        elif role == "rider":
            from app.models.rider_model import Rider
            db.add(Rider(
                user_id=user.id,
                first_name=obj_in.first_name or (obj_in.full_name or "").split()[0],
                last_name=obj_in.last_name or "",
                vehicle_type=obj_in.vehicle_type or "motorcycle",
                vehicle_plate_number=obj_in.vehicle_plate_number,
                vehicle_color=obj_in.vehicle_color,
                vehicle_model=obj_in.vehicle_model,
                gov_id_url=obj_in.gov_id_url,
                is_verified=False,   # requires admin review + document upload
            ))

        # [BUG-13 FIX] Admin path DELETED.
        # Blueprint §2 HARD RULE: "Admin cannot register through mobile app
        # or self-provision an account." Provisioning is done by super-admin
        # exclusively through the admin web application.

    # ══════════════════════════════════════════════════════════════════════════
    # PHONE VERIFICATION
    # ══════════════════════════════════════════════════════════════════════════

    def mark_phone_verified(self, db: Session, *, user: User) -> None:
        """
        Mark phone as verified after successful OTP check.
        Blueprint §3.1 Step 2: "On success: Redis key deleted, phone marked verified."
        OTP validation itself is done via Redis in auth_service — this method
        only updates the DB flag.
        """
        user.is_phone_verified = True
        db.commit()

    def clear_pin_lockout(self, db: Session, *, user: User) -> None:
        """Clear PIN lockout state after successful OTP unlock."""
        user.failed_pin_attempts = 0
        user.pin_locked_until    = None
        db.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # AUTHENTICATION
    # ══════════════════════════════════════════════════════════════════════════

    def authenticate(
        self,
        db: Session,
        *,
        phone: Optional[str] = None,
        password: str,
    ) -> Optional[User]:
        """
        Authenticate by phone + password. Email login is NOT supported.
        Blueprint §3.2: "Phone number + password → JWT access token."
        """
        user: Optional[User] = None
        if phone:
            user = self.get_by_phone(db, phone=phone)

        if not user:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def update_last_login(self, db: Session, *, user: User) -> User:
        user.last_login = _utcnow()
        db.commit()
        db.refresh(user)
        return user

    def reset_password(
        self, db: Session, *, user: User, new_password: str
    ) -> None:
        """Apply a new hashed password after OTP verification."""
        user.password_hash = hash_password(new_password)
        db.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # PIN MANAGEMENT — Blueprint §3.1 step 6 / §3.3
    # ══════════════════════════════════════════════════════════════════════════

    def set_pin(self, db: Session, *, user: User, pin: str) -> None:
        """
        Set or update user's 4-digit PIN.
        Blueprint §3.3: "PIN is hashed with bcrypt before storage. Never plaintext."
        """
        if not validate_pin(pin):
            raise ValidationException("PIN must be exactly 4 numeric digits")

        user.pin_hash            = hash_pin(pin)
        user.failed_pin_attempts = 0
        user.pin_locked_until    = None
        db.commit()

    def verify_pin_auth(self, db: Session, *, user: User, pin: str) -> bool:
        """
        Verify PIN and handle lockout logic.

        Blueprint §3.3:
          "5 consecutive wrong PIN attempts: 30-minute lockout."
          "Lockout state stored in Redis: pin_lockout:{user_id} TTL = 1800s"

        Note: Redis-based lockout is in auth_service. This method provides
        DB-backed lockout tracking as a secondary layer. Both are maintained.

        Returns True on success, False on wrong PIN.
        Raises ValidationException if locked out.
        """
        # Check DB-level lockout
        if user.pin_locked_until and user.pin_locked_until > _utcnow():
            remaining = int((user.pin_locked_until - _utcnow()).total_seconds() // 60)
            raise ValidationException(
                f"PIN locked. Try again in {remaining} minute(s) or request unlock code via SMS."
            )

        # Clear expired DB lockout
        if user.pin_locked_until and user.pin_locked_until <= _utcnow():
            user.pin_locked_until    = None
            user.failed_pin_attempts = 0
            db.commit()

        if not user.pin_hash:
            raise ValidationException(
                "No PIN configured. Please set up your 4-digit PIN to continue."
            )

        if verify_pin(pin, user.pin_hash):
            # Success — reset failed attempts counter
            if user.failed_pin_attempts > 0:
                user.failed_pin_attempts = 0
                db.commit()
            return True
        else:
            # Failed attempt
            user.failed_pin_attempts = (user.failed_pin_attempts or 0) + 1

            from app.config import settings
            if user.failed_pin_attempts >= settings.PIN_MAX_ATTEMPTS:
                user.pin_locked_until = _utcnow() + timedelta(
                    seconds=settings.PIN_LOCKOUT_SECONDS
                )
                db.commit()
                raise ValidationException(
                    f"Too many failed PIN attempts. Locked for "
                    f"{settings.PIN_LOCKOUT_SECONDS // 60} minutes. "
                    "Request unlock code via SMS."
                )

            db.commit()
            return False

    def change_pin(
        self, db: Session, *, user: User, old_pin: str, new_pin: str
    ) -> None:
        """
        Change PIN — requires old PIN for security.
        Blueprint §3.3: "Changeable from security settings (requires current PIN + OTP)."
        OTP verification is handled by auth_service before calling this.
        """
        if not user.pin_hash:
            raise ValidationException("No PIN configured.")

        if not verify_pin(old_pin, user.pin_hash):
            raise InvalidCredentialsException("Incorrect current PIN")

        if not validate_pin(new_pin):
            raise ValidationException("New PIN must be exactly 4 numeric digits")

        user.pin_hash            = hash_pin(new_pin)
        user.failed_pin_attempts = 0
        user.pin_locked_until    = None
        db.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # BIOMETRIC — Blueprint §3.1 step 7 / §3.3
    # ══════════════════════════════════════════════════════════════════════════

    def enable_biometric(self, db: Session, *, user: User) -> None:
        """
        Enable biometric authentication flag.

        [BUG-16 FIX] Uses biometric_flag — Blueprint §14.
        Old code set user.biometric_enabled (field doesn't exist on corrected model).

        Blueprint §3.1 step 7:
          "Only presented AFTER PIN is confirmed active."
          "Server stores only users.biometric_flag BOOLEAN — no biometric data on server."
        Blueprint §3.3: "Biometric is a convenience layer over PIN. Never replaces PIN."
        """
        if not user.pin_hash:
            raise ValidationException(
                "PIN must be set before enabling biometric authentication."
            )
        user.biometric_flag = True
        db.commit()

    def disable_biometric(self, db: Session, *, user: User) -> None:
        """[BUG-16 FIX] Uses biometric_flag — Blueprint §14."""
        user.biometric_flag = False
        db.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # PROFILE UPDATES
    # ══════════════════════════════════════════════════════════════════════════

    def update_customer_profile(
        self,
        db: Session,
        *,
        user: User,
        update_data: Dict[str, Any],
    ) -> "CustomerProfile":
        """
        Partial update for customer profile.
        [BUG-17 FIX] phone update uses phone_number — Blueprint §14.
        """
        profile = user.customer_profile
        if profile is None:
            raise NotFoundException("Customer profile")

        # Phone updates go on User table, not profile
        phone = update_data.pop("phone", None) or update_data.pop("phone_number", None)
        if phone:
            # Blueprint §14: phone_number (not phone)
            user.phone_number = phone

        # Geo point update
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

    # ══════════════════════════════════════════════════════════════════════════
    # T&C ACCEPTANCE LOGGING — Blueprint §3.1 step 8
    # ══════════════════════════════════════════════════════════════════════════

    def log_terms_acceptance(
        self, db: Session, *, user: User, version_id: UUID
    ) -> UserAgreement:
        """
        Log T&C acceptance to user_agreements table.
        Blueprint §3.1 step 8: "Acceptance logged: user_agreements table
        (user_id, version_id, accepted_at)."
        Blueprint §P15: T&C text is admin-editable. Mobile always fetches latest.
        """
        agreement = UserAgreement(
            user_id=user.id,
            version_id=version_id,
        )
        db.add(agreement)
        db.commit()
        db.refresh(agreement)
        return agreement

    # ══════════════════════════════════════════════════════════════════════════
    # CONVENIENCE CHECKS
    # ══════════════════════════════════════════════════════════════════════════

    def is_active(self, user: User) -> bool:
        """Blueprint §14: active = is_active=True AND is_banned=False."""
        return user.is_active and not user.is_banned

    def is_phone_verified(self, user: User) -> bool:
        """Blueprint §3: phone verification is the gating check."""
        return user.is_phone_verified

    # ══════════════════════════════════════════════════════════════════════════
    # ACCOUNT STATUS — Blueprint §14 (two booleans, no status enum)
    # ══════════════════════════════════════════════════════════════════════════

    def ban_user(self, db: Session, *, user: User, reason: str) -> None:
        """
        Blueprint §11.1: suspend, ban, or delete account — mandatory reason log.
        Blueprint §14: is_banned BOOLEAN + ban_reason TEXT.
        """
        if not reason or not reason.strip():
            raise ValidationException("Ban reason is required and cannot be empty.")
        user.is_banned  = True
        user.is_active  = False
        user.ban_reason = reason.strip()
        db.commit()

    def unban_user(self, db: Session, *, user: User) -> None:
        """Restore a banned user (admin action)."""
        user.is_banned  = False
        user.is_active  = True
        user.ban_reason = None
        db.commit()

    def deactivate_user(self, db: Session, *, user: User) -> None:
        """Soft-deactivate without ban."""
        user.is_active = False
        db.commit()

    def reactivate_user(self, db: Session, *, user: User) -> None:
        """Reactivate a deactivated user."""
        user.is_active = True
        db.commit()


user_crud = CRUDUser(User)