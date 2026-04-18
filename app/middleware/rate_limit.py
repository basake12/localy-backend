"""
app/middleware/rate_limit.py

FIXES:
  BUG-R5 FIX: Race condition in rate limiter eliminated.

  Old pattern (BROKEN under burst load):
    current = cache.get(rate_key)          # Request A reads: None
    # Request B also reads: None (race!)
    if current is None:
        cache.set(rate_key, 1, window)     # A sets to 1 — allowed
        # B also sets to 1 — allowed (should have been blocked)
    elif int(current) >= limit:
        return False
    cache.increment(rate_key)

  New pattern (ATOMIC — BUG-R5 FIX):
    new_count = cache.atomic_increment_with_ttl(rate_key, 1, window)
    return new_count <= limit

  atomic_increment_with_ttl uses Redis PIPELINE:
    INCR key   (atomic — no race)
    TTL key    (check if TTL was already set)
    EXPIRE key (set TTL only if this is the first increment)

  BUG-R2 FIX: Uses shared pool via cache module. No new connections created.

All other fixes retained:
  - WebSocket bypass (BaseHTTPMiddleware intercepts WS upgrades)
  - Lifespan ASGI event forwarding
  - asyncio.to_thread for sync Redis in async dispatch
"""
import asyncio
import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.cache import cache, APIRateLimiter

logger = logging.getLogger(__name__)

# Paths exempt from rate limiting
_EXEMPT_PATHS = {"/health", "/", "/favicon.ico"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter keyed by client IP.

    BUG-R5 FIX: Atomic INCR + EXPIRE pipeline — no race condition.
    BUG-R2 FIX: Uses shared Redis pool from cache module.
    All Redis calls offloaded to a thread pool (asyncio.to_thread) so the
    async event loop is never blocked by synchronous Redis I/O.
    """

    def __init__(self, app, requests_per_minute: int = 60) -> None:
        super().__init__(app)
        self.limit  = requests_per_minute
        self.window = 60  # seconds

    async def dispatch(self, request: Request, call_next):
        # ── WebSocket bypass ──────────────────────────────────────────────────
        # BaseHTTPMiddleware cannot lift WS upgrades into HTTP responses.
        # Without this bypass every WebSocket connection returns 403.
        if request.scope.get("type") == "websocket":
            return await call_next(request)

        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        rate_key  = f"rate_limit:{client_ip}"

        # Offload blocking Redis call to thread pool
        allowed = await asyncio.to_thread(
            self._check_atomic, rate_key
        )

        if not allowed:
            logger.warning("Rate limit exceeded for IP %s", client_ip)
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

        try:
            return await call_next(request)
        except Exception as exc:
            logger.error(
                "Unhandled exception in request pipeline: %s %s — %r",
                request.method, request.url.path, exc,
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

    def _check_atomic(self, rate_key: str) -> bool:
        """
        BUG-R5 FIX: Atomic rate-limit check using INCR + EXPIRE pipeline.
        Returns True if within limit, False if exceeded.
        Runs synchronously inside asyncio.to_thread().
        """
        new_count = cache.atomic_increment_with_ttl(
            rate_key, amount=1, ttl=self.window
        )
        if new_count is None:
            return True   # Redis unavailable — fail open
        return new_count <= self.limit


# ── Programmatic per-endpoint rate limiter ────────────────────────────────────

# Re-export APIRateLimiter from cache module for backward compatibility
# Old callers: from app.middleware.rate_limit import APIRateLimiter
# This still works — APIRateLimiter is defined in cache.py and re-exported here.
__all__ = ["RateLimitMiddleware", "APIRateLimiter"]