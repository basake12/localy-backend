from celery import shared_task
from app.core.email import email_service


@shared_task(name="tasks.send_email")
def send_email_async(to_email: str, subject: str, html_content: str):
    """Send email asynchronously."""
    try:
        result = email_service.send_email(to_email, subject, html_content)
        return f"Email sent to {to_email}: {result}"
    except Exception as e:
        return f"Error sending email: {str(e)}"


@shared_task(name="tasks.send_verification_email")
def send_verification_email_async(to_email: str, name: str, otp: str):
    """Send verification email asynchronously."""
    try:
        result = email_service.send_verification_email(to_email, name, otp)
        return f"Verification email sent to {to_email}: {result}"
    except Exception as e:
        return f"Error sending verification email: {str(e)}"


@shared_task(name="tasks.send_booking_confirmation_email")
def send_booking_confirmation_async(to_email: str, name: str, booking_details: dict):
    """Send booking confirmation email asynchronously."""
    try:
        result = email_service.send_booking_confirmation(to_email, name, booking_details)
        return f"Booking confirmation sent to {to_email}: {result}"
    except Exception as e:
        return f"Error: {str(e)}"


@shared_task(name="tasks.send_order_confirmation_email")
def send_order_confirmation_async(to_email: str, name: str, order_details: dict):
    """Send order confirmation email asynchronously."""
    try:
        result = email_service.send_order_confirmation(to_email, name, order_details)
        return f"Order confirmation sent to {to_email}: {result}"
    except Exception as e:
        return f"Error: {str(e)}"