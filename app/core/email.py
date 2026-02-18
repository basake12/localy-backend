"""
Email service for Localy — powered by Resend.
Handles OTP delivery, password reset, and transactional emails.

Install: pip install resend
Docs:    https://resend.com/docs
"""
import resend
from typing import Optional
from app.config import settings


# ============================================
# INITIALISE RESEND
# ============================================

resend.api_key = settings.RESEND_API_KEY
_FROM = f"Localy <{settings.FROM_EMAIL}>"   # e.g. "Localy <noreply@localy.ng>"


# ============================================
# HTML LAYOUT HELPER
# ============================================

def _wrap(content: str, title: str = "Localy") -> str:
    """Wrap content in a branded Localy HTML email layout."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
          background:#f4f6f9;color:#1a1a2e;line-height:1.6}}
    .wrapper{{max-width:560px;margin:40px auto;background:#ffffff;
              border-radius:16px;overflow:hidden;
              box-shadow:0 4px 24px rgba(0,0,0,.08)}}
    .header{{background:linear-gradient(135deg,#32a88f,#28896f);
             padding:32px 40px;text-align:center}}
    .header h1{{color:#fff;font-size:28px;font-weight:700;letter-spacing:-0.5px}}
    .header p{{color:rgba(255,255,255,.8);font-size:14px;margin-top:4px}}
    .body{{padding:40px}}
    .body h2{{font-size:22px;font-weight:600;color:#0a0e27;margin-bottom:12px}}
    .body p{{color:#4a5066;font-size:15px;margin-bottom:16px}}
    .otp-box{{background:#f0faf7;border:2px dashed #32a88f;border-radius:12px;
              padding:24px;text-align:center;margin:24px 0}}
    .otp-code{{font-size:42px;font-weight:700;letter-spacing:12px;color:#32a88f;
               font-variant-numeric:tabular-nums}}
    .otp-note{{font-size:13px;color:#6b7399;margin-top:8px}}
    .btn{{display:inline-block;background:#32a88f;color:#fff;
          padding:14px 32px;border-radius:10px;text-decoration:none;
          font-weight:600;font-size:15px;margin:8px 0}}
    .divider{{border:none;border-top:1px solid #e8ecf4;margin:24px 0}}
    .footer{{background:#f4f6f9;padding:24px 40px;text-align:center;
             color:#9ea8c6;font-size:13px}}
    .footer a{{color:#32a88f;text-decoration:none}}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>Localy</h1>
      <p>If it's local, it's on Localy</p>
    </div>
    <div class="body">
      {content}
    </div>
    <div class="footer">
      <p>&copy; 2026 Localy &bull; Abuja, Nigeria</p>
      <p style="margin-top:8px">
        <a href="#">Privacy Policy</a> &bull; <a href="#">Terms of Service</a>
      </p>
    </div>
  </div>
</body>
</html>"""


# ============================================
# EMAIL TEMPLATES
# ============================================

def _tpl_email_otp(name: str, otp: str) -> str:
    return _wrap(f"""
      <h2>Verify your email address</h2>
      <p>Hi {name},</p>
      <p>Welcome to Localy! Enter the code below to verify your email and activate your account.</p>
      <div class="otp-box">
        <div class="otp-code">{otp}</div>
        <p class="otp-note">Expires in <strong>10 minutes</strong> &bull; Do not share this code</p>
      </div>
      <p>If you didn't create a Localy account, you can safely ignore this email.</p>
    """, "Verify Your Email — Localy")


def _tpl_password_reset_otp(name: str, otp: str) -> str:
    return _wrap(f"""
      <h2>Reset your password</h2>
      <p>Hi {name},</p>
      <p>We received a request to reset your Localy password. Use the code below to proceed.</p>
      <div class="otp-box">
        <div class="otp-code">{otp}</div>
        <p class="otp-note">Expires in <strong>30 minutes</strong> &bull; Do not share this code</p>
      </div>
      <hr class="divider">
      <p style="font-size:13px;color:#6b7399">
        If you didn't request a password reset, your account is safe — just ignore this email.
      </p>
    """, "Reset Your Password — Localy")


