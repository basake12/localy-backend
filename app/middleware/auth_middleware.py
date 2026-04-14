"""
Authentication middleware for Localy.

Attaches the authenticated user_id and user_type to ``request.state``
for every incoming request. Endpoints that require auth use the
``get_current_user`` dependency instead — this middleware provides
a lightweight, pre-parsed state for logging and rate-limiting.
"""
import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.security import TokenDecodeError, decode_token

log = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Starlette-compatible middleware that parses the Bearer token
    (if present) and populates ``request.state``:

        request.state.user_id    — str UUID or None
        request.state.user_type  — str user_type claim or None

    Auth *enforcement* is handled by FastAPI dependencies
    (``get_current_user``, ``get_current_admin_user``, etc.).
    This middleware only enriches the request context.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.user_id   = None
        request.state.user_type = None

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
            try:
                payload = decode_token(token)

                # Only accept access tokens — reject refresh tokens used as auth
                if payload.get("type") != "access":
                    log.warning(
                        "AuthMiddleware: non-access token used as Bearer. "
                        "type=%r path=%s",
                        payload.get("type"),
                        request.url.path,
                    )
                else:
                    request.state.user_id   = payload.get("sub")
                    request.state.user_type = payload.get("user_type")

            except TokenDecodeError:
                # Invalid / expired token — leave state as None.
                # The endpoint dependency will return 401 if auth is required.
                pass

        return await call_next(request)