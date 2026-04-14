"""
app/services/nowpayments_service.py

NOWPayments integration for Localy crypto top-up channel.

Endpoints used:
  GET  /v1/status                        — health check
  GET  /v1/currencies                    — available currencies
  GET  /v1/estimate?amount&from&to       — NGN → crypto exchange rate
  GET  /v1/min-amount?from&to            — minimum payable crypto amount
  POST /v1/payment                       — create payment order → deposit address
  GET  /v1/payment/:id                   — poll payment status

Sandbox base URL  : https://api-sandbox.nowpayments.io/v1
Production base URL: https://api.nowpayments.io/v1

IPN webhook (wallet.py /crypto/webhook):
  - Triggered by NOWPayments on every status change
  - Verified with HMAC-SHA512 of sorted JSON body
  - Statuses that credit wallet: "finished" | "confirmed"

Environment variables required:
  NOWPAYMENTS_API_KEY        — from NOWPayments dashboard
  NOWPAYMENTS_IPN_SECRET     — from NOWPayments Store Settings tab
  NOWPAYMENTS_IPN_CALLBACK_URL — your public webhook URL
  NOWPAYMENTS_SANDBOX        — "true" for sandbox, "false" for production
"""
import logging
import httpx
from decimal import Decimal
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class NOWPaymentsService:
    """
    Async HTTP client wrapper for the NOWPayments API.

    All methods raise NOWPaymentsException on API errors so
    callers can handle failures cleanly.
    """

    # ── Base URLs ──────────────────────────────────────────────────────────
    SANDBOX_URL    = "https://api-sandbox.nowpayments.io/v1"
    PRODUCTION_URL = "https://api.nowpayments.io/v1"

    # Payment statuses that mean the wallet should be credited
    CREDITABLE_STATUSES = {"finished", "confirmed"}

    # Payment statuses considered terminal failures
    FAILED_STATUSES = {"failed", "refunded", "expired"}

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    # ── Internal helpers ───────────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        sandbox = getattr(settings, "NOWPAYMENTS_SANDBOX", "true")
        if isinstance(sandbox, str):
            return self.SANDBOX_URL if sandbox.lower() == "true" else self.PRODUCTION_URL
        return self.SANDBOX_URL if sandbox else self.PRODUCTION_URL

    @property
    def _headers(self) -> dict:
        return {
            "x-api-key":   settings.NOWPAYMENTS_API_KEY,
            "Content-Type": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a shared async HTTP client (lazy init)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers,
                timeout=30.0,
            )
        return self._client

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        client = await self._get_client()
        try:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text
            logger.error(f"NOWPayments GET {path} failed [{exc.response.status_code}]: {body}")
            raise NOWPaymentsException(
                f"NOWPayments error ({exc.response.status_code}): {body}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error(f"NOWPayments GET {path} request error: {exc}")
            raise NOWPaymentsException(f"Network error calling NOWPayments: {exc}") from exc

    async def _post(self, path: str, payload: dict) -> dict:
        client = await self._get_client()
        try:
            resp = await client.post(path, json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text
            logger.error(f"NOWPayments POST {path} failed [{exc.response.status_code}]: {body}")
            raise NOWPaymentsException(
                f"NOWPayments error ({exc.response.status_code}): {body}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error(f"NOWPayments POST {path} request error: {exc}")
            raise NOWPaymentsException(f"Network error calling NOWPayments: {exc}") from exc

    # ── Public API methods ─────────────────────────────────────────────────

    async def check_status(self) -> bool:
        """
        GET /v1/status
        Returns True if NOWPayments API is operational.
        Use at startup to verify credentials and connectivity.
        """
        try:
            data = await self._get("/status")
            return data.get("message") == "OK"
        except NOWPaymentsException:
            return False

    async def get_available_currencies(self) -> list[str]:
        """
        GET /v1/currencies
        Returns list of supported crypto currency tickers.
        E.g. ["btc", "eth", "usdttrc20", "usdterc20", ...]
        """
        data = await self._get("/currencies")
        return data.get("currencies", [])

    async def get_minimum_payment_amount(
        self,
        from_currency: str,
        to_currency: str,
    ) -> Decimal:
        """
        GET /v1/min-amount?currency_from=X&currency_to=Y
        Returns the minimum crypto amount the user can send.
        Always validate that expected_crypto >= this value before
        creating a payment order.
        """
        data = await self._get(
            "/min-amount",
            params={
                "currency_from": from_currency.lower(),
                "currency_to":   to_currency.lower(),
            },
        )
        min_amount = data.get("min_amount")
        if min_amount is None:
            raise NOWPaymentsException("min_amount missing from NOWPayments response")
        return Decimal(str(min_amount))

    async def get_exchange_rate(
        self,
        from_currency: str,
        to_currency: str,
        amount: float = 1.0,
    ) -> dict:
        """
        GET /v1/estimate?amount=X&currency_from=Y&currency_to=Z

        Returns:
            {
                "currency_from": "ngn",
                "amount_from": 5000.0,
                "currency_to": "usdttrc20",
                "estimated_amount": "29.41",   ← crypto amount for given NGN
                "rate": <derived>              ← NGN per 1 unit of crypto
            }

        NOTE: NOWPayments /estimate returns estimated_amount (how much
        crypto the user gets for `amount` NGN), not a raw rate.
        We derive `rate = amount / estimated_amount` so wallet_service
        can do: expected_crypto = ngn_amount / rate
        """
        crypto_ticker = self._resolve_ticker(to_currency)

        data = await self._get(
            "/estimate",
            params={
                "amount":         amount,
                "currency_from":  from_currency.lower(),
                "currency_to":    crypto_ticker,
            },
        )

        estimated_amount = Decimal(str(data.get("estimated_amount", 0)))
        if estimated_amount <= 0:
            raise NOWPaymentsException(
                f"Invalid estimated_amount from NOWPayments: {estimated_amount}"
            )

        # Derive rate: how many NGN per 1 unit of crypto
        rate = Decimal(str(amount)) / estimated_amount

        return {
            "currency_from":   from_currency.upper(),
            "currency_to":     to_currency.upper(),
            "amount_from":     amount,
            "estimated_amount": float(estimated_amount),
            "rate":            float(rate),
            "raw":             data,
        }

    async def create_payment(
        self,
        price_amount: float,
        price_currency: str,
        pay_currency: str,
        pay_amount: float,
        order_id: str,
        order_description: str,
    ) -> dict:
        """
        POST /v1/payment
        Creates a payment order and returns a deposit address.

        Args:
            price_amount:      NGN amount the user wants to top up
            price_currency:    "NGN"
            pay_currency:      crypto ticker e.g. "USDT", "BTC", "ETH"
            pay_amount:        exact crypto amount derived from exchange rate
            order_id:          our internal UUID to correlate with CryptoTopUp
            order_description: human-readable label in NOWPayments dashboard

        Returns NOWPayments payment object:
            {
                "payment_id":    "5745103833",
                "payment_status": "waiting",
                "pay_address":   "T....",       ← user sends crypto here
                "price_amount":  5000.0,
                "price_currency": "ngn",
                "pay_amount":    29.41,
                "pay_currency":  "usdttrc20",
                "order_id":      "...",
                "expiration_estimate_date": "...",
                ...
            }
        """
        crypto_ticker = self._resolve_ticker(pay_currency)

        # Build IPN callback URL
        ipn_callback_url = getattr(
            settings, "NOWPAYMENTS_IPN_CALLBACK_URL", None
        )

        payload = {
            "price_amount":       price_amount,
            "price_currency":     price_currency.lower(),
            "pay_currency":       crypto_ticker,
            "pay_amount":         pay_amount,
            "order_id":           order_id,
            "order_description":  order_description,
        }
        if ipn_callback_url:
            payload["ipn_callback_url"] = ipn_callback_url

        data = await self._post("/payment", payload)

        if not data.get("pay_address"):
            raise NOWPaymentsException(
                f"NOWPayments did not return a deposit address: {data}"
            )

        logger.info(
            f"NOWPayments payment created: id={data.get('payment_id')} "
            f"address={data.get('pay_address')} "
            f"amount={pay_amount} {crypto_ticker}"
        )
        return data

    async def get_payment_status(self, payment_id: str) -> dict:
        """
        GET /v1/payment/:id
        Poll payment status manually (backup to IPN webhooks).

        Statuses: waiting → confirming → confirmed → finished
                  (or: failed | refunded | expired | partially_paid)
        """
        data = await self._get(f"/payment/{payment_id}")
        return data

    async def is_payment_creditable(self, payment_id: str) -> bool:
        """
        Convenience helper — returns True if payment is in a
        creditable state (finished or confirmed).
        """
        data = await self.get_payment_status(payment_id)
        return data.get("payment_status") in self.CREDITABLE_STATUSES

    # ── Internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _resolve_ticker(currency: str) -> str:
        """
        Map human-friendly currency names to NOWPayments tickers.

        NOWPayments uses network-suffixed tickers:
          USDT on TRC20 → "usdttrc20"
          USDT on ERC20 → "usdterc20"
          USDT on BEP20 → "usdtbsc"  (NOWPayments uses "bsc" not "bep20")
          BTC           → "btc"
          ETH           → "eth"
          BNB           → "bnbbsc"
        """
        mapping = {
            "USDT":    "usdttrc20",   # default USDT to TRC20 (lowest fees)
            "BTC":     "btc",
            "ETH":     "eth",
            "BNB":     "bnbbsc",
            "USDC":    "usdcerc20",
            # Network-explicit overrides (match CryptoNetwork literals)
            "USDTTRC20":  "usdttrc20",
            "USDTERC20":  "usdterc20",
            "USDTBEP20":  "usdtbsc",
        }
        key = currency.upper().replace("-", "").replace("_", "")
        return mapping.get(key, currency.lower())

    async def close(self) -> None:
        """Close the underlying HTTP client. Call on app shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class NOWPaymentsException(Exception):
    """Raised when NOWPayments API returns an error or is unreachable."""
    pass


# Singleton — imported by wallet_service and webhook handler
nowpayments_service = NOWPaymentsService()