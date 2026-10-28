"""
One safety gate for every inbound patient message that isn't the live assistant
(`agents/assistant.py` has its own call): the patient support form and the
staff inbox both come through `run_feedback_workflow`, which calls this first.

An emergency match short-circuits before any classifier or model sees the
message. Staff still get a ticket, flagged urgent, so the clinic follows up;
the patient gets the fixed guidance, never generated text. Only the rule id and
level are logged — never the message.
"""

import os
import sys
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from monitoring.logging_utils import log_event
from workflows.safety import screen_message, EMERGENCY

SAFETY_EMERGENCY = "safety_emergency"


def gate_message(text: str, patient_name: Optional[str] = None, source: str = "inbox") -> tuple[Optional[dict], Optional[dict]]:
    """Return (safety_hit, short_circuit_result). The second item is set only
    for emergencies; for urgent hits the caller continues and leads its reply
    with `urgent_prefix(hit)`."""
    hit = screen_message(text)
    if not hit:
        return None, None
    log_event("safety_screen", {"rule_id": hit["rule_id"], "level": hit["level"], "source": source})
    if hit["level"] != EMERGENCY:
        return hit, None
    ticket = db.create_support_ticket(f"[URGENT: safety screen, {hit['rule_id']}] {text}", patient_name=patient_name)
    return hit, {
        "category": SAFETY_EMERGENCY, "classification_method": "safety_screen",
        "message": urgent_prefix(hit).strip(), "ticket_number": ticket, "safety": hit,
    }


def urgent_prefix(hit: dict) -> str:
    return f"**{hit['title']}.** {hit['guidance']}\n\n"
