"""
app/tasks/cleanup_tasks.py

FIXES vs previous version:
  1. prune_expired_stories task ADDED (renamed from expire_old_stories).
     celery_app.py beat schedule references app.tasks.cleanup_tasks.prune_expired_stories
     but the task was registered as "tasks.expire_old_stories" — name mismatch meant
     the beat entry was silently dead. Stories were never expiring.
     Blueprint §16.2: "Hourly — deactivate stories past expires_at"
     Blueprint §8.5: "Disappears after 24 hours."

  2. prune_old_messages task ADDED.
     Was completely missing — not defined anywhere in any file.
     Blueprint §16.2: "Nightly — delete messages older than 90 days"
     Blueprint §10.1: "Chat history retained: 90 days (messages.created_at <
     now() - INTERVAL '90 days' pruned by nightly Celery task)"

  3. close_expired_jobs task ADDED.
     Blueprint §16.2: "Daily — close jobs older than 90 days"
     Blueprint §8.6: "Celery task: close_expired_jobs (runs daily) —
     closes jobs older than 90 days"
     Was referenced in beat schedule but not defined in this file.

  4. datetime.utcnow() → datetime.now(timezone.utc) — §16.4 HARD RULE.
     Previous code used datetime.utcnow() in cleanup_old_notifications (line 41)
     and cleanup_expired_otps (line 56) — produced naive datetimes incompatible
     with PostgreSQL TIMESTAMPTZ columns.

  5. expire_old_stories retained as a deprecated alias so existing callers
     (if any) don't break during transition.
"""

