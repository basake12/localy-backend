"""
OAuth service for Localy.
Handles Google and Apple Sign-In token verification.

Install: pip install google-auth PyJWT cryptography httpx
"""
from __future__ import annotations

import httpx
from typing import Optional
from dataclasses import dataclass

from app.config import settings


# ============================================
# DATA CLASSES
# ============================================

@dataclass
class OAuthUserInfo:
    """Normalised user info returned from any OAuth provider."""
    provider: str          # "google" | "apple"
    provider_id: str       # unique ID from the provider
    email: str
    name: Optional[str]
    avatar_url: Optional[str]
    email_verified: bool


# ============================================
# GOOGLE OAUTH
# ============================================

class GoogleOAuth:
    """
    Verify Google ID tokens via Google's tokeninfo endpoint.

    Flow (mobile):
      1. Flutter uses google_sign_in package → gets idToken
      2. Flutter sends idToken to POST /auth/google
      3. Backend verifies token here

    Config required:
        GOOGLE_CLIENT_ID — from Google Cloud Console
    """

    TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

    async def verify_id_token(self, id_token: str) -> Optional[OAuthUserInfo]:
        """
        Verify a Google ID token and return user info.

        Args:
            id_token: The raw Google ID token from the client

        Returns:
            OAuthUserInfo if valid, None otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    self.TOKENINFO_URL,
                    params={"id_token": id_token},
                )

            if resp.status_code != 200:
                print(f"[GoogleOAuth] tokeninfo HTTP {resp.status_code}")
                return None

            data = resp.json()

            # Validate audience (must match our client ID)
            client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
            if client_id and data.get("aud") != client_id:
                print("[GoogleOAuth] audience mismatch")
                return None

            # Validate token is not expired
            if data.get("error_description"):
                print(f"[GoogleOAuth] token error: {data['error_description']}")
                return None

            email = data.get("email")
            if not email:
                return None

            return OAuthUserInfo(
                provider="google",
                provider_id=data.get("sub", ""),
                email=email,
                name=data.get("name"),
                avatar_url=data.get("picture"),
                email_verified=data.get("email_verified") == "true",
            )

        except Exception as exc:
            print(f"[GoogleOAuth] verify error: {exc}")
            return None


# ============================================
# APPLE SIGN-IN
# ============================================

class AppleOAuth:
    """
    Verify Apple identity tokens (JWT).

    Flow (mobile):
      1. Flutter uses sign_in_with_apple package → gets identityToken + authorizationCode
      2. Flutter sends identityToken (and optionally fullName) to POST /auth/apple
      3. Backend verifies JWT here using Apple's public keys

    Config required:
        APPLE_APP_BUNDLE_ID — e.g. "ng.localy.app"
    """

    APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"

    async def _get_apple_public_keys(self) -> Optional[list]:
        """Fetch Apple's current JWK public keys."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.APPLE_KEYS_URL)
            if resp.status_code == 200:
                return resp.json().get("keys", [])
        except Exception as exc:
            print(f"[AppleOAuth] fetch keys error: {exc}")
        return None

    async def verify_identity_token(
        self,
        identity_token: str,
        full_name: Optional[str] = None,
    ) -> Optional[OAuthUserInfo]:
        """
        Verify Apple identity token and return user info.

        Args:
            identity_token: JWT from Apple
            full_name: Optional full name (only provided on first sign-in by Apple)

        Returns:
            OAuthUserInfo if valid, None otherwise
        """
        try:
            import jwt as pyjwt
            from jwt.algorithms import RSAAlgorithm
            import json

            keys = await self._get_apple_public_keys()
            if not keys:
                return None

            # Decode header to find the key ID
            header = pyjwt.get_unverified_header(identity_token)
            kid = header.get("kid")

            # Find matching key
            matching_key = next((k for k in keys if k.get("kid") == kid), None)
            if not matching_key:
                print("[AppleOAuth] no matching key found")
                return None

            # Convert JWK to PEM
            public_key = RSAAlgorithm.from_jwk(json.dumps(matching_key))

            bundle_id = getattr(settings, "APPLE_APP_BUNDLE_ID", "")

            payload = pyjwt.decode(
                identity_token,
                public_key,
                algorithms=["RS256"],
                audience=bundle_id if bundle_id else pyjwt.decode(
                    identity_token, options={"verify_signature": False}
                ).get("aud"),
                issuer="https://appleid.apple.com",
            )

            email = payload.get("email")
            if not email:
                return None

            return OAuthUserInfo(
                provider="apple",
                provider_id=payload.get("sub", ""),
                email=email,
                name=full_name,
                avatar_url=None,    # Apple never provides a photo
                email_verified=payload.get("email_verified") in (True, "true"),
            )

        except Exception as exc:
            print(f"[AppleOAuth] verify error: {exc}")
            return None


# ============================================
# SINGLETONS
# ============================================

google_oauth = GoogleOAuth()
apple_oauth  = AppleOAuth()