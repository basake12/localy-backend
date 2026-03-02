
"""
Authentication service (alias for auth_service).
This file can redirect to the main auth_service or contain additional auth utilities.
"""
# Most auth logic is in app/crud/user.py and app/core/security.py
# This file can be used for additional auth-related business logic

from app.core.security import create_access_token, create_refresh_token, verify_password, hash_password
from app.crud.user_crud import user_crud

# Re-export for convenience
__all__ = [
    'create_access_token',
    'create_refresh_token',
    'verify_password',
    'hash_password',
    'user_crud'
]
