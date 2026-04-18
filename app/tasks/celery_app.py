"""
app/tasks/celery_app.py

FIXES vs previous version:
  1. app.tasks.wallet_tasks verified in include list.
     Blueprint §16.2 mandatory tasks: create_wallet, assign_virtual_account,
     process_refund, transcode_reel.
     wallet_tasks.py now exists (was previously missing entirely).

  2. Beat schedule task names CORRECTED:
     - prune-expired-stories: was app.tasks.cleanup_tasks.prune_expired_stories
       (task existed as 'tasks.expire_old_stories' — name mismatch). Now fixed
       in cleanup_tasks.py — task registered as 'app.tasks.cleanup_tasks.prune_expired_stories'.
     - nightly-analytics-snapshot: was app.tasks.analytics_tasks.populate_daily_snapshot
       (task didn't exist). Now added to analytics_tasks.py.
     - aggregate-analytics-hourly: was app.tasks.analytics_tasks.aggregate_analytics
       (task didn't exist). Now added to analytics_tasks.py.
     - close-expired-jobs: was app.tasks.cleanup_tasks.close_expired_jobs
       (task didn't exist in cleanup_tasks.py). Now added.
     - prune-old-messages: was app.tasks.cleanup_tasks.prune_old_messages
       (task didn't exist). Now added.

  3. app.tasks.promotions_tasks added to include list (was missing).

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
        # ── Blueprint §16.2 mandatory task modules ──────────────────────────
        "app.tasks.wallet_tasks",        # create_wallet, assign_virtual_account,
                                         # process_refund, transcode_reel
        "app.tasks.notification_tasks",  # send_welcome_sms, send_welcome_push,
                                         # send_push_notification, dispatch_push_task
        "app.tasks.referral_tasks",      # credit_referral_reward, expire_stale_referrals
        "app.tasks.subscription_tasks",  # renew_subscription, cleanup_expired_subscriptions,
                                         # downgrade_expired_subscriptions
        "app.tasks.analytics_tasks",     # aggregate_analytics, populate_daily_snapshot
        # ── Supporting task modules ──────────────────────────────────────────
        "app.tasks.email_tasks",
        "app.tasks.sms_tasks",
        "app.tasks.cleanup_tasks",       # prune_old_messages, prune_expired_stories,
                                         # close_expired_jobs, cleanup_expired_otps
        "app.tasks.promotions_tasks",    # sync_promotion_statuses, notify_streak_milestones
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
        "schedule": crontab(minute=0),
    },

    # ── Blueprint §16.2: prune_expired_stories — hourly ───────────────────
    # "Hourly — deactivate stories past expires_at"
    # Blueprint §8.5: stories disappear after 24 hours.
    # FIX: task name corrected in cleanup_tasks.py to match this entry.
    "prune-expired-stories-hourly": {
        "task":     "app.tasks.cleanup_tasks.prune_expired_stories",
        "schedule": crontab(minute=15),
    },

    # ── Blueprint §16.2: renew_subscription — daily ───────────────────────
    # "Daily — check expiring subscriptions"
    "renew-subscriptions-daily": {
        "task":     "app.tasks.subscription_tasks.renew_subscription",
        "schedule": crontab(hour=3, minute=0),
    },

    # ── Blueprint §8.1: downgrade past grace period — daily ───────────────
    # "Failed payment: 7-day grace period → auto-downgrade to Free on day 8."
    "downgrade-expired-subscriptions-daily": {
        "task":     "app.tasks.subscription_tasks.downgrade_expired_subscriptions",
        "schedule": crontab(hour=3, minute=15),
    },

    # ── Blueprint §16.2: close_expired_jobs — daily ───────────────────────
    # "Daily — closes jobs older than 90 days"
    # Blueprint §8.6: "close_expired_jobs (runs daily)"
    # FIX: task was in beat schedule but not defined in cleanup_tasks.py. Fixed.
    "close-expired-jobs-daily": {
        "task":     "app.tasks.cleanup_tasks.close_expired_jobs",
        "schedule": crontab(hour=1, minute=0),
    },

    # ── Blueprint §16.2: prune_old_messages — nightly ─────────────────────
    # "Nightly — delete messages older than 90 days"
    # Blueprint §10.1: "Chat history retained: 90 days"
    # FIX: task was completely missing. Added to cleanup_tasks.py.
    "prune-old-messages-nightly": {
        "task":     "app.tasks.cleanup_tasks.prune_old_messages",
        "schedule": crontab(hour=2, minute=30),
    },

    # ── Blueprint §16.2: daily snapshot — nightly ─────────────────────────
    # FIX: task populate_daily_snapshot was missing. Added to analytics_tasks.py.
    "nightly-analytics-snapshot": {
        "task":     "app.tasks.analytics_tasks.populate_daily_snapshot",
        "schedule": crontab(hour=2, minute=0),
    },

    # ── Referral expiry — nightly ──────────────────────────────────────────
    "expire-stale-referrals-nightly": {
        "task":     "app.tasks.referral_tasks.expire_stale_referrals",
        "schedule": crontab(hour=1, minute=30),
    },

    # ── Expired subscription cleanup — daily ──────────────────────────────
    "cleanup-expired-subscriptions-daily": {
        "task":     "app.tasks.subscription_tasks.cleanup_expired_subscriptions",
        "schedule": crontab(hour=3, minute=30),
    },

    # ── Expired OTP cleanup — every 15 minutes ────────────────────────────
    # Redis TTL handles this automatically; this DB sweep ensures consistency.
    "cleanup-expired-otps": {
        "task":     "tasks.cleanup_expired_otps",
        "schedule": crontab(minute="*/15"),
    },

    # ── Old notifications cleanup — weekly ────────────────────────────────
    "cleanup-old-notifications-weekly": {
        "task":     "tasks.cleanup_old_notifications",
        "schedule": crontab(hour=4, minute=0, day_of_week=0),  # Sunday 04:00 WAT
    },

    # ── Promotion status sync — hourly ────────────────────────────────────
    "sync-promotion-statuses-hourly": {
        "task":     "tasks.sync_promotion_statuses",
        "schedule": crontab(minute=45),
    },

    # ── Streak milestone notifications — daily ────────────────────────────
    "notify-streak-milestones-daily": {
        "task":     "tasks.notify_streak_milestones",
        "schedule": crontab(hour=10, minute=0),  # 10:00 WAT (business hours)
    },

    # ── Subscription expiry reminders — daily ─────────────────────────────
    "send-expiry-reminders-daily": {
        "task":     "app.tasks.subscription_tasks.send_expiry_reminders",
        "schedule": crontab(hour=9, minute=0),  # 09:00 WAT
    },
}