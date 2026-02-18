from celery import shared_task
from app.core.database import SessionLocal
from app.services.analytics import analytics_service


@shared_task(name="tasks.generate_daily_snapshot")
def generate_daily_snapshot():
    """Generate daily analytics snapshot. Runs at midnight."""
    db = SessionLocal()
    try:
        snapshot = analytics_service.create_daily_snapshot(db)
        return f"Created snapshot for {snapshot.date}"
    except Exception as e:
        return f"Error creating snapshot: {str(e)}"
    finally:
        db.close()