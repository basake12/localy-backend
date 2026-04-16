"""
app/middleware/auth_middleware.py

FIXES vs previous version:
  1.  request.state.user_type → request.state.role.
      Blueprint §3.2 JWT payload uses `role` not `user_type`.
      payload.get("user_type") would always be None since the key
      doesn't exist in the token — making all rate-limit and logging
      enrichment ineffective.

  2.  request.state.business_id added.
      Blueprint §3.2 JWT payload includes business_id for business users.
      Useful for request-level logging and rate limiting by business.
"""
import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.security import TokenDecodeError, decode_token

log = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Starlette-compatible middleware that parses the Bearer token (if present)
    and populates request.state:

        request.state.user_id     — str UUID or None
        request.state.role        — str role claim ('customer'|'business'|'rider') or None
        request.state.business_id — str UUID or None (for business users)

    Auth ENFORCEMENT is handled by FastAPI dependencies (get_current_user, etc.).
    This middleware only enriches the request context for logging and rate limiting.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Default — no authenticated user
        request.state.user_id     = None
        request.state.role        = None      # Blueprint §3.2: role (not user_type)
        request.state.business_id = None      # Blueprint §3.2 JWT claim

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
            try:
                payload = decode_token(token)

                # Only accept access tokens — reject refresh tokens used as Bearer
                if payload.get("type") != "access":
                    log.warning(
                        "AuthMiddleware: non-access token used as Bearer. "
                        "type=%r path=%s",
                        payload.get("type"),
                        request.url.path,
                    )
                else:
                    request.state.user_id     = payload.get("sub")
                    # Blueprint §3.2: JWT uses `role` claim
                    request.state.role        = payload.get("role")
                    request.state.business_id = payload.get("business_id")

            except TokenDecodeError:
                # Invalid / expired token — state stays None.
                # Endpoint dependency returns 401 if auth is required.
                pass

        return await call_next(request)