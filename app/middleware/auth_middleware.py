"""
Authentication middleware for request processing.
"""
from fastapi import Request
from jose import jwt, JWTError

from app.config import settings


class AuthMiddleware:
    """Middleware to attach user info to request if authenticated."""

    async def __call__(self, request: Request, call_next):
        # Try to get auth token
        auth_header = request.headers.get("Authorization")

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            try:
                payload = jwt.decode(
                    token,
                    settings.SECRET_KEY,
                    algorithms=[settings.ALGORITHM]
                )
                request.state.user_id = payload.get("sub")
            except JWTError:
                request.state.user_id = None
        else:
            request.state.user_id = None

        response = await call_next(request)
        return response