"""
Feedback & Support Ticket Triage workflow.

Implements the three downstream handlers from the capstone spec:
  - Positive feedback -> a warm thank-you message
  - Negative feedback -> a new support ticket + an empathetic acknowledgment
  - Query -> look up an existing ticket's status

This module is transport-agnostic, same pattern as
workflows/prescription_refill.py: it's called by both the CrewAI agents
(tools/support_ticket_tool.py) and directly by tests, with the actual
message formats and business logic living in exactly one place.

Classification (deciding whether an incoming message IS positive feedback,
negative feedback, or a query) is a genuine judgment call on unstructured
text, so the real classifier is an LLM-backed CrewAI agent (see
agents/feedback_crew.py) -- that can't be unit-tested without a live model.
classify_message_heuristic() below is a keyword-based fallback: used when
the LLM stack isn't available, and as a fast, fully-deterministic path for
automated tests. It is NOT a substitute for the LLM classifier's judgment
on ambiguous or nuanced messages -- treat it as a safety net, not the
primary classifier.
"""

import os
import sys
import re
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from monitoring.monitor import track

POSITIVE_FEEDBACK = "positive_feedback"
NEGATIVE_FEEDBACK = "negative_feedback"
QUERY = "query"
PRESCRIPTION_REFILL = "prescription_refill"
VALID_CATEGORIES = {POSITIVE_FEEDBACK, NEGATIVE_FEEDBACK, QUERY, PRESCRIPTION_REFILL}


# ---------------------------------------------------------------------------
# Heuristic classifier (fallback / test-only -- see module docstring)
# ---------------------------------------------------------------------------

_TICKET_NUMBER_PATTERN = re.compile(r"\b(\d{6})\b")

_POSITIVE_WORDS = {
    "thank", "thanks", "thank you", "appreciate", "great", "excellent",
    "wonderful", "happy", "pleased", "resolved", "sorted", "helpful",
    "grateful", "awesome", "fantastic",
}
_QUERY_WORDS = {
    "status", "check", "update on", "what's happening", "where is",
    "any update", "following up", "follow up",
}

# Words indicating the message is a COMPLAINT, even if it also mentions a
# prescription/refill -- e.g. the spec's own Example 2 ("My medication
# refill still hasn't been filled.") must classify as negative feedback,
# not prescription_refill, matching the spec's documented agent path.
_NEGATIVE_SENTIMENT_WORDS = {
    "hasn't", "haven't", "still", "unacceptable", "frustrat", "disappoint",
    "terrible", "worst", "never", "waiting", "nobody", "no one", "ignored",
    "cancelled", "canceled", "mistake", "wrong", "issue", "problem", "broken",
}

# A neutral (non-complaining) mention of needing a prescription refilled --
# distinct from a complaint ABOUT a refill (negative feedback) or a status
# check on an existing ticket (query).
_REFILL_KEYWORDS = {
    "refill", "prescription", "renew my", "renewal", "need my medication",
    "get more of my", "running low on",
}


def extract_ticket_number(text: str) -> Optional[str]:
    """Pulls the first standalone 6-digit number out of free text, if any."""
    match = _TICKET_NUMBER_PATTERN.search(text)
    return match.group(1) if match else None


def classify_message_heuristic(text: str) -> str:
    """Keyword-based fallback classifier -- see module docstring for when
    to use this vs. the real LLM-backed classifier agent."""
    lowered = text.lower()

    has_ticket_number = bool(extract_ticket_number(text))
    has_query_language = any(w in lowered for w in _QUERY_WORDS)

    # A ticket number plus query language, or an explicit question about
    # status/checking, is clearly a query. Query language alone (no number)
    # is still a query -- e.g. "what's happening with my ticket?" -- just
    # one the downstream handler won't be able to resolve without asking
    # the user for the number.
    if has_query_language:
        return QUERY
    if has_ticket_number and ("?" in text or "check" in lowered or "status" in lowered):
        return QUERY

    if any(w in lowered for w in _POSITIVE_WORDS):
        return POSITIVE_FEEDBACK

    # A complaint mentioning a refill (e.g. the spec's own Example 2) is
    # still negative feedback -- checked BEFORE the neutral refill-request
    # check below, so tone takes precedence over topic.
    if any(w in lowered for w in _NEGATIVE_SENTIMENT_WORDS):
        return NEGATIVE_FEEDBACK

    if any(w in lowered for w in _REFILL_KEYWORDS):
        return PRESCRIPTION_REFILL

    return NEGATIVE_FEEDBACK


# ---------------------------------------------------------------------------
# Handlers (deterministic message formats, matching the spec exactly)
# ---------------------------------------------------------------------------

def handle_positive_feedback(patient_name: Optional[str] = None) -> dict:
    """Format: 'Thank you for your kind words, [PatientName]! We're
    delighted to assist you.'"""
    with track("feedback.positive_handler"):
        name_part = f", {patient_name}" if patient_name else ""
        message = f"Thank you for your kind words{name_part}! We're delighted to assist you."
        return {"category": POSITIVE_FEEDBACK, "message": message}


def handle_negative_feedback(complaint_text: str, patient_name: Optional[str] = None) -> dict:
    """Creates a new unresolved ticket and returns the spec's empathetic
    acknowledgment: 'We apologize for the inconvenience. A new ticket
    #[TicketNumber] has been generated, and our team will follow up
    shortly.'"""
    with track("feedback.negative_handler"):
        ticket_number = db.create_support_ticket(complaint_text, patient_name=patient_name)
        message = (
            f"We apologize for the inconvenience. A new ticket #{ticket_number} "
            f"has been generated, and our team will follow up shortly."
        )
        return {"category": NEGATIVE_FEEDBACK, "ticket_number": ticket_number, "message": message}


def handle_query(user_message: str) -> dict:
    """Extracts a ticket number from the message and returns the spec's
    format: 'Your ticket #[TicketNumber] is currently marked as:
    [Status].'"""
    with track("feedback.query_handler") as status:
        ticket_number = extract_ticket_number(user_message)
        if not ticket_number:
            status["success"] = False
            return {
                "category": QUERY, "ticket_number": None, "found": False,
                "message": "I couldn't find a ticket number in your message. "
                           "Could you share the 6-digit ticket number you'd like to check?",
            }

        ticket = db.get_support_ticket(ticket_number)
        if not ticket:
            status["success"] = False
            return {
                "category": QUERY, "ticket_number": ticket_number, "found": False,
                "message": f"I couldn't find a ticket matching #{ticket_number}. "
                           f"Please double-check the number.",
            }

        message = f"Your ticket #{ticket_number} is currently marked as: {ticket['status']}."
        return {"category": QUERY, "ticket_number": ticket_number, "found": True,
                "ticket": ticket, "message": message}


def route_and_handle(category: str, user_message: str, patient_name: Optional[str] = None) -> dict:
    """Single entry point: given an already-decided category (from either
    the LLM classifier agent or the heuristic fallback), dispatches to the
    right handler. Raises ValueError on an unrecognized category rather
    than silently guessing."""
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Unrecognized category '{category}'; expected one of {VALID_CATEGORIES}.")

    if category == POSITIVE_FEEDBACK:
        return handle_positive_feedback(patient_name)
    if category == NEGATIVE_FEEDBACK:
        return handle_negative_feedback(user_message, patient_name)
    return handle_query(user_message)
