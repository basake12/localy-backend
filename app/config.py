"""
app/config.py

FIXES vs previous version:
  1.  [HARD RULE] GOOGLE_CLIENT_ID and APPLE_APP_BUNDLE_ID DELETED.
      Blueprint §P05: "Google Sign-In and Apple Sign-In are removed from all
      authentication flows. Phone number and password only."

  2.  ACCESS_TOKEN_EXPIRE_MINUTES: 30 → 15.
      Blueprint §3.2: "JWT access token (15 min)".

  3.  REFRESH_TOKEN_EXPIRE_DAYS: 7 → 30.
      Blueprint §3.2: "refresh token (30 days)".

  4.  DEFAULT_SEARCH_RADIUS_KM: 10.0 → 5.0.
      Blueprint §4.1: "Default radius: 5 km from device GPS position."

  5.  STARTER_MONTHLY_PRICE: 5500.0 → 4500.0. Blueprint §8.1.
      PRO_MONTHLY_PRICE: 16500.0 → 10000.0. Blueprint §8.1.
      ENTERPRISE_MONTHLY_PRICE: 55000.0 → 15000.0. Blueprint §8.1.

  6.  REFERRAL_BONUS_AMOUNT: 500.0 → 1000.0.
      Blueprint §9.1: "Referrer reward: ₦1,000."

  7.  WALLET_MIN_TOPUP: 500.0 → 1000.0.
      Blueprint §5.1: "Minimum top-up: ₦1,000."

  8.  WALLET_DAILY_FUNDING_LIMIT: 500_000.0 → 2_000_000.0.
      Blueprint §5.1: "Daily funding: ₦2,000,000."

  9.  REEL_MAX_DURATION_SECONDS: 60 → 90.
      Blueprint §8.4: "up to 90 seconds."

  10. [HARD RULE] LOCAL_GOVERNMENT_RESTRICTION DELETED.
      Blueprint §4: "No LGA logic anywhere in the codebase."

  11. NOWPAYMENTS_* settings DELETED.
      Blueprint §5: Monnify + Paystack ONLY. No crypto payments.

  12. OTP_EXPIRE_MINUTES = 5 added.
      Blueprint §3.1 Step 1: "OTP is 6-digit, TTL = 5 minutes."

  13. OTP_MAX_ATTEMPTS = 5, OTP_LOCKOUT_MINUTES = 30 added.
      Blueprint §3.1 Step 2.

  14. OTP_RESEND_COOLDOWN_SECONDS = 60 added.
      Blueprint §3.1 Step 1: "Resend available after 60 seconds."

  15. PIN_MAX_ATTEMPTS = 5, PIN_LOCKOUT_SECONDS = 1800 added.
      Blueprint §3.3: "5 consecutive wrong PIN attempts: 30-minute lockout."

  16. Monnify settings updated — MONNIFY_BASE_URL points to production.
      Blueprint §5: Monnify is primary bank transfer provider.

  17. NEW_USER_DISCOUNT_AMOUNT = 1000.0 added. Blueprint §5.1.
      NEW_USER_DISCOUNT_MIN_ORDER = 2000.0 added. Blueprint §5.1.

  18. Subscription annual prices corrected:
      Annual = 10 months price (2 months free). Blueprint §8.1.
"""
from typing import List, Optional, Union

