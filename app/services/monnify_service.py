"""
app/services/monnify_service.py

FIXES:
  1.  [AUDIT BUG-1] Local ServiceUnavailableException DELETED.
      Was defined locally (did NOT inherit from LocalyException), so it
      was never caught by FastAPI's @app.exception_handler(LocalyException).
      Every Monnify API failure produced a raw 500 response.
      Now imports ServiceUnavailableException from app.core.exceptions, which
      IS a LocalyException subclass and will be handled cleanly.

  2.  MONNIFY_BASE_URL defaults to production URL per config.py.

  3.  verify_webhook_signature() — Blueprint §5.5:
      "Verify: hmac.compare_digest(computed_sig, header_sig)"
      Header: X-Monnify-Signature (HMAC-SHA512 of raw request body
      using MONNIFY_SECRET_KEY).

  4.  parse_webhook_payload() — extracts all fields needed by
      wallet_service.handle_monnify_funding().
"""
import base64
import hashlib
import hmac
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.core.exceptions import ServiceUnavailableException   # [BUG-1 FIX] — not a local class

logger = logging.getLogger(__name__)


class MonnifyService:
    """
    Async Monnify payment gateway integration.

    Blueprint §5 / §16.1:
    - Primary bank transfer + virtual account provider.
    - MONNIFY_API_KEY, MONNIFY_SECRET_KEY, MONNIFY_CONTRACT_CODE,
      MONNIFY_BASE_URL required in .env.
    """

    def __init__(self) -> None:
        self.api_key:       str = settings.MONNIFY_API_KEY
        self.secret_key:    str = settings.MONNIFY_SECRET_KEY
        self.contract_code: str = settings.MONNIFY_CONTRACT_CODE
        # Production URL per corrected config: https://api.monnify.com/api/v1
        self._base_v1: str = settings.MONNIFY_BASE_URL
        # Strip /api/v1 suffix to get root for /api/v2 paths
        self._root: str = self._base_v1.rstrip("/").rsplit("/api/", 1)[0]

    # ── Webhook Signature Verification — Blueprint §5.5 ─────────────────────

    def verify_webhook_signature(
        self, raw_body: bytes, signature_header: str
    ) -> bool:
        """
        Blueprint §5.5:
          "Verify: hmac.compare_digest(computed_sig, header_sig)"
          Header: X-Monnify-Signature (HMAC-SHA512)
          Secret: MONNIFY_SECRET_KEY

        Security convention: compare locally computed value FIRST,
        externally received value SECOND, to surface type-mismatch bugs.

        Returns True if the signature is valid, False otherwise.
        The wallet webhook router MUST call this before processing any payload.
        """
        if not signature_header:
            logger.warning("Monnify webhook: missing X-Monnify-Signature header")
            return False

        computed = hmac.new(
            self.secret_key.encode("utf-8"),
            raw_body,
            hashlib.sha512,
        ).hexdigest()

        # [BUG-9 FIX] Local computed value first, external header second.
        is_valid = hmac.compare_digest(computed, signature_header)
        if not is_valid:
            logger.warning(
                "Monnify webhook: HMAC mismatch. "
                "computed=%s... header=%s...",
                computed[:16], signature_header[:16],
            )
        return is_valid

    @staticmethod
    def parse_webhook_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract fields from a Monnify collection webhook payload.

        Blueprint §5.1: Monnify sends amountPaid in Naira (not kobo).
        Returns a normalised dict for wallet_service.handle_monnify_funding().
        """
        source = payload.get("paymentSource") or {}
        acct   = payload.get("reservedAccountInfo") or {}

        return {
            "payment_status":    payload.get("paymentStatus", ""),
            "monnify_reference": payload.get("transactionReference", ""),
            # Destination virtual account number
            "virtual_account_number": (
                payload.get("destinationAccountNumber")
                or acct.get("accountNumber", "")
            ),
            # Amount in Naira (not kobo — no conversion needed for Monnify)
            "amount_ngn":  payload.get("amountPaid", 0),
            "sender_bank": source.get("bankName", ""),
            "sender_name": source.get("accountName", ""),
            "currency":    payload.get("currency", "NGN"),
        }

    @staticmethod
    def is_successful_payment(payload: Dict[str, Any]) -> bool:
        """Return True only when Monnify confirms the payment is PAID/settled."""
        return payload.get("paymentStatus") == "PAID"

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def _get_access_token(self) -> str:
        """
        Obtain a short-lived Bearer token via Basic auth.
        NOTE: In production, cache this in Redis (TTL ≈ 3500s) to avoid a
        round-trip on every webhook/provisioning call.
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
            # [BUG-1 FIX] Now raises the app-level ServiceUnavailableException,
            # which IS a LocalyException and WILL be caught by the FastAPI handler.
            raise ServiceUnavailableException(
                service="Monnify",
                detail=f"Monnify unreachable: {exc}",
            )

        data = response.json()
        if not data.get("requestSuccessful"):
            raise ServiceUnavailableException(
                service="Monnify",
                detail=f"Monnify auth failed: {data.get('responseMessage', 'Unknown error')}",
            )
        return data["responseBody"]["accessToken"]

    # ── Virtual Account Provisioning ──────────────────────────────────────────

    async def reserve_virtual_account(
        self,
        account_reference: str,
        account_name: str,
        customer_email: str = "",
        customer_name:  str = "",
    ) -> Dict[str, Any]:
        """
        Reserve a permanent dedicated virtual account for a user.
        Blueprint §5.1: "Each user gets a unique, permanent Monnify virtual
        account at registration. Account number never changes."

        Args:
            account_reference: Unique identifier (use str(user_id)).
                               Monnify deduplicates on this — safe to retry.
            account_name:      Display name on the account.

        Returns responseBody containing accountNumber, bankName, accountName.
        """
        token = await self._get_access_token()

        payload: Dict[str, Any] = {
            "accountReference":    account_reference,
            "accountName":         account_name,
            "currencyCode":        "NGN",
            "contractCode":        self.contract_code,
            "getAllAvailableBanks": True,
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
                service="Monnify",
                detail=f"Monnify unreachable during account reservation: {exc}",
            )

        data = response.json()
        if not data.get("requestSuccessful"):
            raise ServiceUnavailableException(
                service="Monnify",
                detail=f"Monnify reserve account failed: {data.get('responseMessage', 'Unknown error')}",
            )
        return data["responseBody"]

    async def get_reserved_account(
        self, account_reference: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch existing reserved account by reference (re-sync on failure)."""
        try:
            token = await self._get_access_token()
            url   = f"{self._root}/api/v2/bank-transfer/reserved-accounts/{account_reference}"
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                )
            data = response.json()
            if data.get("requestSuccessful"):
                return data.get("responseBody")
            return None
        except Exception:
            return None

    async def suspend_virtual_account(self, account_reference: str) -> bool:
        """
        Suspend virtual account on user ban.
        Blueprint §5.5: "Virtual account suspended on ban, reactivated on unban."
        """
        try:
            token = await self._get_access_token()
            url   = f"{self._root}/api/v2/bank-transfer/reserved-accounts/limit/{account_reference}"
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.put(
                    url,
                    json={"reservedAccountType": "GENERAL", "restrictPaymentSource": True},
                    headers={"Authorization": f"Bearer {token}"},
                )
            return response.json().get("requestSuccessful", False)
        except Exception as exc:
            logger.warning("Monnify suspend failed for ref=%s: %s", account_reference, exc)
            return False

    async def reactivate_virtual_account(self, account_reference: str) -> bool:
        """
        Reactivate virtual account on user unban.
        Blueprint §5.5: "Virtual account suspended on ban, reactivated on unban."
        """
        try:
            token = await self._get_access_token()
            url   = f"{self._root}/api/v2/bank-transfer/reserved-accounts/limit/{account_reference}"
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.put(
                    url,
                    json={"reservedAccountType": "GENERAL", "restrictPaymentSource": False},
                    headers={"Authorization": f"Bearer {token}"},
                )
            return response.json().get("requestSuccessful", False)
        except Exception as exc:
            logger.warning("Monnify reactivate failed for ref=%s: %s", account_reference, exc)
            return False


# Singleton
monnify_service = MonnifyService()