"""
app/services/monnify_service.py

Monnify integration for Localy.

Responsibilities:
  - Authenticate with Monnify (Basic → Bearer token, cached per session)
  - Reserve a dedicated virtual bank account per user (Blueprint §4.1.1)
  - Webhook payload parsing for auto-credit on bank transfer receipt

Blueprint §4.1.1:
  "Bank Transfer to Dedicated Virtual Account — each user gets a unique
   account number (Monnify) that is permanently theirs. Transfer clears
   and auto-credits wallet within 60 seconds."

Monnify auth flow:
  POST /api/v1/auth/login  (Basic auth: base64(apiKey:secretKey))
  → { responseBody: { accessToken, expiresIn } }

Reserve account:
  POST /api/v2/bank-transfer/reserved-accounts  (Bearer token)
  → { responseBody: { accountNumber, bankName, accountName, ... } }

Note: The env MONNIFY_BASE_URL is /api/v1. Reserved accounts live at /api/v2,
so we construct the v2 URL by swapping the suffix.
"""
import base64
import logging
from typing import Optional, Dict, Any

import httpx

from app.config import settings
from app.core.exceptions import ServiceUnavailableException

logger = logging.getLogger(__name__)


class MonnifyService:
    """Async Monnify payment gateway integration."""

    def __init__(self) -> None:
        self.api_key:       str = getattr(settings, "MONNIFY_API_KEY", "")
        self.secret_key:    str = getattr(settings, "MONNIFY_SECRET_KEY", "")
        self.contract_code: str = getattr(settings, "MONNIFY_CONTRACT_CODE", "")
        # e.g. https://sandbox.monnify.com/api/v1
        self._base_v1: str = getattr(settings, "MONNIFY_BASE_URL", "https://sandbox.monnify.com/api/v1")
        # Strip /api/v1 to get root so we can build /api/v2 paths
        self._root: str = self._base_v1.rstrip("/").rsplit("/api/", 1)[0]

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def _get_access_token(self) -> str:
        """
        Obtain a short-lived Bearer token from Monnify via Basic auth.
        Monnify tokens expire in ~1 hour. In production you should cache this
        in Redis with a TTL slightly below the expiry. For now we fetch fresh
        on each call — acceptable for low-volume operations like wallet creation.
        """
        credentials = base64.b64encode(
            f"{self.api_key}:{self.secret_key}".encode()
        ).decode()

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self._base_v1}/auth/login",
                    headers={"Authorization": f"Basic {credentials}"},
                )
        except httpx.RequestError as exc:
            raise ServiceUnavailableException(
                detail=f"Monnify unreachable: {exc}"
            )

        data = response.json()
        if not data.get("requestSuccessful"):
            raise ServiceUnavailableException(
                detail=f"Monnify auth failed: {data.get('responseMessage', 'Unknown error')}"
            )

        return data["responseBody"]["accessToken"]

    # ── Virtual Account ───────────────────────────────────────────────────────

    async def reserve_virtual_account(
        self,
        account_reference: str,
        account_name: str,
        customer_email: str = "",
        customer_name:  str = "",
    ) -> Dict[str, Any]:
        """
        Reserve a permanent dedicated virtual account for a user.

        Args:
            account_reference: Unique identifier (use user_id as string).
                               Monnify uses this to prevent duplicate reservations.
            account_name:      Display name on the account (user's full name).
            customer_email:    Optional — stored by Monnify for dispute resolution.
            customer_name:     Optional — stored by Monnify.

        Returns:
            responseBody dict containing:
                accountNumber  — the virtual NUBAN account number
                bankName       — the issuing bank name (e.g. "Wema Bank")
                accountName    — the account display name
                accountReference — mirrors what we sent

        Raises:
            ServiceUnavailableException if Monnify is unreachable or returns an error.
        """
        token = await self._get_access_token()

        payload: Dict[str, Any] = {
            "accountReference": account_reference,
            "accountName":      account_name,
            "currencyCode":     "NGN",
            "contractCode":     self.contract_code,
            "getAllAvailableBanks": True,   # Required by Monnify sandbox — returns all available banks
        }
        if customer_email:
            payload["customerEmail"] = customer_email
        if customer_name:
            payload["customerName"] = customer_name

        url = f"{self._root}/api/v2/bank-transfer/reserved-accounts"

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type":  "application/json",
                    },
                )
        except httpx.RequestError as exc:
            raise ServiceUnavailableException(
                detail=f"Monnify unreachable during account reservation: {exc}"
            )

        data = response.json()

        if not data.get("requestSuccessful"):
            raise ServiceUnavailableException(
                detail=f"Monnify reserve account failed: {data.get('responseMessage', 'Unknown error')}"
            )

        return data["responseBody"]

    async def get_reserved_account(self, account_reference: str) -> Optional[Dict[str, Any]]:
        """
        Fetch an existing reserved account by reference.
        Useful for re-syncing account_number/bank_name if the wallet row
        was created before Monnify provisioning succeeded.
        """
        token = await self._get_access_token()
        url   = f"{self._root}/api/v2/bank-transfer/reserved-accounts/{account_reference}"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.RequestError:
            return None

        data = response.json()
        if not data.get("requestSuccessful"):
            return None
        return data.get("responseBody")

    # ── Webhook Parsing ───────────────────────────────────────────────────────

    @staticmethod
    def parse_webhook_amount(payload: Dict[str, Any]) -> Optional[float]:
        """
        Extract the credited NGN amount from a Monnify collection webhook.
        Monnify sends `amountPaid` in Naira (not kobo) — no conversion needed.
        """
        return payload.get("amountPaid") or payload.get("paidOn")

    @staticmethod
    def is_successful_payment(payload: Dict[str, Any]) -> bool:
        """Return True only when Monnify confirms the payment is settled."""
        return payload.get("paymentStatus") == "PAID"


# Singleton
monnify_service = MonnifyService()