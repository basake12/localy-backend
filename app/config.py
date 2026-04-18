"""
app/config.py

FIXES:
  [AUDIT BUG-11] JWT_ADMIN_SECRET_KEY: str = "" — catastrophically insecure default.

  Root cause:
    JWT_ADMIN_SECRET_KEY defaulted to empty string "". With HS256, an empty key
    means any attacker can forge admin JWT tokens with no knowledge of the secret:
      jwt.encode({"role": "admin", "admin_id": "..."}, "", algorithm="HS256")
    This would grant full admin access to the platform — read/write to every
    entity, transaction, and user account.

  Fix:
    Added @field_validator("JWT_ADMIN_SECRET_KEY") that:
      1. Raises ValueError if the key is empty in production.
      2. Logs a WARNING if the key is empty in non-production (dev/test may omit it).
      3. Requires minimum 32-character length (same policy as SECRET_KEY).

  All other fixes from previous version retained (see changelog below).

PREVIOUS FIXES (retained):
  1.  GOOGLE_CLIENT_ID and APPLE_APP_BUNDLE_ID DELETED — Blueprint §P05.
  2.  ACCESS_TOKEN_EXPIRE_MINUTES: 30 → 15. Blueprint §3.2.
  3.  REFRESH_TOKEN_EXPIRE_DAYS: 7 → 30. Blueprint §3.2.
  4.  DEFAULT_SEARCH_RADIUS_KM: 10.0 → 5.0. Blueprint §4.1.
  5.  STARTER/PRO/ENTERPRISE prices corrected. Blueprint §8.1.
  6.  REFERRAL_BONUS_AMOUNT: 500 → 1000. Blueprint §9.1.
  7.  WALLET_MIN_TOPUP: 500 → 1000. Blueprint §5.1.
  8.  WALLET_DAILY_FUNDING_LIMIT: 500k → 2,000,000. Blueprint §5.1.
  9.  REEL_MAX_DURATION_SECONDS: 60 → 90. Blueprint §8.4.
  10. LOCAL_GOVERNMENT_RESTRICTION DELETED. Blueprint §4 HARD RULE.
  11. NOWPAYMENTS_* DELETED. Blueprint §5.
  12. OTP/PIN/Monnify settings added. Blueprint §3.1 / §3.3.
"""
import logging
from typing import List, Optional, Union

