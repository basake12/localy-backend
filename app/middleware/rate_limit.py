
"""
Rate limiting middleware using Redis.
"""
import time
from fastapi import Request, HTTPException, status
from app.core.cache import cache


class RateLimitMiddleware:
    """
    Rate limiting middleware.
    Limits requests per IP address.
    """

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.window = 60  # seconds

    async def __call__(self, request: Request, call_next):
        # Get client IP
        client_ip = request.client.host

        # Skip rate limiting for health checks
        if request.url.path in ["/health", "/api/v1/health"]:
            return await call_next(request)

        # Create rate limit key
        rate_key = f"rate_limit:{client_ip}"

        # Check current count
        current = cache.get(rate_key)

        if current is None:
            # First request in window
            cache.set(rate_key, 1, self.window)
        else:
            if int(current) >= self.requests_per_minute:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please try again later."
                )
            cache.increment(rate_key)

        response = await call_next(request)
        return response


class APIRateLimiter:
    """Endpoint-specific rate limiter."""

    @staticmethod
    def limit(key: str, max_requests: int, window_seconds: int = 60) -> bool:
        """
        Check if rate limit exceeded.

        Args:
            key: Unique identifier (user_id, ip, etc.)
            max_requests: Max requests allowed
            window_seconds: Time window in seconds

        Returns:
            True if allowed, False if exceeded
        """
        rate_key = f"api_limit:{key}"
        current = cache.get(rate_key)

        if current is None:
            cache.set(rate_key, 1, window_seconds)
            return True

        if int(current) >= max_requests:
            return False

        cache.increment(rate_key)
        return True