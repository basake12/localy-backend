from celery import shared_task
from app.core.sms import sms_service


@shared_task(name="tasks.send_sms")
def send_sms_async(to_phone: str, message: str):
    """Send SMS asynchronously."""
    try:
        result = sms_service.send_sms(to_phone, message)
        return f"SMS sent to {to_phone}: {result}"
    except Exception as e:
        return f"Error sending SMS: {str(e)}"


@shared_task(name="tasks.send_otp_sms")
def send_otp_async(to_phone: str, otp: str):
    """Send OTP SMS asynchronously."""
    try:
        result = sms_service.send_otp(to_phone, otp)
        return f"OTP sent to {to_phone}: {result}"
    except Exception as e:
        return f"Error sending OTP: {str(e)}"


@shared_task(name="tasks.send_booking_sms")
def send_booking_notification_async(to_phone: str, booking_id: str):
    """Send booking notification SMS asynchronously."""
    try:
        result = sms_service.send_booking_notification(to_phone, booking_id)
        return f"Booking notification sent to {to_phone}: {result}"
    except Exception as e:
        return f"Error: {str(e)}"