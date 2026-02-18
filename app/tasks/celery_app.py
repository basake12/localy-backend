"""
celery_app.py

Single Celery instance consumed by all task modules.
Run workers with:  celery -A app.celery_app.celery worker --loglevel=info
Beat (scheduler):  celery -A app.celery_app.celery beat  --loglevel=info
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
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Africa/Lagos",
    enable_utc=True,
    # Autodiscover task modules inside app/tasks/
    include=["app.tasks.analytics_tasks"],
)

# ---------------------------------------------------------------------------
# BEAT SCHEDULE  —  cron-style periodic tasks
# ---------------------------------------------------------------------------
celery.conf.beat_schedule = {
    "nightly-analytics-snapshot": {
        "task":     "app.tasks.analytics_tasks.populate_daily_snapshot",
        "schedule": crontab(hour=2, minute=0),   # 02:00 WAT every night
    },
}