
"""
Payment processing service using Paystack.
"""
import requests
from typing import Optional, Dict, Any
from decimal import Decimal

from app.config import settings
from app.core.utils import generate_reference_code, money_to_kobo, kobo_to_money


class PaystackService:
    """Paystack payment gateway integration."""

    def __init__(self):
        self.secret_key = getattr(settings, 'PAYSTACK_SECRET_KEY', '')
        self.public_key = getattr(settings, 'PAYSTACK_PUBLIC_KEY', '')
        self.base_url = "https://api.paystack.co"
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json"
        }

    def initialize_transaction(
            self,
            email: str,
            amount: Decimal,
            reference: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
            callback_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Initialize a payment transaction.

        Returns:
            {
                "status": True,
                "message": "Authorization URL created",
                "data": {
                    "authorization_url": "https://...",
                    "access_code": "...",
                    "reference": "..."
                }
            }
        """
        url = f"{self.base_url}/transaction/initialize"

        # Generate reference if not provided
        if not reference:
            reference = generate_reference_code("PAY")

        # Convert amount to kobo
        amount_kobo = money_to_kobo(amount)

        payload = {
            "email": email,
            "amount": amount_kobo,
            "reference": reference
        }

        if metadata:
            payload["metadata"] = metadata

        if callback_url:
            payload["callback_url"] = callback_url

        response = requests.post(url, json=payload, headers=self.headers)
        return response.json()

    def verify_transaction(self, reference: str) -> Dict[str, Any]:
        """
        Verify a transaction.

        Returns:
            {
                "status": True,
                "message": "Verification successful",
                "data": {
                    "status": "success",
                    "reference": "...",
                    "amount": 500000,
                    "paid_at": "...",
                    "customer": {...}
                }
            }
        """
        url = f"{self.base_url}/transaction/verify/{reference}"
        response = requests.get(url, headers=self.headers)
        result = response.json()

        # Convert amount back to Naira
        if result.get("status") and result.get("data"):
            result["data"]["amount"] = kobo_to_money(result["data"]["amount"])

        return result

    def create_transfer_recipient(
            self,
            account_number: str,
            bank_code: str,
            name: str,
            currency: str = "NGN"
    ) -> Dict[str, Any]:
        """Create a transfer recipient for payouts."""
        url = f"{self.base_url}/transferrecipient"

        payload = {
            "type": "nuban",
            "name": name,
            "account_number": account_number,
            "bank_code": bank_code,
            "currency": currency
        }

        response = requests.post(url, json=payload, headers=self.headers)
        return response.json()

    def initiate_transfer(
            self,
            recipient_code: str,
            amount: Decimal,
            reason: str = "Wallet withdrawal"
    ) -> Dict[str, Any]:
        """Initiate a transfer/payout."""
        url = f"{self.base_url}/transfer"

        amount_kobo = money_to_kobo(amount)

        payload = {
            "source": "balance",
            "amount": amount_kobo,
            "recipient": recipient_code,
            "reason": reason
        }

        response = requests.post(url, json=payload, headers=self.headers)
        return response.json()

    def list_banks(self, country: str = "nigeria") -> Dict[str, Any]:
        """Get list of banks."""
        url = f"{self.base_url}/bank?country={country}"
        response = requests.get(url, headers=self.headers)
        return response.json()

    def resolve_account(self, account_number: str, bank_code: str) -> Dict[str, Any]:
        """Resolve/verify bank account."""
        url = f"{self.base_url}/bank/resolve"
        params = {
            "account_number": account_number,
            "bank_code": bank_code
        }
        response = requests.get(url, params=params, headers=self.headers)
        return response.json()


# Singleton instance
payment_service = PaystackService()



