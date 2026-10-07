"""
CrewAI tools for the Prescription Refill Agent (the multi-turn confirmation
flow and controlled-substance schedule engine). Thin wrappers around
workflows/prescription_refill_agent.py, returning FACTS for the agent to
phrase in its own genuine words -- same pattern as
tools/support_ticket_tool.py.
"""

from crewai.tools import tool

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workflows import prescription_refill_agent as ra
from monitoring.monitor import track


@tool("start_refill_confirmation")
def start_refill_confirmation(ticket_number: str, patient_name: str, medication_name: str) -> str:
    """Start a confirmation session for a prescription refill ticket.
    Returns the facts needed to ask the customer to confirm the claimed
    patient name and medication before proceeding -- do not proceed with
    any refill action until the customer confirms."""
    with track("refill_tool.start_confirmation"):
        result = ra.start_confirmation(ticket_number, patient_name, medication_name)
        return (f"ticket_number={result['ticket_number']}, patient_name={result['patient_name']}, "
                f"medication_name={result['medication_name']}. Ask the customer to confirm this is correct.")


@tool("confirm_refill_details_correct")
def confirm_refill_details_correct(ticket_number: str) -> str:
    """Call this ONLY after the customer has explicitly confirmed the
    claimed patient/medication details are correct. Verifies against real
    records, applies controlled-substance schedule rules, and returns the
    outcome as facts (eligible for refill, or escalated to the doctor with
    an appointment date/time)."""
    with track("refill_tool.confirm_positive") as status:
        result = ra.confirm_positive(ticket_number)
        if result["outcome"] == "error":
            status["success"] = False
            return result["message"]
        if result["outcome"] == "escalated":
            return (f"ESCALATED to doctor. reason={result['reason']}, "
                    f"appointment_date={result['appointment_date']}, "
                    f"appointment_time={result['appointment_time']}, ticket_number={ticket_number}")
        return (f"ELIGIBLE for refill. medication={result['medication_name']}, "
                f"schedule_reason={result['eligibility']['reason']}")


@tool("record_refill_details_incorrect")
def record_refill_details_incorrect(ticket_number: str) -> str:
    """Call this ONLY after the customer has explicitly said the claimed
    patient/medication details are WRONG. Marks the session as awaiting a
    correction and returns confirmation that the system is ready for the
    corrected details."""
    with track("refill_tool.confirm_negative"):
        result = ra.confirm_negative(ticket_number)
        return (f"Marked ticket {result['ticket_number']} as awaiting correction. "
                f"Ask the customer for the correct Ticket #, Patient name, and Medication name.")


@tool("submit_refill_correction")
def submit_refill_correction(ticket_number: str, corrected_patient_name: str,
                              corrected_medication_name: str) -> str:
    """Submit corrected patient/medication details after the customer
    provided them. Verifies against real records, applies controlled-
    substance schedule rules if verified, and returns the outcome as
    facts."""
    with track("refill_tool.submit_correction") as status:
        result = ra.submit_correction(ticket_number, corrected_patient_name, corrected_medication_name)
        if result["outcome"] == "error":
            status["success"] = False
            return result["message"]
        if result["outcome"] == "escalated":
            return (f"Could not verify the corrected information. ESCALATED to doctor. "
                    f"reason={result['reason']}, appointment_date={result['appointment_date']}, "
                    f"appointment_time={result['appointment_time']}, ticket_number={ticket_number}")
        return (f"VERIFIED and ELIGIBLE for refill. medication={result['medication_name']}, "
                f"schedule_reason={result['eligibility']['reason']}")


PRESCRIPTION_REFILL_AGENT_TOOLS = [
    start_refill_confirmation, confirm_refill_details_correct,
    record_refill_details_incorrect, submit_refill_correction,
]