import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import update

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: timezone-aware UTC. NEVER datetime.utcnow()."""
    return datetime.now(timezone.utc)


# ── prune_expired_stories ─────────────────────────────────────────────────────

@shared_task(name="app.tasks.cleanup_tasks.prune_expired_stories")
def prune_expired_stories() -> str:
    """
    Blueprint §16.2: prune_expired_stories — Hourly — deactivate stories past expires_at.
    Blueprint §8.5: Stories disappear after 24 hours.
    DB: stories.expires_at = created_at + 24h (timezone-aware TIMESTAMPTZ).
    Blueprint §16.4: datetime.now(timezone.utc) used.

    FIX: task was previously named 'tasks.expire_old_stories' — name mismatch with
    celery_app.py beat schedule entry which expects 'prune_expired_stories'.
    """
    db = SessionLocal()
    try:
        from app.models.stories_model import Story

        now = _utcnow()
        result = db.execute(
            update(Story)
            .where(
                Story.expires_at <= now,
                Story.is_active.is_(True),
            )
            .values(is_active=False)
        )
        db.commit()
        count = result.rowcount
        logger.info("prune_expired_stories: deactivated %d stories", count)
        return f"Deactivated {count} expired stories"
    except Exception as exc:
        db.rollback()
        logger.error("prune_expired_stories failed: %s", exc)
        return f"Error: {exc}"
    finally:
        db.close()


# Backward-compat alias — kept so any existing direct .delay() calls don't break
@shared_task(name="tasks.expire_old_stories")
def expire_old_stories() -> str:
    """Deprecated alias for prune_expired_stories. Use prune_expired_stories."""
    return prune_expired_stories.apply().get()


# ── prune_old_messages ────────────────────────────────────────────────────────

@shared_task(name="app.tasks.cleanup_tasks.prune_old_messages")
def prune_old_messages(retention_days: int = 90) -> str:
    """
    Blueprint §16.2: prune_old_messages — Nightly — delete messages older than 90 days.
    Blueprint §10.1:
      "Chat history retained: 90 days"
      "(messages.created_at < now() - INTERVAL '90 days' pruned by nightly Celery task)"
    Blueprint §16.4: datetime.now(timezone.utc) — NOT datetime.utcnow().

    FIX: This task was completely missing from all provided files.
    celery_app.py beat schedule references it nightly at 02:30 WAT.
    """
    db = SessionLocal()
    try:
        from app.models.chat_model import Message

        cutoff = _utcnow() - timedelta(days=retention_days)
        result = db.query(Message).filter(
            Message.created_at < cutoff,
        ).delete(synchronize_session=False)
        db.commit()
        logger.info(
            "prune_old_messages: deleted %d messages older than %d days",
            result, retention_days,
        )
        return f"Deleted {result} messages older than {retention_days} days"
    except Exception as exc:
        db.rollback()
        logger.error("prune_old_messages failed: %s", exc)
        return f"Error: {exc}"
    finally:
        db.close()


# ── close_expired_jobs ────────────────────────────────────────────────────────

@shared_task(name="app.tasks.cleanup_tasks.close_expired_jobs")
def close_expired_jobs(max_age_days: int = 90) -> str:
    """
    Blueprint §16.2: close_expired_jobs — Daily — closes jobs older than 90 days.
    Blueprint §8.6:
      "Vacancy auto-closes when marked filled (jobs.status = 'filled', updated_at = now())"
      "Celery task: close_expired_jobs (runs daily) — closes jobs older than 90 days"
    Blueprint §16.4: datetime.now(timezone.utc).

    FIX: Task was in beat schedule but not defined in any file.
    """
    db = SessionLocal()
    try:
        from app.models.jobs_model import Job

        cutoff = _utcnow() - timedelta(days=max_age_days)
        result = db.execute(
            update(Job)
            .where(
                Job.created_at <= cutoff,
                Job.status == "open",
            )
            .values(status="expired", updated_at=_utcnow())
        )
        db.commit()
        count = result.rowcount
        logger.info("close_expired_jobs: closed %d jobs older than %d days", count, max_age_days)
        return f"Closed {count} expired jobs"
    except Exception as exc:
        db.rollback()
        logger.error("close_expired_jobs failed: %s", exc)
        return f"Error: {exc}"
    finally:
        db.close()


# ── cleanup_expired_subscriptions ─────────────────────────────────────────────

@shared_task(name="tasks.cleanup_expired_subscriptions")
def cleanup_expired_subscriptions() -> str:
    """Mark expired subscriptions as inactive. Runs daily at 03:00 WAT."""
    db = SessionLocal()
    try:
        from app.crud.subscription_crud import subscription_crud
        count = subscription_crud.mark_expired(db)
        db.commit()
        return f"Marked {count} subscriptions as expired"
    except Exception as exc:
        db.rollback()
        logger.error("cleanup_expired_subscriptions failed: %s", exc)
        return f"Error: {exc}"
    finally:
        db.close()


# ── cleanup_old_notifications ─────────────────────────────────────────────────

@shared_task(name="tasks.cleanup_old_notifications")
def cleanup_old_notifications(days: int = 90) -> str:
    """Delete read notifications older than N days. Runs weekly."""
    db = SessionLocal()
    try:
        from app.models.notifications_model import Notification
        # FIX §16.4: was datetime.utcnow() — naive datetime
        cutoff = _utcnow() - timedelta(days=days)
        count = (
            db.query(Notification)
            .filter(
                Notification.created_at < cutoff,
                Notification.is_read.is_(True),
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return f"Deleted {count} notifications older than {days} days"
    except Exception as exc:
        db.rollback()
        logger.error("cleanup_old_notifications failed: %s", exc)
        return f"Error: {exc}"
    finally:
        db.close()


# ── cleanup_expired_otps ──────────────────────────────────────────────────────

@shared_task(name="tasks.cleanup_expired_otps")
def cleanup_expired_otps() -> str:
    """
    Null out expired OTP codes from the users table. Runs every 15 min.
    Blueprint §3.1: OTP TTL = 5 minutes (stored in Redis with key otp:{phone}).
    Redis TTL handles the primary expiry; this task is a DB consistency sweep.
    FIX §16.4: was datetime.utcnow() — produces naive datetime.
    """
    db = SessionLocal()
    try:
        from app.models.user_model import User
        # FIX §16.4: was datetime.utcnow()
        result = db.execute(
            update(User)
            .where(
                User.otp_expires_at < _utcnow(),
                User.otp_code.isnot(None),
            )
            .values(otp_code=None, otp_expires_at=None)
        )
        db.commit()
        return f"Cleared OTP codes for {result.rowcount} users"
    except Exception as exc:
        db.rollback()
        logger.error("cleanup_expired_otps failed: %s", exc)
        return f"Error: {exc}"
    finally:
        db.close()