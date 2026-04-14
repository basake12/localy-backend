from celery import shared_task
from app.core.database import SessionLocal
from app.services.analytics_service import analytics_service  # ✅ correct module path


@shared_task(
    name="tasks.generate_daily_snapshot",
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # seconds between retries
)
def generate_daily_snapshot(self):
    """Generate daily analytics snapshot.

    Scheduled to run at midnight via Celery Beat.
    Retries up to 3 times on transient failures.
    """
    db = SessionLocal()
    try:
        snapshot = analytics_service.create_daily_snapshot(db)
        return {"status": "ok", "snapshot_date": str(snapshot.snapshot_date)}
    except Exception as exc:
        db.rollback()
        # Re-raise so Celery marks the task as FAILED and triggers retry
        raise self.retry(exc=exc)
    finally:
        db.close()