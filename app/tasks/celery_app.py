"""
app/tasks/celery_app.py

FIXES vs previous version:
  1.  app.tasks.wallet_tasks added to include list.
      Blueprint §16.2 mandatory tasks: create_wallet, assign_virtual_account,
      process_refund, credit_referral_reward.

  2.  Beat schedule completed with all Blueprint §16.2 periodic tasks:
      - close_expired_jobs (daily) — Blueprint §8.6: closes jobs > 90 days
      - prune_old_messages (nightly) — Blueprint §10.1: delete messages > 90 days
      - prune_expired_stories (hourly) — Blueprint §8.5: deactivate expired stories
      - renew_subscription (daily) — Blueprint §16.2
      - aggregate_analytics (hourly) — Blueprint §16.2

  3.  Task paths corrected to use canonical module names.

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
        # ── Blueprint §16.2 mandatory task modules ──────────────────────
        "app.tasks.wallet_tasks",        # create_wallet, assign_virtual_account,
                                         # process_refund, credit_referral_reward
        "app.tasks.notification_tasks",  # send_welcome_sms, send_welcome_push,
                                         # send_push_notification, wallet_funded
        "app.tasks.referral_tasks",      # credit_referral_reward, expire_stale_referrals
        "app.tasks.subscription_tasks",  # renew_subscription, cleanup_expired_subscriptions
        "app.tasks.analytics_tasks",     # aggregate_analytics, populate_daily_snapshot
        # ── Supporting task modules ──────────────────────────────────────
        "app.tasks.email_tasks",
        "app.tasks.sms_tasks",
        "app.tasks.cleanup_tasks",       # prune_old_messages, prune_expired_stories,
                                         # close_expired_jobs, cleanup_expired_otps
    ],
)

# ─────────────────────────────────────────────────────────────────────────────
# BEAT SCHEDULE — Blueprint §16.2 periodic tasks
# All times in Africa/Lagos (WAT = UTC+1). enable_utc=True ensures Celery
# internally converts to UTC before scheduling.
# ─────────────────────────────────────────────────────────────────────────────
celery.conf.beat_schedule = {

    # ── Blueprint §16.2: aggregate_analytics — hourly ─────────────────────
    # "Hourly — pre-aggregate stats for admin dashboard"
    "aggregate-analytics-hourly": {
        "task":     "app.tasks.analytics_tasks.aggregate_analytics",
        "schedule": crontab(minute=0),  # every hour at :00
    },

    # ── Blueprint §16.2: prune_expired_stories — hourly ───────────────────
    # "Hourly — deactivate stories past expires_at"
    # Blueprint §8.5: stories disappear after 24 hours
    "prune-expired-stories-hourly": {
        "task":     "app.tasks.cleanup_tasks.prune_expired_stories",
        "schedule": crontab(minute=15),   # :15 of every hour
    },

    # ── Blueprint §16.2: renew_subscription — daily ───────────────────────
    # "Daily — check expiring subscriptions"
    "renew-subscriptions-daily": {
        "task":     "app.tasks.subscription_tasks.renew_subscription",
        "schedule": crontab(hour=3, minute=0),
    },

    # ── Blueprint §16.2: close_expired_jobs — daily ───────────────────────
    # "Daily — closes jobs older than 90 days"
    # Blueprint §8.6: "Vacancy auto-closes when marked filled.
    #   Celery task: close_expired_jobs (runs daily) — closes jobs older than 90 days"
    "close-expired-jobs-daily": {
        "task":     "app.tasks.cleanup_tasks.close_expired_jobs",
        "schedule": crontab(hour=1, minute=0),
    },

    # ── Blueprint §16.2: prune_old_messages — nightly ─────────────────────
    # "Nightly — delete messages older than 90 days"
    # Blueprint §10.1: "Chat history retained: 90 days"
    "prune-old-messages-nightly": {
        "task":     "app.tasks.cleanup_tasks.prune_old_messages",
        "schedule": crontab(hour=2, minute=30),
    },

    # ── Blueprint §16.2: aggregate_analytics / daily snapshot — nightly ───
    # "Hourly — pre-aggregate stats for admin dashboard"
    # Daily snapshot for admin (section 11.5)
    "nightly-analytics-snapshot": {
        "task":     "app.tasks.analytics_tasks.populate_daily_snapshot",
        "schedule": crontab(hour=2, minute=0),
    },

    # ── Referral expiry — nightly ──────────────────────────────────────────
    # "Expire stale PENDING referrals — nightly at 01:00 WAT"
    "expire-stale-referrals-nightly": {
        "task":     "app.tasks.referral_tasks.expire_stale_referrals",
        "schedule": crontab(hour=1, minute=0),
    },

    # ── Expired subscription cleanup — daily ──────────────────────────────
    "cleanup-expired-subscriptions-daily": {
        "task":     "app.tasks.subscription_tasks.cleanup_expired_subscriptions",
        "schedule": crontab(hour=3, minute=30),
    },

    # ── Expired OTP cleanup — every 15 minutes ────────────────────────────
    # Redis TTL handles this automatically, but a DB sweep ensures consistency
    "cleanup-expired-otps": {
        "task":     "app.tasks.cleanup_tasks.cleanup_expired_otps",
        "schedule": crontab(minute="*/15"),
    },

    # ── Old notifications cleanup — weekly ────────────────────────────────
    "cleanup-old-notifications-weekly": {
        "task":     "app.tasks.cleanup_tasks.cleanup_old_notifications",
        "schedule": crontab(hour=4, minute=0, day_of_week=0),  # Sunday 04:00 WAT
    },
}