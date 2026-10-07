"""
Celery app for asynchronous processing of the clinical rules engine, so the
portal-facing API can return instantly (202 Accepted + tracking ID) instead
of blocking the request while the rule engine and EHR queries run.

Run a worker with:
    celery -A tasks.celery_app worker --loglevel=info

Requires a running Redis (or RabbitMQ) broker — set CELERY_BROKER_URL (or
REDIS_URL) in your .env. If unset, api/portal_integration.py automatically
falls back to FastAPI BackgroundTasks instead of erroring, so the demo
works without standing up Redis first.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CELERY_BROKER_URL, CELERY_RESULT_BACKEND

try:
    from celery import Celery

    celery_app = Celery(
        "healthcare_assistant",
        broker=CELERY_BROKER_URL or "redis://localhost:6379/0",
        backend=CELERY_RESULT_BACKEND or "redis://localhost:6379/0",
    )
    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
    )
    CELERY_AVAILABLE = True
except ImportError:
    celery_app = None
    CELERY_AVAILABLE = False
