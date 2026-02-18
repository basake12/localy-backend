from fastapi import HTTPException, status
from typing import Any, Optional


class LocalyException(HTTPException):
    """Base exception for Localy application"""

    def __init__(
            self,
            status_code: int,
            detail: str,
            headers: Optional[dict] = None
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)


# ============================================
# AUTHENTICATION EXCEPTIONS
# ============================================

class AuthenticationException(LocalyException):
    """Base authentication exception"""

    def __init__(self, detail: str = "Authentication failed"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"}
        )


class InvalidCredentialsException(AuthenticationException):
    """Invalid username or password"""

    def __init__(self):
        super().__init__(detail="Invalid email or password")


class TokenExpiredException(AuthenticationException):
    """JWT token has expired"""

    def __init__(self):
        super().__init__(detail="Token has expired")


class InvalidTokenException(AuthenticationException):
    """Invalid JWT token"""

    def __init__(self):
        super().__init__(detail="Invalid authentication token")


class EmailNotVerifiedException(LocalyException):
    """Email not verified"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address not verified. Please check your inbox."
        )


class PhoneNotVerifiedException(LocalyException):
    """Phone not verified"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Phone number not verified. Please verify your phone."
        )


# ============================================
# AUTHORIZATION EXCEPTIONS
# ============================================

class PermissionDeniedException(LocalyException):
    """User doesn't have required permissions"""

    def __init__(self, detail: str = "Permission denied"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail
        )


class AccountSuspendedException(LocalyException):
    """User account is suspended"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended. Contact support."
        )


class AccountBannedException(LocalyException):
    """User account is banned"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been permanently banned."
        )


# ============================================
# RESOURCE EXCEPTIONS
# ============================================

class NotFoundException(LocalyException):
    """Resource not found"""

    def __init__(self, resource: str = "Resource"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource} not found"
        )


class AlreadyExistsException(LocalyException):
    """Resource already exists"""

    def __init__(self, resource: str = "Resource"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{resource} already exists"
        )


class ValidationException(LocalyException):
    """Validation error"""

    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail
        )


# ============================================
# BUSINESS LOGIC EXCEPTIONS
# ============================================

class InsufficientBalanceException(LocalyException):
    """Wallet has insufficient balance"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient wallet balance"
        )


# Alias — wallet_service uses this name
InsufficientFundsException = InsufficientBalanceException


class SubscriptionRequiredException(LocalyException):
    """Feature requires active subscription"""

    def __init__(self, feature: str = "This feature"):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"{feature} requires an active subscription"
        )


class BookingNotAvailableException(LocalyException):
    """Booking slot not available"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="Selected time slot is not available"
        )


class OutOfStockException(LocalyException):
    """Product out of stock"""

    def __init__(self, product: str = "Product"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{product} is out of stock"
        )


class LocationOutOfRangeException(LocalyException):
    """Location is outside service area"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Location is outside the service area"
        )


class InvalidUserTypeException(LocalyException):
    """User type not allowed for this operation"""

    def __init__(self, allowed_types: list):
        types_str = ', '.join(
            t.value if hasattr(t, 'value') else str(t)
            for t in allowed_types
        )
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This operation is only allowed for: {types_str}"
        )


# ============================================
# FILE UPLOAD EXCEPTIONS
# ============================================

class FileTooLargeException(LocalyException):
    """Uploaded file exceeds size limit"""

    def __init__(self, max_size_mb: int):
        super().__init__(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds {max_size_mb}MB limit"
        )


class InvalidFileTypeException(LocalyException):
    """Invalid file type"""

    def __init__(self, allowed_types: list):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}"
        )


# ============================================
# RATE LIMITING EXCEPTIONS
# ============================================

class RateLimitExceededException(LocalyException):
    """Rate limit exceeded"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later."
        )


# ============================================
# PAYMENT EXCEPTIONS
# ============================================

class PaymentFailedException(LocalyException):
    """Payment processing failed"""

    def __init__(self, detail: str = "Payment processing failed"):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=detail
        )