def _tpl_welcome(name: str, user_type: str) -> str:
    role_msg = {
        "customer": "Start exploring local hotels, restaurants, services, and more.",
        "business": "Your business listing is live. Complete your profile to attract more customers.",
        "rider": "Your rider account is active. Head to the app to start accepting deliveries.",
    }.get(user_type.lower(), "Your account is now active.")

    return _wrap(f"""
      <h2>Welcome to Localy, {name}! 🎉</h2>
      <p>Your account has been verified and is now fully active.</p>
      <p>{role_msg}</p>
      <p style="text-align:center;margin-top:24px">
        <a class="btn" href="{getattr(settings, 'APP_DEEP_LINK', 'https://localy.ng')}">
          Open Localy App
        </a>
      </p>
    """, f"Welcome to Localy, {name}!")


def _tpl_booking_confirmation(name: str, details: dict) -> str:
    return _wrap(f"""
      <h2>Booking Confirmed!</h2>
      <p>Hi {name}, your booking is confirmed. Here are the details:</p>
      <div style="background:#f0faf7;border-radius:10px;padding:20px;margin:20px 0">
        <p><strong>Booking ID:</strong> {details.get('id','N/A')}</p>
        <p><strong>Date:</strong> {details.get('date','N/A')}</p>
        <p><strong>Total:</strong> ₦{details.get('total',0):,.2f}</p>
      </div>
    """, "Booking Confirmed — Localy")


def _tpl_payment_receipt(name: str, details: dict) -> str:
    return _wrap(f"""
      <h2>Payment Received</h2>
      <p>Hi {name}, your payment was successful.</p>
      <div style="background:#f0faf7;border-radius:10px;padding:20px;margin:20px 0">
        <p><strong>Reference:</strong> {details.get('reference','N/A')}</p>
        <p><strong>Amount:</strong> ₦{details.get('amount',0):,.2f}</p>
        <p><strong>Date:</strong> {details.get('date','N/A')}</p>
        <p><strong>Method:</strong> {details.get('method','N/A')}</p>
      </div>
    """, "Payment Receipt — Localy")


# ============================================
# EMAIL SERVICE
# ============================================

class EmailService:
    """
    Resend-powered email service.

    Config required in settings:
        RESEND_API_KEY  — from https://resend.com
        FROM_EMAIL      — e.g. noreply@localy.ng  (must be a verified domain)
        FROM_NAME       — display name, default "Localy"
    """

    # ---------- low-level sender ----------

    def _send(self, to: str, subject: str, html: str) -> bool:
        """Send a single transactional email via Resend."""
        try:
            params: resend.Emails.SendParams = {
                "from": _FROM,
                "to": [to],
                "subject": subject,
                "html": html,
            }
            resp = resend.Emails.send(params)
            return bool(resp.get("id"))
        except Exception as exc:
            # Log but never raise — email failure should not break the request
            print(f"[EmailService] send failed to={to!r} subject={subject!r}: {exc}")
            return False

    # ---------- OTP / verification ----------

    def send_email_otp(self, to_email: str, name: str, otp: str) -> bool:
        """Send 6-digit email-OTP for account verification."""
        html = _tpl_email_otp(name, otp)
        return self._send(to_email, "Your Localy verification code", html)

    def send_password_reset_otp(self, to_email: str, name: str, otp: str) -> bool:
        """Send 6-digit OTP for password reset."""
        html = _tpl_password_reset_otp(name, otp)
        return self._send(to_email, "Reset your Localy password", html)

    # ---------- Lifecycle ----------

    def send_welcome(self, to_email: str, name: str, user_type: str) -> bool:
        """Send welcome email after full verification."""
        html = _tpl_welcome(name, user_type)
        return self._send(to_email, f"Welcome to Localy, {name}!", html)

    # ---------- Transactional ----------

    def send_booking_confirmation(
        self, to_email: str, name: str, details: dict
    ) -> bool:
        html = _tpl_booking_confirmation(name, details)
        return self._send(to_email, "Booking Confirmed — Localy", html)

    def send_payment_receipt(
        self, to_email: str, name: str, details: dict
    ) -> bool:
        html = _tpl_payment_receipt(name, details)
        return self._send(to_email, "Payment Receipt — Localy", html)


# Singleton — import this everywhere
email_service = EmailService()