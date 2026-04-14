from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import PostgresDsn, RedisDsn, field_validator
from typing import List, Optional, Union


class Settings(BaseSettings):
    """Application configuration with environment validation"""

    # Application
    APP_NAME: str = "Localy"
    APP_ENV: str = "development"
    DEBUG: bool = False
    API_VERSION: str = "v1"
    API_PREFIX: str = "/api"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: PostgresDsn
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_ECHO: bool = False

    # Redis
    REDIS_URL: RedisDsn
    REDIS_CACHE_EXPIRE_SECONDS: int = 3600

    # FIX: SECRET_KEY must NOT have a default — a new random value on every
    # restart invalidates every JWT issued before the restart.
    # Provide a stable, long value in .env (generate once with:
    #   python -c "import secrets; print(secrets.token_urlsafe(64))"  )
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    PASSWORD_MIN_LENGTH: int = 8

    # CORS
    ALLOWED_ORIGINS: Union[str, List[str]] = "http://localhost:3000,http://localhost:19006"

    # File Storage (Cloudinary)
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # Email (Resend)
    RESEND_API_KEY: str
    FROM_EMAIL: str = "noreply@localy.ng"
    FROM_NAME: str = "Localy"

    # SMS (Termii)
    TERMII_API_KEY: str
    TERMII_SENDER_ID: str = "Localy"
    TERMII_API_URL: str = "https://api.ng.termii.com/api"

    # OAuth
    GOOGLE_CLIENT_ID: str
    APPLE_APP_BUNDLE_ID: str

    # Payment (Paystack)
    PAYSTACK_SECRET_KEY: str
    PAYSTACK_PUBLIC_KEY: str
    PAYSTACK_CALLBACK_URL: Optional[str] = None

    # Location & Search
    DEFAULT_LOCATION_LAT: float = 9.0765   # Abuja, Nigeria
    DEFAULT_LOCATION_LNG: float = 7.3986
    DEFAULT_SEARCH_RADIUS_KM: float = 10.0
    MAX_SEARCH_RADIUS_KM: float = 50.0

    # File Upload
    MAX_FILE_SIZE_MB: int = 10
    MAX_VIDEO_SIZE_MB: int = 50
    ALLOWED_IMAGE_TYPES: Union[str, List[str]] = "image/jpeg,image/png,image/webp"
    ALLOWED_VIDEO_TYPES: Union[str, List[str]] = "video/mp4,video/webm"
    ALLOWED_DOCUMENT_TYPES: Union[str, List[str]] = "application/pdf"

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000

    # Celery
    CELERY_BROKER_URL: RedisDsn
    CELERY_RESULT_BACKEND: RedisDsn

    # Subscription Plans (NGN)
    STARTER_MONTHLY_PRICE: float = 5500.0
    STARTER_ANNUAL_PRICE: float = 55000.0
    PRO_MONTHLY_PRICE: float = 16500.0
    PRO_ANNUAL_PRICE: float = 165000.0
    ENTERPRISE_MONTHLY_PRICE: float = 55000.0
    ENTERPRISE_ANNUAL_PRICE: float = 550000.0
    PRO_DRIVER_MONTHLY_PRICE: float = 8500.0
    PRO_DRIVER_ANNUAL_PRICE: float = 85000.0

    # Wallet
    REFERRAL_BONUS_AMOUNT: float = 500.0
    WALLET_MIN_TOPUP: float = 500.0
    WALLET_MAX_BALANCE: float = 10_000_000.0
    WALLET_DAILY_FUNDING_LIMIT: float = 500_000.0

    # Monnify (virtual account / bank transfer top-ups)
    MONNIFY_API_KEY: str = ""
    MONNIFY_SECRET_KEY: str = ""
    MONNIFY_CONTRACT_CODE: str = ""
    MONNIFY_BASE_URL: str = "https://sandbox.monnify.com/api/v1"

    # NOWPayments (crypto top-ups)
    NOWPAYMENTS_API_KEY: str = ""
    NOWPAYMENTS_IPN_SECRET: str = ""
    NOWPAYMENTS_IPN_CALLBACK_URL: Optional[str] = None
    NOWPAYMENTS_SANDBOX: str = "true"   # "true" = sandbox, "false" = production

    # Business Rules (Blueprint)
    LOCAL_GOVERNMENT_RESTRICTION: bool = True
    STORY_EXPIRE_HOURS: int = 24
    REEL_MAX_DURATION_SECONDS: int = 60

    # Pagination
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Convenience helpers parsed from REDIS_URL                           #
    # (used by cache.py which needs host/port/password/db individually)   #
    # ------------------------------------------------------------------ #

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
        # Path is "/0", "/1", etc.
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


# Singleton
settings = Settings()


def is_production() -> bool:
    return settings.APP_ENV == "production"


def is_development() -> bool:
    return settings.APP_ENV == "development"


def is_testing() -> bool:
    return settings.APP_ENV == "testing"