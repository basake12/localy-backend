from datetime import datetime, timedelta
from typing import Optional, Union, Any
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import HTTPException, status
import secrets
import string

from app.config import settings

# Password hashing context - using argon2 (no 72-byte limit, more secure than bcrypt)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


# ============================================
# PASSWORD HASHING
# ============================================

def hash_password(password: str) -> str:
    """Hash a plain text password using argon2"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def validate_password_strength(password: str) -> bool:
    """
    Validate password meets minimum requirements:
    - At least 8 characters
    - Contains uppercase and lowercase
    - Contains at least one digit
    """
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        return False

    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)

    return has_upper and has_lower and has_digit


# ============================================
# JWT TOKEN MANAGEMENT
# ============================================

def create_access_token(
        subject: Union[str, Any],
        expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create JWT access token

    Args:
        subject: User ID or any identifier
        expires_delta: Token expiration time

    Returns:
        Encoded JWT token string
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode = {
        "exp": expire,
        "iat": datetime.utcnow(),
        "sub": str(subject)
    }

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )

    return encoded_jwt


def create_refresh_token(
        subject: Union[str, Any],
        expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create JWT refresh token

    Args:
        subject: User ID
        expires_delta: Token expiration time

    Returns:
        Encoded JWT refresh token string
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )

    to_encode = {
        "exp": expire,
        "iat": datetime.utcnow(),
        "sub": str(subject),
        "type": "refresh"
    }

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )

    return encoded_jwt


def decode_token(token: str) -> dict:
    """
    Decode and validate JWT token

    Args:
        token: JWT token string

    Returns:
        Decoded token payload

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_token(token: str) -> Optional[str]:
    """
    Verify token and return user ID

    Args:
        token: JWT token string

    Returns:
        User ID if valid, None otherwise
    """
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
        return user_id
    except HTTPException:
        return None


# ============================================
# OTP GENERATION
# ============================================

def generate_otp(length: int = 6) -> str:
    """
    Generate numeric OTP

    Args:
        length: Number of digits

    Returns:
        OTP string
    """
    return ''.join(secrets.choice(string.digits) for _ in range(length))


def generate_verification_token(length: int = 32) -> str:
    """
    Generate secure random token for email verification

    Args:
        length: Token length

    Returns:
        Random token string
    """
    return secrets.token_urlsafe(length)


# ============================================
# PERMISSION CHECKS
# ============================================

def check_user_type(user_type: str, allowed_types: list) -> bool:
    """
    Check if user type is in allowed types

    Args:
        user_type: Current user type
        allowed_types: List of allowed user types

    Returns:
        True if allowed, False otherwise
    """
    return user_type in allowed_types


def require_user_type(*allowed_types: str):
    """
    Decorator to require specific user types

    Usage:
        @require_user_type('admin', 'business')
        def admin_function(user):
            pass
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            # Implementation will be in dependencies.py
            return func(*args, **kwargs)

        return wrapper

    return decorator


# ============================================
# API KEY GENERATION (for businesses)
# ============================================

def generate_api_key() -> str:
    """
    Generate API key for business integrations

    Returns:
        Secure random API key
    """
    return f"lc_{secrets.token_urlsafe(32)}"


def verify_api_key(api_key: str) -> bool:
    """
    Verify API key format

    Args:
        api_key: API key to verify

    Returns:
        True if format is valid
    """
    return api_key.startswith("lc_") and len(api_key) > 35