"""
Core utility functions for the application.
"""
import re
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, Any, Dict
from decimal import Decimal
import hashlib
import json


# ============================================
# STRING UTILITIES
# ============================================

def generate_random_string(length: int = 32, include_digits: bool = True, include_special: bool = False) -> str:
    """Generate a random string."""
    chars = string.ascii_letters
    if include_digits:
        chars += string.digits
    if include_special:
        chars += string.punctuation
    return ''.join(secrets.choice(chars) for _ in range(length))


def generate_reference_code(prefix: str = "", length: int = 10) -> str:
    """Generate a unique reference code."""
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))
    timestamp = datetime.utcnow().strftime("%Y%m%d")
    return f"{prefix}{timestamp}{random_part}" if prefix else f"{timestamp}{random_part}"


def slugify_text(text: str, max_length: int = 100) -> str:
    """Convert text to URL-friendly slug."""
    # Convert to lowercase
    text = text.lower()
    # Replace spaces and special chars with hyphens
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    # Remove leading/trailing hyphens
    text = text.strip('-')
    return text[:max_length]


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate text to max length."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


# ============================================
# PHONE NUMBER UTILITIES
# ============================================

def format_nigerian_phone(phone: str) -> str:
    """Format Nigerian phone number to international format."""
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', phone)

    # Handle different formats
    if digits.startswith('234'):
        return f'+{digits}'
    elif digits.startswith('0'):
        return f'+234{digits[1:]}'
    else:
        return f'+234{digits}'


def validate_nigerian_phone(phone: str) -> bool:
    """Validate Nigerian phone number."""
    formatted = format_nigerian_phone(phone)
    # Should be +234 followed by 10 digits
    return bool(re.match(r'^\+234\d{10}$', formatted))


# ============================================
# MONEY/DECIMAL UTILITIES
# ============================================

def money_to_kobo(amount: Decimal) -> int:
    """Convert Naira to kobo (smallest unit) for payment processors."""
    return int(amount * 100)


def kobo_to_money(kobo: int) -> Decimal:
    """Convert kobo to Naira."""
    return Decimal(kobo) / 100


def format_money(amount: Decimal, currency: str = "NGN") -> str:
    """Format money for display."""
    symbols = {
        "NGN": "₦",
        "USD": "$",
        "GBP": "£",
        "EUR": "€"
    }
    symbol = symbols.get(currency, currency)
    return f"{symbol}{amount:,.2f}"


# ============================================
# DATE/TIME UTILITIES
# ============================================

def get_date_range(days: int) -> tuple[datetime, datetime]:
    """Get date range from now backwards."""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    return start_date, end_date


def is_business_hours(hour: int = None) -> bool:
    """Check if current time is within business hours (9 AM - 6 PM WAT)."""
    if hour is None:
        hour = datetime.utcnow().hour
    return 8 <= hour < 18  # UTC+1 for WAT


def get_expiry_date(days: int = 30) -> datetime:
    """Get expiry date from now."""
    return datetime.utcnow() + timedelta(days=days)


# ============================================
# HASHING UTILITIES
# ============================================

def generate_hash(data: str, algorithm: str = "sha256") -> str:
    """Generate hash of data."""
    hash_obj = hashlib.new(algorithm)
    hash_obj.update(data.encode('utf-8'))
    return hash_obj.hexdigest()


def generate_file_hash(file_content: bytes) -> str:
    """Generate hash of file content."""
    return hashlib.sha256(file_content).hexdigest()


# ============================================
# DISTANCE CALCULATIONS
# ============================================

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two coordinates using Haversine formula.
    Returns distance in kilometers.
    """
    from math import radians, sin, cos, sqrt, atan2

    # Earth radius in kilometers
    R = 6371.0

    lat1_rad = radians(lat1)
    lon1_rad = radians(lon1)
    lat2_rad = radians(lat2)
    lon2_rad = radians(lon2)

    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad

    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    distance = R * c
    return distance


def is_within_radius(
        center_lat: float,
        center_lon: float,
        point_lat: float,
        point_lon: float,
        radius_km: float
) -> bool:
    """Check if point is within radius of center."""
    distance = calculate_distance(center_lat, center_lon, point_lat, point_lon)
    return distance <= radius_km


# ============================================
# PAGINATION UTILITIES
# ============================================

def calculate_pagination(total: int, page: int, page_size: int) -> Dict[str, Any]:
    """Calculate pagination metadata."""
    total_pages = (total + page_size - 1) // page_size
    has_next = page < total_pages
    has_prev = page > 1

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": has_next,
        "has_prev": has_prev
    }


# ============================================
# JSON UTILITIES
# ============================================

def safe_json_loads(data: str, default: Any = None) -> Any:
    """Safely parse JSON, return default on error."""
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return default


def safe_json_dumps(data: Any, default: str = "{}") -> str:
    """Safely dump to JSON, return default on error."""
    try:
        return json.dumps(data)
    except (TypeError, ValueError):
        return default


# ============================================
# PERCENTAGE CALCULATIONS
# ============================================

def calculate_percentage(part: float, whole: float) -> float:
    """Calculate percentage."""
    if whole == 0:
        return 0.0
    return (part / whole) * 100


def apply_percentage(amount: Decimal, percentage: float) -> Decimal:
    """Apply percentage to amount."""
    return amount * Decimal(str(percentage / 100))


def calculate_commission(amount: Decimal, rate: float) -> Decimal:
    """Calculate commission from amount."""
    return apply_percentage(amount, rate)


# ============================================
# RATING CALCULATIONS
# ============================================

def calculate_new_average(
        current_avg: float,
        current_count: int,
        new_rating: float
) -> float:
    """Calculate new average rating."""
    total = (current_avg * current_count) + new_rating
    new_count = current_count + 1
    return total / new_count if new_count > 0 else 0.0


# ============================================
# FILE SIZE UTILITIES
# ============================================

def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def validate_file_size(size_bytes: int, max_mb: int = 10) -> bool:
    """Validate file size is within limit."""
    max_bytes = max_mb * 1024 * 1024
    return size_bytes <= max_bytes


# ============================================
# VALIDATION UTILITIES
# ============================================

def is_valid_email(email: str) -> bool:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def is_valid_url(url: str) -> bool:
    """Validate URL format."""
    pattern = r'^https?://[^\s/$.?#].[^\s]*$'
    return bool(re.match(pattern, url))


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe storage."""
    # Remove path separators and special characters
    safe_name = re.sub(r'[^\w\s.-]', '', filename)
    # Replace spaces with underscores
    safe_name = re.sub(r'\s+', '_', safe_name)
    return safe_name