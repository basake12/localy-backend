# app/tasks/celery_app.py
"""
Single Celery instance consumed by all task modules.

Start worker:    celery -A app.tasks.celery_app.celery worker --loglevel=info
Start beat:      celery -A app.tasks.celery_app.celery beat  --loglevel=info
Combined (dev):  celery -A app.tasks.celery_app.celery worker --beat --loglevel=info
"""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery = Celery(
    "localy",
    broker=str(settings.CELERY_BROKER_URL),
    backend=str(settings.CELERY_RESULT_BACKEND),
)

celery.conf.update(
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Africa/Lagos",
    enable_utc=True,
    include=[
        "app.tasks.email_tasks",
        "app.tasks.sms_tasks",
        "app.tasks.cleanup_tasks",
        "app.tasks.analytics_tasks",
        "app.tasks.subscription_tasks",
        "app.tasks.referral_tasks",
        "app.tasks.notification_tasks",      # push / email / sms dispatch
    ],
)

# ─────────────────────────────────────────────
# BEAT SCHEDULE — periodic tasks
# ─────────────────────────────────────────────
celery.conf.beat_schedule = {
    # Analytics snapshot — nightly at 02:00 WAT
    "nightly-analytics-snapshot": {
        "task":     "app.tasks.analytics_tasks.populate_daily_snapshot",
        "schedule": crontab(hour=2, minute=0),
    },
    # Expire 24-hour stories — every 30 minutes
    "expire-old-stories": {
        "task":     "tasks.expire_old_stories",
        "schedule": crontab(minute="*/30"),
    },
    # Clean up expired subscriptions — daily at 03:00 WAT
    "cleanup-expired-subscriptions": {
        "task":     "tasks.cleanup_expired_subscriptions",
        "schedule": crontab(hour=3, minute=0),
    },
    # Purge read notifications older than 90 days — weekly Sunday 04:00 WAT
    "cleanup-old-notifications": {
        "task":     "tasks.cleanup_old_notifications",
        "schedule": crontab(hour=4, minute=0, day_of_week=0),
    },
    # Clear expired OTPs — every 15 minutes
    "cleanup-expired-otps": {
        "task":     "tasks.cleanup_expired_otps",
        "schedule": crontab(minute="*/15"),
    },
    # Expire stale PENDING referrals — nightly at 01:00 WAT
    "expire-stale-referrals": {
        "task":     "app.tasks.referral_tasks.expire_stale_referrals",
        "schedule": crontab(hour=1, minute=0),
    },
}