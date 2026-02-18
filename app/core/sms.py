"""
SMS service for Localy — powered by Termii (Nigerian-first).
Falls back to Twilio for international numbers.

Install: pip install requests
Docs:    https://developers.termii.com
"""
import requests
from typing import Optional
from app.config import settings


# ============================================
# PHONE FORMATTER
# ============================================

def format_nigerian_phone(phone: str) -> str:
    """
    Normalise phone to international format: +234XXXXXXXXXX

    Accepts:
        08012345678   → +2348012345678
        2348012345678 → +2348012345678
        +2348012345678 → +2348012345678
    """
    phone = phone.strip().replace(" ", "").replace("-", "")

    if phone.startswith("+"):
        return phone

    if phone.startswith("234"):
        return f"+{phone}"

    if phone.startswith("0") and len(phone) == 11:
        return f"+234{phone[1:]}"

    # Unknown format — return as-is
    return phone


# ============================================
# TERMII PROVIDER
# ============================================

class TermiiSMS:
    """
    Termii SMS / OTP provider (Nigerian-focused).

    Config required in settings:
        TERMII_API_KEY   — from https://termii.com
        TERMII_SENDER_ID — registered sender ID, default "Localy"
    """

    BASE = "https://api.ng.termii.com/api"

    def __init__(self):
        self.api_key = getattr(settings, "TERMII_API_KEY", "")
        self.sender_id = getattr(settings, "TERMII_SENDER_ID", "Localy")

    # ---------- internals ----------

    def _post(self, path: str, payload: dict) -> tuple[bool, dict]:
        try:
            r = requests.post(
                f"{self.BASE}{path}",
                json=payload,
                timeout=10,
            )
            data = r.json() if r.content else {}
            return r.ok, data
        except Exception as exc:
            print(f"[TermiiSMS] POST {path} error: {exc}")
            return False, {}

    def _fmt(self, phone: str) -> str:
        """Termii expects digits only with country code, no '+'."""
        return format_nigerian_phone(phone).lstrip("+")

    # ---------- public ----------

    def send_plain(self, phone: str, message: str) -> bool:
        """Send a plain SMS."""
        ok, data = self._post("/sms/send", {
            "api_key":  self.api_key,
            "to":       self._fmt(phone),
            "from":     self.sender_id,
            "sms":      message,
            "type":     "plain",
            "channel":  "generic",
        })
        return ok and data.get("message") == "Successfully Sent"

    def send_otp(self, phone: str, otp: str, expiry_minutes: int = 10) -> bool:
        """
        Send OTP via Termii's token API.
        We pass our pre-generated OTP inside the message text.
        """
        message = (
            f"Your Localy verification code is {otp}. "
            f"Valid for {expiry_minutes} minutes. Do not share."
        )
        return self.send_plain(phone, message)

    def send_password_reset(self, phone: str, otp: str) -> bool:
        message = (
            f"Your Localy password reset code is {otp}. "
            "Valid for 30 minutes. Ignore if you didn't request this."
        )
        return self.send_plain(phone, message)


# ============================================
# TWILIO FALLBACK
# ============================================

class TwilioSMS:
    """Twilio — used as a fallback for non-Nigerian numbers."""

    def __init__(self):
        self.sid   = getattr(settings, "TWILIO_ACCOUNT_SID", "")
        self.token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
        self.from_ = getattr(settings, "TWILIO_PHONE_NUMBER", "")

    def send_plain(self, phone: str, message: str) -> bool:
        try:
            r = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
                auth=(self.sid, self.token),
                data={"From": self.from_, "To": phone, "Body": message},
                timeout=10,
            )
            return r.status_code == 201
        except Exception as exc:
            print(f"[TwilioSMS] error: {exc}")
            return False

    def send_otp(self, phone: str, otp: str, expiry_minutes: int = 10) -> bool:
        message = (
            f"Your Localy code: {otp}. "
            f"Valid {expiry_minutes} min. Don't share."
        )
        return self.send_plain(phone, message)

    def send_password_reset(self, phone: str, otp: str) -> bool:
        return self.send_plain(
            phone,
            f"Localy password reset code: {otp}. Valid 30 min."
        )


# ============================================
# SMS SERVICE  (auto-selects provider)
# ============================================

class SMSService:
    """
    Unified SMS service.

    Provider priority:
      1. Termii  (if TERMII_API_KEY is set)
      2. Twilio  (if TWILIO_ACCOUNT_SID is set)
    """

    def __init__(self):
        if getattr(settings, "TERMII_API_KEY", ""):
            self._p = TermiiSMS()
        elif getattr(settings, "TWILIO_ACCOUNT_SID", ""):
            self._p = TwilioSMS()
        else:
            self._p = None

    def _warn(self) -> bool:
        print("[SMSService] No SMS provider configured.")
        return False

    # ---------- OTP ----------

    def send_otp(self, phone: str, otp: str, expiry_minutes: int = 10) -> bool:
        if not self._p:
            return self._warn()
        return self._p.send_otp(phone, otp, expiry_minutes)

    def send_password_reset(self, phone: str, otp: str) -> bool:
        if not self._p:
            return self._warn()
        return self._p.send_password_reset(phone, otp)

    # ---------- Transactional ----------

    def send_sms(self, phone: str, message: str) -> bool:
        if not self._p:
            return self._warn()
        return self._p.send_plain(phone, message)

    def send_booking_notification(self, phone: str, booking_id: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy: Booking {booking_id} confirmed. Open the app for details."
        )

    def send_order_update(self, phone: str, order_id: str, status: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy: Order {order_id} is now {status}. Track it in the app."
        )

    def send_delivery_update(self, phone: str, delivery_id: str, status: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy Delivery {delivery_id}: {status}."
        )

    def send_payment_received(self, phone: str, amount: float, ref: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy: Payment of ₦{amount:,.2f} received. Ref: {ref}."
        )

    def send_rider_assignment(self, phone: str, delivery_id: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy: New delivery {delivery_id} assigned. Open the app to accept."
        )


# Singleton
sms_service = SMSService()