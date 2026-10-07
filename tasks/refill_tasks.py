"""
Background task: runs the clinical rules engine for a portal-originated
refill request, then syncs the result back to the portal via webhook + SQS.

This is the "Background Execution" step in the integration workflow — the
API layer (api/portal_integration.py) enqueues this and returns immediately;
this function does the actual (potentially slow) EHR/rule-engine work.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from workflows.prescription_refill import process_refill_request_authenticated
from integrations.portal_webhook import notify_portal_webhook
from integrations.sqs_sync import publish_status_to_sqs
from monitoring.monitor import track

_RESULT_STATUS_MAP = {
    "processed": "sent_to_pharmacy",
    "routed_to_review": "pending_provider_review",
    "unauthorized": "unauthorized",
    "error": "error",
}


def _execute(tracking_id: str, patient_id: int, prescription_id: int, pharmacy_id: str) -> dict:
    """The actual work, factored out so it can run either as a real Celery
    task or as a plain function under FastAPI BackgroundTasks — same logic
    either way, just a different execution context."""
    with track("tasks.process_clinical_rules_engine", meta={"tracking_id": tracking_id}) as status:
        db.update_refill_task_status(tracking_id, "processing")

        result = process_refill_request_authenticated(patient_id, prescription_id, pharmacy_id)
        final_status = _RESULT_STATUS_MAP.get(result["status"], result["status"])
        if final_status in ("unauthorized", "error"):
            status["success"] = False

        db.update_refill_task_status(tracking_id, final_status, result)

        # Step 4 of the integration workflow: push the update back to the portal
        notify_portal_webhook(tracking_id, patient_id, final_status, result)
        publish_status_to_sqs(tracking_id, patient_id, final_status, result)

        try:
            from api.websocket_manager import broadcast_status_sync
            broadcast_status_sync(patient_id, {"tracking_id": tracking_id, "status": final_status, "result": result})
        except ImportError:
            pass

        return result


try:
    from tasks.celery_app import celery_app, CELERY_AVAILABLE

    if CELERY_AVAILABLE:
        @celery_app.task(name="process_clinical_rules_engine", bind=True, max_retries=2)
        def process_clinical_rules_engine(self, tracking_id: str, patient_id: int,
                                           prescription_id: int, pharmacy_id: str) -> dict:
            return _execute(tracking_id, patient_id, prescription_id, pharmacy_id)
    else:
        process_clinical_rules_engine = None
except ImportError:
    CELERY_AVAILABLE = False
    process_clinical_rules_engine = None


def run_synchronously_as_fallback(tracking_id: str, patient_id: int, prescription_id: int,
                                   pharmacy_id: str) -> dict:
    """Used by api/portal_integration.py when Celery isn't configured —
    runs the exact same logic via FastAPI BackgroundTasks instead."""
    return _execute(tracking_id, patient_id, prescription_id, pharmacy_id)
