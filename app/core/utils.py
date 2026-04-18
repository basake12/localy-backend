"""
app/core/utils.py

Core utility functions for the application.

FIXES vs previous version:
  Blueprint §16.4 HARD RULE: "Always use datetime.now(timezone.utc).
  NEVER use datetime.utcnow() — it produces naive datetimes incompatible
  with PostgreSQL TIMESTAMPTZ columns."

  4 violations fixed:
    generate_reference_code  → datetime.utcnow().strftime(...)
    get_date_range           → end_date = datetime.utcnow()
    is_business_hours        → datetime.utcnow().hour
    get_expiry_date          → datetime.utcnow() + timedelta(days=days)
"""

import re
import secrets
import string
from datetime import datetime, timedelta, timezone    # timezone imported
from typing import Optional, Any, Dict
from decimal import Decimal
import hashlib
import json


# ── String Utilities ───────────────────────────────────────────────────────────

def generate_random_string(
    length: int = 32,
    include_digits: bool = True,
    include_special: bool = False,
) -> str:
    """Generate a cryptographically random string."""
    chars = string.ascii_letters
    if include_digits:
        chars += string.digits
    if include_special:
        chars += string.punctuation
    return "".join(secrets.choice(chars) for _ in range(length))


def generate_reference_code(prefix: str = "", length: int = 10) -> str:
    """
    Generate a unique reference code with a date prefix.
    Blueprint §16.4 FIX: was datetime.utcnow() — naive datetime.
    """
    random_part = "".join(
        secrets.choice(string.ascii_uppercase + string.digits)
        for _ in range(length)
    )
    # FIX §16.4: was datetime.utcnow().strftime(...)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}{timestamp}{random_part}" if prefix else f"{timestamp}{random_part}"


def slugify_text(text: str, max_length: int = 100) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    text = text.strip("-")
    return text[:max_length]


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate text to max length with suffix."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


# ── Phone Number Utilities ─────────────────────────────────────────────────────

