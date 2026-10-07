"""
CrewAI tools for the feedback & support-ticket triage workflow. Thin
wrappers around workflows/feedback_support.py, same pattern as
tools/prescription_tool.py.
"""

from typing import Optional
from crewai.tools import tool

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workflows import feedback_support as fs
from monitoring.monitor import track


@tool("send_thank_you_message")
def send_thank_you_message(patient_name: Optional[str] = None) -> str:
    """Look up the facts needed to acknowledge positive feedback. Pass the
    patient/customer's name if known. Returns a plain-text summary (NOT a
    polished message) -- use it to write your own genuine, warm reply in
    your own words; do not just copy this raw output verbatim. Use this
    ONLY after the message has been classified as positive feedback."""
    with track("feedback_tool.send_thank_you_message"):
        name_part = f"Patient/customer name: {patient_name}." if patient_name else "No name given."
        return f"Feedback acknowledged as positive. {name_part}"


@tool("create_support_ticket")
def create_support_ticket(complaint_text: str, patient_name: Optional[str] = None) -> str:
    """Create a new unresolved support ticket for a negative-feedback
    complaint. Returns the resulting facts (ticket number, status) as
    plain text -- NOT a polished message. Use these facts to write your
    own genuine, empathetic reply in your own words; the ticket number
    MUST appear in your reply exactly as given here, but do not just copy
    this raw output verbatim as your final answer. Use this ONLY after
    the message has been classified as negative feedback -- pass the
    complaint text itself as complaint_text."""
    with track("feedback_tool.create_support_ticket"):
        result = fs.handle_negative_feedback(complaint_text, patient_name)
        name_part = f" for {patient_name}" if patient_name else ""
        return (f"Ticket created{name_part}. ticket_number={result['ticket_number']}, "
                f"status=Unresolved, complaint=\"{complaint_text}\"")


@tool("get_ticket_status")
def get_ticket_status(user_message: str) -> str:
    """Extract a 6-digit ticket number from the user's message and look up
    its current status. Returns the facts as plain text -- NOT a polished
    message. Use these facts to write your own clear, genuine reply in
    your own words; the ticket number and status MUST appear in your
    reply exactly as given here (never alter or guess a status), but do
    not just copy this raw output verbatim. Use this ONLY after the
    message has been classified as a query. Pass the original user
    message (the tool extracts the ticket number itself)."""
    with track("feedback_tool.get_ticket_status") as status:
        result = fs.handle_query(user_message)
        if not result.get("found"):
            status["success"] = False
            if result.get("ticket_number"):
                return (f"No ticket found matching ticket_number={result['ticket_number']}. "
                        f"Tell the user this number doesn't match any ticket on file.")
            return "No ticket number could be found in the user's message. Ask them for it."
        ticket = result["ticket"]
        return (f"ticket_number={ticket['ticket_number']}, status={ticket['status']}, "
                f"patient_name={ticket.get('patient_name') or 'unknown'}")


FEEDBACK_TOOLS = [send_thank_you_message, create_support_ticket, get_ticket_status]
