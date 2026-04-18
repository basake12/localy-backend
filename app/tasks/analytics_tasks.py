"""
app/tasks/analytics_tasks.py

FIXES vs previous version:
  1. aggregate_analytics task ADDED.
     Blueprint §16.2: "Hourly — pre-aggregate stats for admin dashboard."
     celery_app.py beat schedule references app.tasks.analytics_tasks.aggregate_analytics
     but only generate_daily_snapshot existed — the beat entry was silently dead.

  2. populate_daily_snapshot task ADDED.
     celery_app.py beat schedule also references app.tasks.analytics_tasks.populate_daily_snapshot
     for the nightly snapshot. This was missing.

  3. generate_daily_snapshot retained as a named alias for backward compatibility
     (in case it is called directly in some places). It now delegates to the
     populate_daily_snapshot logic.

  4. Blueprint §16.4 HARD RULE: datetime.now(timezone.utc) used throughout
     analytics_service. Confirmed here for task-layer correctness.
"""

import logging
from datetime import datetime, timezone

from celery import shared_task

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


# ── aggregate_analytics ───────────────────────────────────────────────────────

@shared_task(
    name="app.tasks.analytics_tasks.aggregate_analytics",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
)
def aggregate_analytics(self) -> dict:
    """
    Blueprint §16.2: aggregate_analytics — Hourly — pre-aggregate stats for
    admin dashboard.

    Scheduled every hour at :00 via celery_app beat schedule.
    Writes/updates aggregated metrics used by the admin dashboard KPI cards
    so the dashboard queries hit pre-computed values rather than raw tables.
    """
    db = SessionLocal()
    try:
        from app.services.analytics_service import analytics_service
        result = analytics_service.aggregate_hourly_stats(db)
        db.commit()
        logger.info("aggregate_analytics: completed successfully")
        return {"status": "ok", "aggregated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        db.rollback()
        logger.error("aggregate_analytics failed: %s", exc)
        raise self.retry(exc=exc)
    finally:
        db.close()


# ── populate_daily_snapshot ───────────────────────────────────────────────────

@shared_task(
    name="app.tasks.analytics_tasks.populate_daily_snapshot",
    bind=True,
    max_retries=3,
    default_retry_delay=180,
)
def populate_daily_snapshot(self) -> dict:
    """
    Blueprint §16.2: nightly analytics snapshot for admin panel (§11.5).
    Scheduled nightly at 02:00 WAT via celery_app beat schedule.

    Inserts a DailyAnalyticsSnapshot row for the previous calendar day
    with all required metrics: new_users, total_orders, new_revenue,
    completed_deliveries, new_reviews, total_messages.
    """
    db = SessionLocal()
    try:
        from app.services.analytics_service import analytics_service
        snapshot = analytics_service.create_daily_snapshot(db)
        db.commit()
        logger.info(
            "populate_daily_snapshot: snapshot created for %s",
            snapshot.snapshot_date,
        )
        return {
            "status":        "ok",
            "snapshot_date": str(snapshot.snapshot_date),
        }
    except Exception as exc:
        db.rollback()
        logger.error("populate_daily_snapshot failed: %s", exc)
        raise self.retry(exc=exc)
    finally:
        db.close()


# ── generate_daily_snapshot (retained for backward compatibility) ─────────────

@shared_task(
    name="app.tasks.analytics_tasks.generate_daily_snapshot",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def generate_daily_snapshot(self) -> dict:
    """
    Backward-compatible alias for populate_daily_snapshot.
    FIX: original task had this name but beat schedule called populate_daily_snapshot.
    Both now exist — beat schedule uses populate_daily_snapshot.
    """
    return populate_daily_snapshot.apply().get()