"""
CORS middleware configuration.
"""
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings


def add_cors_middleware(app):
    """Add CORS middleware to app."""

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"]
    )



