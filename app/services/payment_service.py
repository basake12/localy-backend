"""
Payment processing service — Paystack gateway.
Uses httpx for non-blocking async HTTP calls.

FIX: _raise_for_status previously called ServiceUnavailableException(detail)
     passing the Paystack error message as the *service* positional argument,
     producing garbled messages like:
         "Cannot resolve account is currently unavailable. Please try again later."
     Fixed to: ServiceUnavailableException(detail=detail)
     which correctly surfaces Paystack's own message.
"""
import httpx
from typing import Optional, Dict, Any
from decimal import Decimal

from app.config import settings
from app.core.utils import generate_reference_code, money_to_kobo, kobo_to_money
from app.core.exceptions import ServiceUnavailableException


class PaystackService:
    """Async Paystack payment gateway integration."""

    def __init__(self) -> None:
        self.secret_key: str = getattr(settings, "PAYSTACK_SECRET_KEY", "")
        self.public_key: str = getattr(settings, "PAYSTACK_PUBLIC_KEY", "")
        self.base_url = "https://api.paystack.co"

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, payload: dict) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=self._headers,
            )
        self._raise_for_status(response)
        return response.json()

    async def _get(self, path: str, params: Optional[dict] = None) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self._headers,
            )
        self._raise_for_status(response)
        return response.json()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 500:
            raise ServiceUnavailableException(
                detail=f"Paystack gateway error: {response.status_code}"
            )
        # 4xx: surface Paystack's own error message directly.
        # FIX: was ServiceUnavailableException(detail) — positional arg maps to
        # `service`, producing: "<message> is currently unavailable. Please try again later."
        # Now uses keyword arg so the message is passed to `detail` unchanged.
        if response.status_code >= 400:
            detail = response.json().get("message", "Payment gateway request failed")
            raise ServiceUnavailableException(detail=detail)

    # ─── Transactions ─────────────────────────────────────────────────────

    async def initialize_transaction(
        self,
        email: str,
        amount: Decimal,
        reference: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        callback_url: Optional[str] = None,
        gateway: str = "paystack",
    ) -> Dict[str, Any]:
        """
        Initialize a Paystack transaction.
        Returns authorization_url, access_code, and reference.
        """
        payload: Dict[str, Any] = {
            "email": email,
            "amount": money_to_kobo(amount),
            "reference": reference or generate_reference_code("PAY"),
        }
        if metadata:
            payload["metadata"] = metadata
        if callback_url:
            payload["callback_url"] = callback_url

        return await self._post("/transaction/initialize", payload)

    async def verify_transaction(self, reference: str) -> Dict[str, Any]:
        """
        Verify a completed transaction by reference.
        Converts amount from kobo back to Naira in the response.

        IMPORTANT: amount in the returned dict is already in NGN (not kobo).
        Callers must NOT divide by 100 again.
        The value is a plain float (not Decimal) so the dict is safe to
        store directly in a PostgreSQL JSONB column without extra sanitisation.
        """
        result = await self._get(f"/transaction/verify/{reference}")
        if result.get("status") and result.get("data"):
            # kobo_to_money() returns Decimal — cast to float so the result dict
            # is JSON-serializable when stored as JSONB metadata.
            result["data"]["amount"] = float(kobo_to_money(result["data"]["amount"]))
        return result

    # ─── Transfers / Payouts ──────────────────────────────────────────────

    async def create_transfer_recipient(
        self,
        account_number: str,
        bank_code: str,
        name: str,
        currency: str = "NGN",
    ) -> Dict[str, Any]:
        """Create a transfer recipient (required before initiating a payout)."""
        return await self._post(
            "/transferrecipient",
            {
                "type": "nuban",
                "name": name,
                "account_number": account_number,
                "bank_code": bank_code,
                "currency": currency,
            },
        )

    async def initiate_transfer(
        self,
        recipient_code: str,
        amount: Decimal,
        reason: str = "Wallet withdrawal",
    ) -> Dict[str, Any]:
        """Initiate a payout to a verified bank account."""
        return await self._post(
            "/transfer",
            {
                "source": "balance",
                "amount": money_to_kobo(amount),
                "recipient": recipient_code,
                "reason": reason,
            },
        )

    # ─── Utility ──────────────────────────────────────────────────────────

    async def list_banks(self, country: str = "nigeria") -> Dict[str, Any]:
        return await self._get("/bank", params={"country": country})

    async def resolve_account(
        self, account_number: str, bank_code: str
    ) -> Dict[str, Any]:
        return await self._get(
            "/bank/resolve",
            params={"account_number": account_number, "bank_code": bank_code},
        )


# Singleton
payment_service = PaystackService()