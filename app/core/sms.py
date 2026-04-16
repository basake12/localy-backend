"""
app/core/sms.py

SMS service for Localy — powered by Termii (Blueprint §16.1 / §3.1).

FIXES vs previous version:
  1.  send_otp default expiry_minutes: 10 → 5.
      Blueprint §3.1 Step 1: "OTP is 6-digit, TTL = 5 minutes."

  2.  send_welcome added — Blueprint §3 POST-REGISTRATION:
      "send_welcome_sms task: 'Welcome to Localy, [Name]! Your account is ready.'"

  3.  TwilioSMS marked as non-blueprint extension.
      Blueprint §16.1: only Termii is specified as the SMS gateway.
      Twilio is kept as a fallback for non-Nigerian numbers but is not
      part of the blueprint spec.

NOTE: Termii is the ONLY SMS provider specified in Blueprint §16.1.
All references: TERMII_API_KEY, TERMII_SENDER_ID, TERMII_API_URL.
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
    return phone


# ─── Termii Provider (Blueprint §16.1 — PRIMARY gateway) ─────────────────────

class TermiiSMS:
    """
    Termii SMS gateway.
    Blueprint §16.1: TERMII_API_KEY, TERMII_SENDER_ID, TERMII_API_URL.
    """

    BASE = "https://api.ng.termii.com/api"

    def __init__(self):
        self.api_key   = getattr(settings, "TERMII_API_KEY", "")
        self.sender_id = getattr(settings, "TERMII_SENDER_ID", "Localy")

    def _post(self, path: str, payload: dict) -> tuple[bool, dict]:
        try:
            r    = requests.post(f"{self.BASE}{path}", json=payload, timeout=10)
            data = r.json() if r.content else {}
            return r.ok, data
        except Exception:
            log.exception("TermiiSMS: POST %s failed", path)
            return False, {}

    def _fmt(self, phone: str) -> str:
        """Termii expects digits only with country code, no '+'."""
        return format_nigerian_phone(phone).lstrip("+")

    def send_plain(self, phone: str, message: str) -> bool:
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

    def send_otp(self, phone: str, otp: str, expiry_minutes: int = 5) -> bool:
        """
        Send OTP via Termii.
        Blueprint §3.1 Step 1: "OTP is 6-digit, TTL = 5 minutes."
        Default expiry_minutes = 5 (was 10 — FIXED).
        """
        message = (
            f"Your Localy verification code is {otp}. "
            f"Valid for {expiry_minutes} minutes. Do not share."
        )
        return self.send_plain(phone, message)

    def send_password_reset(self, phone: str, otp: str) -> bool:
        """Send password reset OTP. Blueprint §3.2."""
        message = (
            f"Your Localy password reset code is {otp}. "
            "Valid for 5 minutes. Ignore if you didn't request this."
        )
        return self.send_plain(phone, message)

    def send_pin_unlock(self, phone: str, name: str, otp: str) -> bool:
        """Send PIN unlock code. Blueprint §3.3."""
        message = (
            f"Hi {name}, your Localy PIN unlock code is {otp}. "
            "Valid for 5 minutes. Ignore if you didn't request this."
        )
        return self.send_plain(phone, message)

    def send_welcome(self, phone: str, name: str) -> bool:
        """
        Send welcome SMS.
        Blueprint §3 POST-REGISTRATION:
          "send_welcome_sms task: 'Welcome to Localy, [Name]! Your account is ready.'"
        """
        message = f"Welcome to Localy, {name}! Your account is ready."
        return self.send_plain(phone, message)


# ─── Twilio Fallback (NOT in blueprint — extension only) ─────────────────────

class TwilioSMS:
    """
    Twilio — non-blueprint fallback for international numbers.
    Blueprint §16.1 specifies Termii only. Use this only if explicitly
    decided outside the blueprint for non-Nigerian numbers.
    """

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
                    "TwilioSMS: failed. phone=%r status=%d body=%s",
                    phone, r.status_code, r.text[:200],
                )
            return success
        except Exception:
            log.exception("TwilioSMS: send_plain error. phone=%r", phone)
            return False

    def send_otp(self, phone: str, otp: str, expiry_minutes: int = 5) -> bool:
        return self.send_plain(
            phone,
            f"Your Localy code: {otp}. Valid {expiry_minutes} min. Don't share.",
        )

    def send_password_reset(self, phone: str, otp: str) -> bool:
        return self.send_plain(phone, f"Localy password reset code: {otp}. Valid 5 min.")

    def send_pin_unlock(self, phone: str, name: str, otp: str) -> bool:
        return self.send_plain(
            phone, f"Hi {name}, Localy PIN unlock code: {otp}. Valid 5 min."
        )

    def send_welcome(self, phone: str, name: str) -> bool:
        return self.send_plain(phone, f"Welcome to Localy, {name}! Your account is ready.")


# ─── SMS Service (auto-selects provider) ──────────────────────────────────────

class SMSService:
    """
    Unified SMS service.
    Provider priority (Blueprint §16.1):
      1. Termii  — if TERMII_API_KEY is set (required per blueprint)
      2. Twilio  — if TWILIO_ACCOUNT_SID is set (non-blueprint fallback)
    """

    def __init__(self):
        if getattr(settings, "TERMII_API_KEY", ""):
            self._p = TermiiSMS()
            log.info("SMSService: using Termii (blueprint primary)")
        elif getattr(settings, "TWILIO_ACCOUNT_SID", ""):
            self._p = TwilioSMS()
            log.info("SMSService: using Twilio (non-blueprint fallback)")
        else:
            self._p = None
            log.warning("SMSService: no provider configured — SMS disabled")

    def _no_provider(self) -> bool:
        log.error("SMSService: send attempted but no provider is configured")
        return False

    # ── OTP — Blueprint §3.1 ─────────────────────────────────────────────────

    def send_otp(self, phone: str, otp: str, expiry_minutes: int = 5) -> bool:
        """
        Send OTP. Default TTL = 5 minutes (Blueprint §3.1 Step 1).
        """
        if settings.DEBUG:
            log.info("DEVELOPMENT: OTP for %s → %s", phone, otp)
            return True
        if not self._p:
            return self._no_provider()
        return self._p.send_otp(phone, otp, expiry_minutes)

    def send_password_reset(self, phone: str, otp: str) -> bool:
        if settings.DEBUG:
            log.info("DEVELOPMENT: Password reset OTP for %s → %s", phone, otp)
            return True
        if not self._p:
            return self._no_provider()
        return self._p.send_password_reset(phone, otp)

    def send_pin_unlock(self, phone: str, name: str, otp: str) -> bool:
        if settings.DEBUG:
            log.info("DEVELOPMENT: PIN unlock OTP for %s → %s", phone, otp)
            return True
        if not self._p:
            return self._no_provider()
        return self._p.send_pin_unlock(phone, name, otp)

    def send_welcome(self, phone: str, name: str) -> bool:
        """
        Welcome SMS. Blueprint §3 POST-REGISTRATION Celery task:
          "send_welcome_sms: 'Welcome to Localy, [Name]! Your account is ready.'"
        """
        if settings.DEBUG:
            log.info("DEVELOPMENT: Welcome SMS for %s", phone)
            return True
        if not self._p:
            return self._no_provider()
        return self._p.send_welcome(phone, name)

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

    def send_wallet_credited(self, phone: str, amount: float) -> bool:
        """Wallet funding notification. Blueprint §5.1."""
        return self.send_sms(
            phone,
            f"Your Localy wallet has been funded with ₦{amount:,.2f}.",
        )


# Singleton
sms_service = SMSService()