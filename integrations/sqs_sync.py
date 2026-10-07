"""
Live status sync to the web portal, part 2: AWS SQS.

Where the webhook (portal_webhook.py) is a direct push notification, SQS is
for durable, at-least-once delivery the portal's own backend can poll/
consume on its own schedule — useful when the portal team doesn't want to
expose a webhook receiver, or wants replay/retry semantics AWS gives you for
free. Both are wired into the same task completion event
(tasks/refill_tasks.py calls both); use whichever fits the portal's
architecture, or both for redundancy.

Degrades gracefully: if boto3 isn't installed or AWS_SQS_QUEUE_URL isn't
configured, this is a documented no-op rather than a crash.
"""

import os
import sys
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import AWS_SQS_QUEUE_URL, AWS_REGION
from monitoring.logging_utils import log_event


def publish_status_to_sqs(tracking_id: str, patient_id: int, status: str, result: dict) -> bool:
    """Returns True if a message was actually published, False if skipped
    or failed. Never raises."""
    if not AWS_SQS_QUEUE_URL:
        log_event("sqs_publish_skipped", {
            "reason": "AWS_SQS_QUEUE_URL not configured", "tracking_id": tracking_id,
        })
        return False

    message = {
        "event": "refill_status_changed",
        "tracking_id": tracking_id,
        "patient_id": patient_id,
        "status": status,
        "result": result,
    }

    try:
        import boto3
        client = boto3.client("sqs", region_name=AWS_REGION)
        client.send_message(
            QueueUrl=AWS_SQS_QUEUE_URL,
            MessageBody=json.dumps(message),
            MessageAttributes={
                "tracking_id": {"DataType": "String", "StringValue": tracking_id},
                "status": {"DataType": "String", "StringValue": status},
            },
        )
        log_event("sqs_publish_sent", {"tracking_id": tracking_id, "status": status})
        return True
    except ImportError:
        log_event("sqs_publish_error", {"tracking_id": tracking_id, "error": "boto3 not installed"})
        return False
    except Exception as e:
        log_event("sqs_publish_error", {"tracking_id": tracking_id, "error": str(e)})
        return False