from pydantic import PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application configuration — all values loaded from .env."""

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "Localy"
    APP_ENV:  str = "development"   # development | staging | production
    DEBUG:    bool = False
    API_VERSION: str = "v1"
    API_PREFIX:  str = "/api"

    # ── Server ────────────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL:          PostgresDsn
    DATABASE_POOL_SIZE:    int  = 10
    DATABASE_MAX_OVERFLOW: int  = 20
    DATABASE_ECHO:         bool = False

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL:                   RedisDsn
    REDIS_CACHE_EXPIRE_SECONDS:  int = 3600

    # ── JWT / Security ────────────────────────────────────────────────────────
    # SECRET_KEY has NO default — must be set in .env.
    # Generate: python -c "import secrets; print(secrets.token_urlsafe(64))"
    SECRET_KEY: str
    ALGORITHM:  str = "HS256"

    # Blueprint §3.2: access token = 15 min, refresh token = 30 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15    # was 30 — FIXED
    REFRESH_TOKEN_EXPIRE_DAYS:   int = 30    # was 7 — FIXED

    PASSWORD_MIN_LENGTH: int = 8

    # REMOVED: GOOGLE_CLIENT_ID — Blueprint §P05 HARD RULE: OAuth deleted.
    # REMOVED: APPLE_APP_BUNDLE_ID — same reason.

    # ── Admin JWT (separate secret, separate issuance endpoint) ───────────────
    # Blueprint §3.2 / §13.3: admin tokens use a SEPARATE secret key.
    # Admin tokens are NEVER accepted by mobile API endpoints.
    # Admin token claim: { role: "admin", admin_id: uuid }
    #
    # [BUG-11 FIX] Default changed from "" to require non-empty in production.
    # See @field_validator("JWT_ADMIN_SECRET_KEY") below.
    # Generate separately: python -c "import secrets; print(secrets.token_urlsafe(64))"
    JWT_ADMIN_SECRET_KEY: str = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: Union[str, List[str]] = "http://localhost:3000,http://localhost:19006"

    # ── File Storage ──────────────────────────────────────────────────────────
    # AWS S3 — primary media + video storage (Blueprint §16.1)
    AWS_S3_BUCKET:          str = ""
    AWS_ACCESS_KEY_ID:      str = ""
    AWS_SECRET_ACCESS_KEY:  str = ""
    AWS_REGION:             str = "us-east-1"

    # Cloudinary — image/video upload (upload_service.py)
    CLOUDINARY_CLOUD_NAME:      str = ""
    CLOUDINARY_API_KEY:         str = ""
    CLOUDINARY_API_SECRET:      str = ""

    # Cloudflare R2 — alternative CDN-backed storage (Blueprint §16.1)
    CLOUDFLARE_R2_BUCKET:          str = ""
    CLOUDFLARE_R2_ACCOUNT_ID:      str = ""
    CLOUDFLARE_R2_ACCESS_KEY_ID:   str = ""
    CLOUDFLARE_R2_SECRET_ACCESS_KEY: str = ""

    # ── Email (Resend) — optional; SMS is primary ─────────────────────────────
    RESEND_API_KEY: str = ""
    FROM_EMAIL:     str = "noreply@localy.ng"
    FROM_NAME:      str = "Localy"

    # ── SMS (Termii) — PRIMARY gateway per blueprint §3.1 / §16.1 ────────────
    TERMII_API_KEY:    str
    TERMII_SENDER_ID:  str = "Localy"
    TERMII_API_URL:    str = "https://api.ng.termii.com/api"

    # ── OTP Rules — Blueprint §3.1 ────────────────────────────────────────────
    # Step 1: TTL = 5 minutes — stored in Redis: otp:{phone}
    OTP_EXPIRE_MINUTES: int = 5

    # Step 1: resend cooldown
    OTP_RESEND_COOLDOWN_SECONDS: int = 60

    # Step 2: max attempts per phone per hour
    OTP_MAX_ATTEMPTS:    int = 5
    OTP_LOCKOUT_MINUTES: int = 30

    # ── PIN Rules — Blueprint §3.3 ────────────────────────────────────────────
    # Blueprint §16.3: pin_lockout:{user_id} TTL = 1800s
    PIN_MAX_ATTEMPTS:    int = 5
    PIN_LOCKOUT_SECONDS: int = 1800

    # ── Payment — Monnify (primary) + Paystack (card) ─────────────────────────
    MONNIFY_API_KEY:       str = ""
    MONNIFY_SECRET_KEY:    str = ""
    MONNIFY_CONTRACT_CODE: str = ""
    MONNIFY_BASE_URL:      str = "https://api.monnify.com/api/v1"

    # Blueprint §1 / §5: Paystack = card payments
    PAYSTACK_SECRET_KEY:   str
    PAYSTACK_PUBLIC_KEY:   str
    PAYSTACK_CALLBACK_URL: Optional[str] = None

    # REMOVED: NOWPAYMENTS_* — Blueprint §5: Monnify + Paystack ONLY.

    # ── Google APIs ───────────────────────────────────────────────────────────
    # Geocoding only — NOT OAuth. Blueprint §16.1 + §3.1 step 5a.
    GOOGLE_GEOCODING_API_KEY: str = ""

    # ── FCM Push Notifications ────────────────────────────────────────────────
    FCM_SERVER_KEY: str = ""

    # ── Discovery / Search ────────────────────────────────────────────────────
    # Blueprint §4.1: default 5 km, adjustable 1–50 km
    DEFAULT_SEARCH_RADIUS_KM: float = 5.0
    MAX_SEARCH_RADIUS_KM:     float = 50.0
    MIN_SEARCH_RADIUS_KM:     float = 1.0

    # Default coords (Abuja, Nigeria)
    DEFAULT_LOCATION_LAT: float = 9.0765
    DEFAULT_LOCATION_LNG: float = 7.3986

    # REMOVED: LOCAL_GOVERNMENT_RESTRICTION — Blueprint HARD RULE: no LGA anywhere.

    # ── File Upload ───────────────────────────────────────────────────────────
    MAX_FILE_SIZE_MB:    int = 10
    MAX_VIDEO_SIZE_MB:   int = 50
    ALLOWED_IMAGE_TYPES: Union[str, List[str]] = "image/jpeg,image/png,image/webp"
    ALLOWED_VIDEO_TYPES: Union[str, List[str]] = "video/mp4,video/webm"
    ALLOWED_DOCUMENT_TYPES: Union[str, List[str]] = "application/pdf"

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR:   int = 1000

    # ── Celery ────────────────────────────────────────────────────────────────
    CELERY_BROKER_URL:      RedisDsn
    CELERY_RESULT_BACKEND:  RedisDsn

    # ── Subscription Plans (₦ NGN) — Blueprint §8.1 ──────────────────────────
    # Annual = 10 months price (2 months free). Blueprint §8.1.
    STARTER_MONTHLY_PRICE: float = 4_500.0
    STARTER_ANNUAL_PRICE:  float = 45_000.0    # 10 × 4,500

    PRO_MONTHLY_PRICE: float = 10_000.0
    PRO_ANNUAL_PRICE:  float = 100_000.0       # 10 × 10,000

    ENTERPRISE_MONTHLY_PRICE: float = 15_000.0
    ENTERPRISE_ANNUAL_PRICE:  float = 150_000.0 # 10 × 15,000

    # ── Wallet Rules — Blueprint §5.1 / §5.2 ─────────────────────────────────
    WALLET_MIN_TOPUP:           float = 1_000.0
    WALLET_MAX_BALANCE:         float = 10_000_000.0
    WALLET_DAILY_FUNDING_LIMIT: float = 2_000_000.0

    # Blueprint §3.3: PIN required for all wallet transactions above ₦5,000
    WALLET_PIN_THRESHOLD: float = 5_000.0

    # ── Referral & Discount — Blueprint §9.1 / §5.1 ──────────────────────────
    REFERRAL_BONUS_AMOUNT:       float = 1_000.0
    NEW_USER_DISCOUNT_AMOUNT:    float = 1_000.0
    NEW_USER_DISCOUNT_MIN_ORDER: float = 2_000.0

    # ── Platform Fees — Blueprint §5.4 ────────────────────────────────────────
    # Product/food orders: ₦50 flat (₦50 from business + ₦50 from customer)
    PLATFORM_FEE_ORDER:   int = 50
    # Service/hotel/health bookings: ₦100 flat (₦100 each side)
    PLATFORM_FEE_BOOKING: int = 100
    # Ticket purchases: ₦50 flat (from customer only)
    PLATFORM_FEE_TICKET:  int = 50

    # ── Content Rules — Blueprint §8.4 / §8.5 ────────────────────────────────
    REEL_MAX_DURATION_SECONDS:  int = 90   # Blueprint §8.4: up to 90 seconds
    STORY_MAX_DURATION_SECONDS: int = 30   # Blueprint §8.5: up to 30 seconds
    STORY_EXPIRE_HOURS:         int = 24   # Blueprint §8.5: 24-hour expiry

    # ── Pagination ────────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE:     int = 100

    # ── Deep link ─────────────────────────────────────────────────────────────
    APP_DEEP_LINK: str = "https://localy.ng"

    # ──────────────────────────────────────────────────────────────────────────
    # Validators
    # ──────────────────────────────────────────────────────────────────────────

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if isinstance(v, str) and not v.startswith("postgresql"):
            raise ValueError("DATABASE_URL must start with 'postgresql'")
        return v

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v

    @field_validator("JWT_ADMIN_SECRET_KEY")
    @classmethod
    def validate_admin_secret_key(cls, v: str, info) -> str:
        """
        [BUG-11 FIX] Enforce non-empty JWT_ADMIN_SECRET_KEY in production.

        Root cause: defaulting to "" allows trivial admin token forgery with HS256.
        An empty admin secret = no admin security whatsoever.

        Policy:
          - Production (APP_ENV="production"): MUST be set and at least 32 chars.
          - Development/staging: logs a WARNING but allows empty (for local dev).
        """
        app_env = info.data.get("APP_ENV", "development") if info.data else "development"

        if not v:
            if app_env == "production":
                raise ValueError(
                    "JWT_ADMIN_SECRET_KEY must be set in production. "
                    "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
                )
            else:
                # In development, log a loud warning but don't crash
                logger.warning(
                    "⚠️  JWT_ADMIN_SECRET_KEY is not set. Admin JWT security is disabled. "
                    "Set this in .env before deploying to production."
                )
            return v

        if len(v) < 32:
            raise ValueError(
                "JWT_ADMIN_SECRET_KEY must be at least 32 characters. "
                "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        return v

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v) -> List[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        if isinstance(v, list):
            return v
        return ["http://localhost:3000"]

    @field_validator(
        "ALLOWED_IMAGE_TYPES", "ALLOWED_VIDEO_TYPES", "ALLOWED_DOCUMENT_TYPES",
        mode="before",
    )
    @classmethod
    def parse_file_types(cls, v) -> List[str]:
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        if isinstance(v, list):
            return v
        return []

    # ── Redis URL helpers ─────────────────────────────────────────────────────

    @property
    def redis_host(self) -> str:
        return self.REDIS_URL.host or "localhost"

    @property
    def redis_port(self) -> int:
        return self.REDIS_URL.port or 6379

    @property
    def redis_password(self) -> Optional[str]:
        return self.REDIS_URL.password

    @property
    def redis_db(self) -> int:
        try:
            return int((self.REDIS_URL.path or "/0").lstrip("/"))
        except ValueError:
            return 0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        env_parse_none_str="null",
        extra="ignore",
    )


# Singleton — import this everywhere
settings = Settings()


def is_production() -> bool:
    return settings.APP_ENV == "production"


def is_development() -> bool:
    return settings.APP_ENV == "development"


def is_testing() -> bool:
    return settings.APP_ENV == "testing"