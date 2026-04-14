"""
app/middleware/rate_limit.py

IP-based rate limiting backed by Redis.

Registered in main.py as:
    from app.middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.RATE_LIMIT_PER_MINUTE)

FIX APPLIED — ASGI lifespan unsupported warning:
    The class was previously a plain dispatch callable with __call__(request, call_next).
    When registered via app.add_middleware(), Starlette expects a proper ASGI middleware
    class whose __call__ accepts (scope, receive, send). The old shape caused lifespan
    events (startup/shutdown) to be swallowed, producing:
        "ASGI 'lifespan' protocol appears unsupported."

    Fix: subclass BaseHTTPMiddleware and move logic into dispatch(). __init__ must
    accept `app` as its first argument and forward it to super().__init__(app).
    The base class handles the raw ASGI __call__, correctly forwards lifespan and
    WebSocket scopes, and delegates HTTP requests to dispatch().

FIX APPLIED — WebSocket 403:
    Starlette's BaseHTTPMiddleware intercepts ALL incoming connections, including
    WebSocket upgrade requests (scope["type"] == "websocket"). The original code had
    no bypass for WebSocket scopes, so the middleware would attempt to process the WS
    handshake as an HTTP request and call_next() would return a 403 — rejecting every
    WebSocket connection before it could reach the router.

    Fix: check scope type at the very top of dispatch(). If it's a WebSocket, pass
    straight through to the next layer without any rate-limit logic. Rate limiting of
    WebSocket connections is handled at the JWT auth level inside the endpoint itself
    (invalid/missing token → close with 1008).
"""
import asyncio
import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.cache import cache

logger = logging.getLogger(__name__)

# Paths exempt from rate limiting
_EXEMPT_PATHS = {"/health", "/", "/favicon.ico"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter keyed by client IP.

    All Redis calls are offloaded to a thread pool so the async event
    loop is never blocked.
    """

    def __init__(self, app, requests_per_minute: int = 60) -> None:
        super().__init__(app)
        self.limit  = requests_per_minute
        self.window = 60  # seconds

    async def dispatch(self, request: Request, call_next):
        # ─────────────────────────────────────────────────────────────────
        # [FIX] WEBSOCKET BYPASS
        # BaseHTTPMiddleware cannot properly lift WebSocket upgrade requests
        # into HTTP responses. If we don't bypass here, every WebSocket
        # connection is rejected with 403 before it reaches the router.
        # ─────────────────────────────────────────────────────────────────
        if request.scope.get("type") == "websocket":
            return await call_next(request)

        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        rate_key  = f"rate_limit:{client_ip}"

        allowed = await asyncio.to_thread(self._check_and_increment, rate_key)

        if not allowed:
            logger.warning(f"Rate limit exceeded for IP {client_ip}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "success": False,
                    "error": {
                        "message": "Too many requests. Please try again later.",
                        "type":    "RateLimitExceeded",
                    },
                },
            )

        # Wrap call_next so any downstream exception doesn't surface as
        # "RuntimeError: No response returned" from Starlette.
        try:
            return await call_next(request)
        except Exception as exc:
            logger.error(
                "Unhandled exception in request pipeline: %s %s — %r",
                request.method,
                request.url.path,
                exc,
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "success": False,
                    "error": {
                        "message": "An unexpected error occurred.",
                        "type":    "InternalServerError",
                    },
                },
            )

    def _check_and_increment(self, rate_key: str) -> bool:
        """
        Returns True if the request is within the allowed limit,
        False if the limit has been exceeded.
        Runs synchronously inside asyncio.to_thread().
        """
        current = cache.get(rate_key)
        if current is None:
            cache.set(rate_key, 1, self.window)
            return True

        if int(current) >= self.limit:
            return False

        cache.increment(rate_key)
        return True


# ============================================
# UTILITY — endpoint-level rate limiter
# ============================================

class APIRateLimiter:
    """
    Programmatic per-endpoint rate limiter.

    Usage in a route:
        if not APIRateLimiter.limit(f"otp:{user.id}", max_requests=5, window_seconds=300):
            raise RateLimitExceededException()
    """

    @staticmethod
    def limit(key: str, max_requests: int, window_seconds: int = 60) -> bool:
        """
        Returns True if the request is within the allowed window,
        False if exceeded.
        """
        rate_key = f"api_limit:{key}"
        current  = cache.get(rate_key)

        if current is None:
            cache.set(rate_key, 1, window_seconds)
            return True

        if int(current) >= max_requests:
            return False

        cache.increment(rate_key)
        return True