"""
Live status sync to the web portal, part 1: webhooks.

When a refill task transitions status (queued -> processing -> sent_to_
pharmacy / pending_provider_review), this posts a signed payload to the
portal's own webhook endpoint so its UI/database can reflect the change
without polling.

Degrades gracefully: if PORTAL_WEBHOOK_URL isn't configured, this is a
documented no-op (logged, not a crash) — consistent with how the rest of
this project treats optional external integrations (Tavily, FAISS
embeddings, etc.), so the demo keeps working without every piece of
production infra wired up.
"""

import os
import sys
import hmac
import hashlib
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PORTAL_WEBHOOK_URL, PORTAL_WEBHOOK_SECRET
from monitoring.logging_utils import log_event


def _sign_payload(payload_bytes: bytes) -> str:
    if not PORTAL_WEBHOOK_SECRET:
        return ""
    return hmac.new(PORTAL_WEBHOOK_SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()


def notify_portal_webhook(tracking_id: str, patient_id: int, status: str, result: dict) -> bool:
    """Returns True if a webhook was actually sent, False if skipped
    (not configured) or failed. Never raises — a portal outage shouldn't
    take down refill processing."""
    if not PORTAL_WEBHOOK_URL:
        log_event("portal_webhook_skipped", {
            "reason": "PORTAL_WEBHOOK_URL not configured", "tracking_id": tracking_id,
        })
        return False

    payload = {
        "tracking_id": tracking_id,
        "patient_id": patient_id,
        "status": status,
        "result": result,
    }
    body = json.dumps(payload).encode()
    signature = _sign_payload(body)

    try:
        import httpx
        headers = {"Content-Type": "application/json"}
        if signature:
            headers["X-Portal-Signature"] = signature
        with httpx.Client(timeout=10) as client:
            resp = client.post(PORTAL_WEBHOOK_URL, content=body, headers=headers)
            resp.raise_for_status()
        log_event("portal_webhook_sent", {"tracking_id": tracking_id, "status": status})
        return True
    except ImportError:
        log_event("portal_webhook_error", {"tracking_id": tracking_id, "error": "httpx not installed"})
        return False
    except Exception as e:
        log_event("portal_webhook_error", {"tracking_id": tracking_id, "error": str(e)})
        return False
