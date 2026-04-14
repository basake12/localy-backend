"""
SMS service for Localy — powered by Termii (Nigerian-first).
Falls back to Twilio for international numbers.

Install: pip install requests
Docs:    https://developers.termii.com
"""
import logging

import requests

from app.config import settings

log = logging.getLogger(__name__)


# ─── Phone Formatter ──────────────────────────────────────────────────────────

def format_nigerian_phone(phone: str) -> str:
    """
    Normalise phone to international format: +234XXXXXXXXXX

    Accepts:
        08012345678    → +2348012345678
        2348012345678  → +2348012345678
        +2348012345678 → +2348012345678
    """
    phone = phone.strip().replace(" ", "").replace("-", "")

    if phone.startswith("+"):
        return phone
    if phone.startswith("234"):
        return f"+{phone}"
    if phone.startswith("0") and len(phone) == 11:
        return f"+234{phone[1:]}"

    # Unknown format — return as-is (e.g. international numbers without +)
    return phone


# ─── Termii Provider ──────────────────────────────────────────────────────────

class TermiiSMS:
    """
    Termii SMS / OTP provider (Nigerian-focused).

    Config required in settings:
        TERMII_API_KEY   — from https://termii.com
        TERMII_SENDER_ID — registered sender ID, default "Localy"
    """

    BASE = "https://api.ng.termii.com/api"

    def __init__(self):
        self.api_key   = getattr(settings, "TERMII_API_KEY", "")
        self.sender_id = getattr(settings, "TERMII_SENDER_ID", "Localy")

    def _post(self, path: str, payload: dict) -> tuple[bool, dict]:
        try:
            r = requests.post(
                f"{self.BASE}{path}",
                json=payload,
                timeout=10,
            )
            data = r.json() if r.content else {}
            return r.ok, data
        except Exception:
            log.exception("TermiiSMS: POST %s failed", path)
            return False, {}

    def _fmt(self, phone: str) -> str:
        """Termii expects digits only with country code, no '+'."""
        return format_nigerian_phone(phone).lstrip("+")

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
        success = ok and data.get("message") == "Successfully Sent"
        if not success:
            log.warning("TermiiSMS: send_plain failed. phone=%r data=%r", phone, data)
        return success

    def send_otp(self, phone: str, otp: str, expiry_minutes: int = 10) -> bool:
        """Send OTP via Termii (plain SMS channel, pre-generated code)."""
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


    def send_pin_unlock(self, phone: str, name: str, otp: str) -> bool:
        message = (
            f"Hi {name}, your Localy PIN unlock code is {otp}. "
            "Valid for 30 minutes. Ignore if you didn't request this."
        )
        return self.send_plain(phone, message)


# ─── Twilio Fallback ──────────────────────────────────────────────────────────

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
            success = r.status_code == 201
            if not success:
                log.warning(
                    "TwilioSMS: send_plain failed. phone=%r status=%d body=%s",
                    phone, r.status_code, r.text[:200],
                )
            return success
        except Exception:
            log.exception("TwilioSMS: send_plain error. phone=%r", phone)
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
            f"Localy password reset code: {otp}. Valid 30 min.",
        )


    def send_pin_unlock(self, phone: str, name: str, otp: str) -> bool:
        return self.send_plain(
            phone,
            f"Hi {name}, your Localy PIN unlock code is {otp}. Valid 30 min.",
        )


# ─── SMS Service (auto-selects provider) ──────────────────────────────────────

class SMSService:
    """
    Unified SMS service.

    Provider priority:
      1. Termii  — if ``TERMII_API_KEY`` is set in settings
      2. Twilio  — if ``TWILIO_ACCOUNT_SID`` is set in settings
    """

    def __init__(self):
        if getattr(settings, "TERMII_API_KEY", ""):
            self._p = TermiiSMS()
            log.info("SMSService: using Termii provider")
        elif getattr(settings, "TWILIO_ACCOUNT_SID", ""):
            self._p = TwilioSMS()
            log.info("SMSService: using Twilio provider")
        else:
            self._p = None
            log.warning("SMSService: no SMS provider configured — SMS delivery disabled")

    def _no_provider(self) -> bool:
        log.error("SMSService: attempted to send SMS but no provider is configured")
        return False

    # ── OTP ───────────────────────────────────────────────────────────────────

    def send_otp(self, phone: str, otp: str, expiry_minutes: int = 10) -> bool:
        if settings.DEBUG:
            log.info(f"DEVELOPMENT: SMS OTP for {phone} -> {otp}")
            return True
        if not self._p:
            return self._no_provider()
        return self._p.send_otp(phone, otp, expiry_minutes)

    def send_password_reset(self, phone: str, otp: str) -> bool:
        if settings.DEBUG:
            log.info(f"DEVELOPMENT: Password reset SMS for {phone} -> {otp}")
            return True
        if not self._p:
            return self._no_provider()
        return self._p.send_password_reset(phone, otp)
        
    def send_pin_unlock(self, phone: str, name: str, otp: str) -> bool:
        if settings.DEBUG:
            log.info(f"DEVELOPMENT: PIN unlock SMS for {phone} -> {otp}")
            return True
        if not self._p:
            return self._no_provider()
        return self._p.send_pin_unlock(phone, name, otp)

    # ── Transactional ─────────────────────────────────────────────────────────

    def send_sms(self, phone: str, message: str) -> bool:
        if not self._p:
            return self._no_provider()
        return self._p.send_plain(phone, message)

    def send_booking_notification(self, phone: str, booking_id: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy: Booking {booking_id} confirmed. Open the app for details.",
        )

    def send_order_update(self, phone: str, order_id: str, status: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy: Order {order_id} is now {status}. Track it in the app.",
        )

    def send_delivery_update(self, phone: str, delivery_id: str, status: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy Delivery {delivery_id}: {status}.",
        )

    def send_payment_received(self, phone: str, amount: float, ref: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy: Payment of ₦{amount:,.2f} received. Ref: {ref}.",
        )

    def send_rider_assignment(self, phone: str, delivery_id: str) -> bool:
        return self.send_sms(
            phone,
            f"Localy: New delivery {delivery_id} assigned. Open the app to accept.",
        )


# Singleton
sms_service = SMSService()