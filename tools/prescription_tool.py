"""
Agent-facing tools for the prescription refill automation pipeline. These
wrap workflows/prescription_refill.py so the conversational agent can handle
requests like "I need a refill on my metformin" end-to-end: identity check,
clinical rule engine, and either golden-path queue injection or provider
review routing — the same pipeline the FastAPI webhook uses.
"""

from typing import Optional
from crewai.tools import tool

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import patient_db as db
from workflows.prescription_refill import process_refill_request
from monitoring.monitor import track


@tool("list_patient_prescriptions")
def list_patient_prescriptions(patient_id: int) -> str:
    """List all prescriptions on file for a patient, including remaining
    refills and whether each requires special handling (controlled
    substance, lab check). Use this to find the correct prescription_id
    before calling request_prescription_refill."""
    with track("prescription_tool.list_patient_prescriptions") as status:
        prescriptions = db.list_prescriptions(patient_id)
        if not prescriptions:
            status["success"] = False
            return f"No prescriptions found for patient_id={patient_id}."
        return str(prescriptions)


@tool("request_prescription_refill")
def request_prescription_refill(patient_id: int, date_of_birth: str, prescription_id: int,
                                 pharmacy_id: str, home_address: Optional[str] = None,
                                 ssn_last4: Optional[str] = None) -> str:
    """Process a prescription refill request through the full automation
    pipeline: identity verification (date_of_birth must match YYYY-MM-DD on
    file), then clinical safety checks (controlled substance status, refill
    count, required lab values). If all checks pass, the refill is sent
    directly to the pharmacy (golden path). If any check fails, it's
    automatically routed to the prescribing provider's review queue with
    full context assembled (patient profile, failed rule, active
    medications, last visit) — do NOT tell the patient the refill is
    approved in that case; tell them it's under provider review and why.

    home_address and ssn_last4 (last 4 digits of SSN) are optional
    additional identity factors for stronger verification — use these when
    the situation calls for extra assurance (e.g. the patient sounds
    uncertain, this is their first request, or the medication is sensitive).
    If provided, they must also match on file or the whole request is
    rejected as unauthorized, same as a DOB mismatch. Never ask for a full
    SSN — only the last 4 digits."""
    with track("prescription_tool.request_refill") as status:
        result = process_refill_request(patient_id, date_of_birth, prescription_id, pharmacy_id,
                                         address=home_address, ssn_last4=ssn_last4)
        if result["status"] not in ("processed",):
            status["success"] = result["status"] != "unauthorized"  # routing to review is a valid outcome, not a tool error
        return str(result)


@tool("get_provider_review_queue")
def get_provider_review_queue(patient_id: Optional[int] = None) -> str:
    """List prescriptions currently pending provider review (refills that
    failed an automated safety check), optionally filtered to one patient.
    Each entry includes the failure reason and full context payload
    (patient profile, medication reconciliation, last visit) for a 1-click
    provider decision."""
    with track("prescription_tool.get_review_queue") as status:
        reviews = db.list_provider_review(patient_id=patient_id, status="pending")
        if not reviews:
            status["success"] = False
            return "No prescriptions currently pending provider review."
        return str(reviews)


@tool("get_erx_queue")
def get_erx_queue(patient_id: Optional[int] = None) -> str:
    """List prescriptions successfully auto-approved and sent to the
    pharmacy's eRx queue (the golden path), optionally filtered to one
    patient."""
    with track("prescription_tool.get_erx_queue") as status:
        queue = db.list_erx_queue(patient_id=patient_id)
        if not queue:
            status["success"] = False
            return "No prescriptions currently in the eRx queue."
        return str(queue)


PRESCRIPTION_TOOLS = [
    list_patient_prescriptions,
    request_prescription_refill,
    get_provider_review_queue,
    get_erx_queue,
]