def format_nigerian_phone(phone: str) -> str:
    """Format Nigerian phone number to international format (+234...)."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("234"):
        return f"+{digits}"
    elif digits.startswith("0"):
        return f"+234{digits[1:]}"
    else:
        return f"+234{digits}"


def validate_nigerian_phone(phone: str) -> bool:
    """Validate Nigerian phone number. Returns True if valid."""
    formatted = format_nigerian_phone(phone)
    return bool(re.match(r"^\+234\d{10}$", formatted))


# ── Money / Decimal Utilities ──────────────────────────────────────────────────

def money_to_kobo(amount: Decimal) -> int:
    """
    Convert Naira to kobo for payment processors (Paystack uses kobo).
    Blueprint §5.6: Paystack amounts are in kobo — divide by 100 before crediting.
    """
    return int(amount * 100)


def kobo_to_money(kobo: int) -> Decimal:
    """Convert kobo back to Naira. Blueprint §5.6: division by 100 at display layer."""
    return Decimal(kobo) / 100


def format_money(amount: Decimal, currency: str = "NGN") -> str:
    """Format money amount for display. Blueprint §1: currency is ₦ NGN."""
    symbols = {"NGN": "₦", "USD": "$", "GBP": "£", "EUR": "€"}
    symbol = symbols.get(currency, currency)
    return f"{symbol}{amount:,.2f}"


# ── Date / Time Utilities ──────────────────────────────────────────────────────

def get_date_range(days: int) -> tuple[datetime, datetime]:
    """
    Get a date range from now backwards by N days.
    Returns timezone-aware datetimes.
    Blueprint §16.4 FIX: was datetime.utcnow() — naive datetime.
    """
    # FIX §16.4: was datetime.utcnow()
    end_date   = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    return start_date, end_date


def is_business_hours(hour: int = None) -> bool:
    """
    Check if current time is within business hours (9 AM–6 PM WAT = UTC+1).
    Blueprint §16.4 FIX: was datetime.utcnow().hour — naive datetime.
    """
    if hour is None:
        # FIX §16.4: was datetime.utcnow().hour
        hour = datetime.now(timezone.utc).hour
    return 8 <= hour < 18  # UTC+1 offset for WAT approximated as UTC+0 offset 8-18


def get_expiry_date(days: int = 30) -> datetime:
    """
    Get a timezone-aware expiry datetime N days from now.
    Blueprint §16.4 FIX: was datetime.utcnow() — naive datetime incompatible
    with PostgreSQL TIMESTAMPTZ columns.
    """
    # FIX §16.4: was datetime.utcnow() + timedelta(days=days)
    return datetime.now(timezone.utc) + timedelta(days=days)


# ── Hashing Utilities ──────────────────────────────────────────────────────────

def generate_hash(data: str, algorithm: str = "sha256") -> str:
    """Generate hash of a string."""
    hash_obj = hashlib.new(algorithm)
    hash_obj.update(data.encode("utf-8"))
    return hash_obj.hexdigest()


def generate_file_hash(file_content: bytes) -> str:
    """Generate SHA-256 hash of file content for integrity verification."""
    return hashlib.sha256(file_content).hexdigest()


# ── Distance Calculations (Haversine fallback only) ────────────────────────────

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two coordinates using the Haversine formula.
    Returns distance in kilometres.

    IMPORTANT: This is a CPU-side FALLBACK only. All production geo discovery
    queries MUST use PostGIS ST_DWithin / ST_Distance for index-backed
    spatial filtering. Blueprint §4.3 + §13.2 [P11].
    """
    from math import radians, sin, cos, sqrt, atan2

    R = 6371.0  # Earth radius in kilometres

    lat1_r = radians(lat1)
    lon1_r = radians(lon1)
    lat2_r = radians(lat2)
    lon2_r = radians(lon2)

    dlon = lon2_r - lon1_r
    dlat = lat2_r - lat1_r

    a = sin(dlat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def is_within_radius(
    center_lat: float,
    center_lon: float,
    point_lat: float,
    point_lon: float,
    radius_km: float,
) -> bool:
    """
    Haversine fallback radius check. Use PostGIS ST_DWithin in all DB queries.
    Blueprint §4.3: ST_DWithin activates the GIST index — Haversine does not.
    """
    return calculate_distance(center_lat, center_lon, point_lat, point_lon) <= radius_km


# ── Pagination Utilities ───────────────────────────────────────────────────────

def calculate_pagination(total: int, page: int, page_size: int) -> Dict[str, Any]:
    """Calculate pagination metadata for API responses."""
    total_pages = (total + page_size - 1) // page_size
    return {
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": total_pages,
        "has_next":    page < total_pages,
        "has_prev":    page > 1,
    }


# ── JSON Utilities ─────────────────────────────────────────────────────────────

def safe_json_loads(data: str, default: Any = None) -> Any:
    """Safely parse JSON string. Returns default on error."""
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return default


def safe_json_dumps(data: Any, default: str = "{}") -> str:
    """Safely serialise to JSON string. Returns default on error."""
    try:
        return json.dumps(data)
    except (TypeError, ValueError):
        return default


# ── Percentage Calculations ────────────────────────────────────────────────────

def calculate_percentage(part: float, whole: float) -> float:
    """Calculate percentage. Returns 0.0 if whole is zero."""
    if whole == 0:
        return 0.0
    return (part / whole) * 100


def apply_percentage(amount: Decimal, percentage: float) -> Decimal:
    """Apply a percentage rate to an amount."""
    return amount * Decimal(str(percentage / 100))


def calculate_commission(amount: Decimal, rate: float) -> Decimal:
    """Calculate commission from amount at a given rate (%)."""
    return apply_percentage(amount, rate)


# ── Rating Calculations ────────────────────────────────────────────────────────

def calculate_new_average(
    current_avg: float,
    current_count: int,
    new_rating: float,
) -> float:
    """
    Incrementally calculate a new average rating after adding one review.
    Blueprint §7.2 factor 4: rating quality weighted by number of verified reviews.
    """
    total     = (current_avg * current_count) + new_rating
    new_count = current_count + 1
    return total / new_count if new_count > 0 else 0.0


# ── File Size Utilities ────────────────────────────────────────────────────────

def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable form (B, KB, MB, GB, TB)."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def validate_file_size(size_bytes: int, max_mb: int = 10) -> bool:
    """Return True if file is within the allowed size limit."""
    return size_bytes <= max_mb * 1024 * 1024


# ── Validation Utilities ───────────────────────────────────────────────────────

def is_valid_email(email: str) -> bool:
    """Validate basic email format."""
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email))


def is_valid_url(url: str) -> bool:
    """Validate URL format (http or https)."""
    return bool(re.match(r"^https?://[^\s/$.?#].[^\s]*$", url))


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename for safe object storage keys."""
    safe_name = re.sub(r"[^\w\s.-]", "", filename)
    return re.sub(r"\s+", "_", safe_name)