from pydantic import PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Generate once: python -c "import secrets; print(secrets.token_urlsafe(64))"
    SECRET_KEY: str
    ALGORITHM:  str = "HS256"

    # Blueprint §3.2: access token = 15 min, refresh token = 30 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15    # was 30 — FIXED
    REFRESH_TOKEN_EXPIRE_DAYS:   int = 30    # was 7 — FIXED

    PASSWORD_MIN_LENGTH: int = 8

    # REMOVED: GOOGLE_CLIENT_ID — Blueprint §P05 HARD RULE: OAuth deleted.
    # REMOVED: APPLE_APP_BUNDLE_ID — same reason.

    # ── CORS ──────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: Union[str, List[str]] = "http://localhost:3000,http://localhost:19006"

    # ── File Storage ──────────────────────────────────────────────────────────
    # AWS S3 — primary media + video storage (Blueprint §16.1)
    AWS_S3_BUCKET:          str = ""
    AWS_ACCESS_KEY_ID:      str = ""
    AWS_SECRET_ACCESS_KEY:  str = ""
    AWS_REGION:             str = "us-east-1"

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
    # Step 1: TTL = 5 minutes
    OTP_EXPIRE_MINUTES: int = 5            # Redis TTL for otp:{phone}

    # Step 1: resend cooldown
    OTP_RESEND_COOLDOWN_SECONDS: int = 60  # minimum gap between OTP sends

    # Step 2: max attempts per phone per hour
    OTP_MAX_ATTEMPTS:    int = 5
    OTP_LOCKOUT_MINUTES: int = 30          # phone locked after 5 failures

    # ── PIN Rules — Blueprint §3.3 ────────────────────────────────────────────
    PIN_MAX_ATTEMPTS:    int = 5           # consecutive wrong attempts before lockout
    PIN_LOCKOUT_SECONDS: int = 1800        # 30 minutes — Redis TTL for pin_lockout:{user_id}

    # ── Payment — Monnify (primary) + Paystack (card) ─────────────────────────
    # Blueprint §1 / §5 / §16.1: Monnify = bank transfer + virtual accounts
    MONNIFY_API_KEY:       str = ""
    MONNIFY_SECRET_KEY:    str = ""        # HMAC-SHA512 webhook signature key
    MONNIFY_CONTRACT_CODE: str = ""
    MONNIFY_BASE_URL:      str = "https://api.monnify.com/api/v1"  # production

    # Blueprint §1 / §5: Paystack = card payments
    PAYSTACK_SECRET_KEY:   str
    PAYSTACK_PUBLIC_KEY:   str
    PAYSTACK_CALLBACK_URL: Optional[str] = None

    # REMOVED: NOWPAYMENTS_* — Blueprint §5 specifies Monnify + Paystack ONLY.
    # Crypto top-up is not in the blueprint.

    # ── Google APIs ───────────────────────────────────────────────────────────
    # Geocoding only — NOT OAuth. Blueprint §16.1 + §3.1 step 5a.
    GOOGLE_GEOCODING_API_KEY: str = ""

    # ── FCM Push Notifications ────────────────────────────────────────────────
    FCM_SERVER_KEY: str = ""               # Blueprint §16.1

    # ── Admin JWT (separate from mobile JWT) ──────────────────────────────────
    # Blueprint §3.2 / §13.3: admin tokens use a SEPARATE secret.
    # They are never accepted by mobile API endpoints.
    JWT_ADMIN_SECRET_KEY: str = ""

    # ── Discovery / Search ────────────────────────────────────────────────────
    # Blueprint §4.1: default 5 km, adjustable 1–50 km
    DEFAULT_SEARCH_RADIUS_KM: float = 5.0    # was 10.0 — FIXED
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
    STARTER_MONTHLY_PRICE: float = 4_500.0     # was 5500 — FIXED
    STARTER_ANNUAL_PRICE:  float = 45_000.0    # 10 × 4500

    PRO_MONTHLY_PRICE: float = 10_000.0        # was 16500 — FIXED
    PRO_ANNUAL_PRICE:  float = 100_000.0       # 10 × 10000

    ENTERPRISE_MONTHLY_PRICE: float = 15_000.0  # was 55000 — FIXED
    ENTERPRISE_ANNUAL_PRICE:  float = 150_000.0 # 10 × 15000

    # ── Wallet Rules — Blueprint §5.1 ─────────────────────────────────────────
    WALLET_MIN_TOPUP:         float = 1_000.0        # was 500 — FIXED
    WALLET_MAX_BALANCE:       float = 10_000_000.0
    WALLET_DAILY_FUNDING_LIMIT: float = 2_000_000.0  # was 500k — FIXED

    # Blueprint §3.3: PIN required for wallet transactions above ₦5,000
    WALLET_PIN_THRESHOLD: float = 5_000.0

    # ── Referral & Discount — Blueprint §9.1 / §5.1 ──────────────────────────
    REFERRAL_BONUS_AMOUNT:       float = 1_000.0   # was 500 — FIXED
    NEW_USER_DISCOUNT_AMOUNT:    float = 1_000.0   # Blueprint §5.1
    NEW_USER_DISCOUNT_MIN_ORDER: float = 2_000.0   # Blueprint §5.1 + §9.1

    # ── Platform Fees — Blueprint §5.4 ────────────────────────────────────────
    PLATFORM_FEE_ORDER:   int = 50    # ₦50 for product/food orders (per side)
    PLATFORM_FEE_BOOKING: int = 100   # ₦100 for service/hotel/health (per side)
    PLATFORM_FEE_TICKET:  int = 50    # ₦50 per ticket (customer only)

    # ── Content Rules — Blueprint §8.4 / §8.5 ────────────────────────────────
    REEL_MAX_DURATION_SECONDS:  int = 90   # was 60 — FIXED. Blueprint §8.4.
    STORY_MAX_DURATION_SECONDS: int = 30   # Blueprint §8.5.
    STORY_EXPIRE_HOURS:         int = 24   # Blueprint §8.5.

    # ── Pagination ────────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE:     int = 100

    # ── Deep link (for email CTAs) ────────────────────────────────────────────
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