
"""
Logging middleware for request/response tracking.
"""
import time
import logging
from fastapi import Request

logger = logging.getLogger(__name__)


class LoggingMiddleware:
    """Middleware to log all requests and responses."""

    async def __call__(self, request: Request, call_next):
        start_time = time.time()

        # Log request
        logger.info(f"Request: {request.method} {request.url.path}")

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration = time.time() - start_time

        # Log response
        logger.info(
            f"Response: {request.method} {request.url.path} "
            f"Status: {response.status_code} Duration: {duration:.3f}s"
        )

        # Add custom headers
        response.headers["X-Process-Time"] = str(duration)

        return response

