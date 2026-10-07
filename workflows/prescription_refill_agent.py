"""
Prescription Refill Agent workflow -- the multi-turn confirmation flow and
controlled-substance schedule engine from the updated capstone spec.

This is DISTINCT from workflows/prescription_refill.py (the DOB-verification
+ rule-engine + Cal.com/eRx pipeline built for the original capstone). That
module handles "patient calls in asking to refill a specific known
prescription." This module handles a different scenario from the feedback/
support-ticket system: a support ticket mentions a refill, and the agent
has to (a) confirm which patient and medication the ticket is actually
about, since that's ambiguous from a free-text message, then (b) apply
DEA-schedule-specific refill rules once it knows.

Multi-turn flow:
  1. start_confirmation()      -- ticket created, agent asks "is this
                                   correct?" naming the claimed patient +
                                   medication
  2. confirm_positive()        -- user said yes -> resolve against real
                                   records, run the schedule eligibility
                                   check, escalate to doctor if the record
                                   can't be verified
  3. confirm_negative()        -- user said no -> ask for corrected
                                   ticket#/patient/medication
  4. submit_correction()       -- verify the corrected info against real
                                   records; verified -> eligibility check,
                                   not found -> escalate to doctor

Schedule eligibility rules (deterministic, safety-critical -- kept out of
LLM judgment on purpose, same philosophy as workflows/prescription_refill.py's
rule engine):
  - Schedule II: NEVER auto-eligible. Always requires a new prescription
    from the doctor; always escalates.
  - Schedule III/IV: eligible only if written_date is on file AND within
    the last 6 months AND at least one refill remains. (Simplification:
    "refilled more than 5 times in 6 months" is modeled via
    remaining_refills reaching 0, since this project's schema tracks
    refills-remaining rather than a full per-fill audit ledger -- see
    README for this documented simplification.)
  - Schedule V: eligible as long as at least one refill remains (not
    subject to the 6-month/5-refill limit).
  - Non-controlled: not this module's concern -- see
    workflows/prescription_refill.py for the general refill rule engine.
"""

import os
import sys
import datetime as dt
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from monitoring.monitor import track
from monitoring.logging_utils import log_event

SCHEDULE_II = "II"
SCHEDULE_III = "III"
SCHEDULE_IV = "IV"
SCHEDULE_V = "V"

_SCHEDULE_DAYS_LIMIT = 183  # ~6 months

_POSITIVE_CONFIRMATION_WORDS = {
    "yes", "yep", "yeah", "correct", "right", "confirmed", "that's right",
    "that is correct", "affirmative", "correct that's",
}
_NEGATIVE_CONFIRMATION_WORDS = {
    "no", "nope", "wrong", "incorrect", "not right", "not correct",
    "that's not", "that is not",
}


def classify_confirmation_response_heuristic(text: str) -> bool:
    """Fallback yes/no classifier for the confirmation question (used when
    crewai/transformers aren't installed, or as the fast deterministic
    path for tests). Returns True for a positive confirmation, False for
    negative -- and, deliberately, False for ambiguous input, since this
    gates a prescription action and the safer default is to ask again
    rather than assume consent."""
    lowered = text.lower().strip()
    if any(w in lowered for w in _NEGATIVE_CONFIRMATION_WORDS):
        return False
    if any(w in lowered for w in _POSITIVE_CONFIRMATION_WORDS):
        return True
    return False


def _normalize_schedule(controlled_schedule: Optional[str]) -> Optional[str]:
    """Prescriptions store schedules as 'CII'/'CIII'/'CIV'/'CV' (matching
    the existing seed data) -- strip the leading 'C' to get the roman
    numeral the spec's rules are written against."""
    if not controlled_schedule:
        return None
    s = controlled_schedule.strip().upper()
    return s[1:] if s.startswith("C") else s


def check_schedule_eligibility(prescription: dict) -> dict:
    """Given a real prescription record, determines whether it's eligible
    for automated refill under DEA-schedule-specific rules. Returns
    {"eligible": bool, "schedule": str|None, "reason": str}."""
    if not prescription.get("is_controlled_substance"):
        return {"eligible": True, "schedule": None,
                "reason": "Not a controlled substance -- schedule rules don't apply."}

    schedule = _normalize_schedule(prescription.get("controlled_schedule"))

    if schedule == SCHEDULE_II:
        return {"eligible": False, "schedule": SCHEDULE_II,
                "reason": "Schedule II controlled substances always require a new prescription "
                          "from the doctor and cannot be refilled without doctor confirmation."}

    if schedule in (SCHEDULE_III, SCHEDULE_IV):
        written_date = prescription.get("written_date")
        remaining_refills = prescription.get("remaining_refills", 0)
        if not written_date:
            return {"eligible": False, "schedule": schedule,
                    "reason": f"Schedule {schedule} requires a written date on file to verify "
                              f"the 6-month refill window, and none is recorded."}
        try:
            written = dt.datetime.fromisoformat(written_date.replace("Z", "+00:00"))
            if written.tzinfo is None:
                written = written.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return {"eligible": False, "schedule": schedule,
                    "reason": f"Could not parse the written date on file ('{written_date}')."}
        age_days = (dt.datetime.now(dt.timezone.utc) - written).days
        if age_days > _SCHEDULE_DAYS_LIMIT:
            return {"eligible": False, "schedule": schedule,
                    "reason": f"Schedule {schedule} prescriptions cannot be filled or refilled "
                              f"more than 6 months after the written date (written {age_days} days ago)."}
        if remaining_refills <= 0:
            return {"eligible": False, "schedule": schedule,
                    "reason": f"Schedule {schedule} prescription has no refills remaining within "
                              f"the 6-month window (refill limit reached)."}
        return {"eligible": True, "schedule": schedule,
                "reason": f"Schedule {schedule}: within the 6-month window with refills remaining."}

    if schedule == SCHEDULE_V:
        remaining_refills = prescription.get("remaining_refills", 0)
        if remaining_refills <= 0:
            return {"eligible": False, "schedule": SCHEDULE_V,
                    "reason": "Schedule V has no 6-month/5-refill limit, but no refills remain "
                              "as authorized in the doctor's notes."}
        return {"eligible": True, "schedule": SCHEDULE_V,
                "reason": "Schedule V: not subject to the 6-month/5-refill limit, refills remain."}

    # Flagged as controlled but with an unrecognized/missing schedule value --
    # fail closed (require review) rather than guess.
    return {"eligible": False, "schedule": schedule,
            "reason": "Marked as a controlled substance but no recognized DEA schedule is on "
                      "file -- requires manual review."}


# ---------------------------------------------------------------------------
# Multi-turn confirmation flow
# ---------------------------------------------------------------------------

def start_confirmation(ticket_number: str, patient_name: str, medication_name: str) -> dict:
    """Step 1: records the claimed patient/medication for this ticket and
    returns the facts needed for the spec's confirmation question: 'Your
    ticket #[TicketNumber] is for [PatientName] requesting a refill of
    [MedicationName], correct?'"""
    with track("refill_agent.start_confirmation"):
        db.create_refill_confirmation(ticket_number, patient_name, medication_name)
        log_event("refill_confirmation_started", {
            "ticket_number": ticket_number, "patient_name": patient_name, "medication_name": medication_name,
        })
        return {
            "ticket_number": ticket_number, "patient_name": patient_name,
            "medication_name": medication_name, "stage": "awaiting_confirmation",
        }


def _escalate_to_doctor(ticket_number: str, patient_name: Optional[str], reason: str,
                         resolved_patient_id: Optional[int] = None) -> dict:
    """Shared escalation path: computes a follow-up appointment date/time
    and updates the ticket. If the patient has actually been resolved to a
    real patient_id (e.g. a Schedule II eligibility failure on an already-
    verified prescription), a real row is written to the appointments
    table via db.record_appointment. If the patient could NOT be verified
    (the more common escalation trigger -- unmatched patient/medication
    info), no appointments-table row is written, since that table is
    keyed by a real patient_id and we deliberately don't fabricate one --
    the date/time are still computed, logged, and shown to the user."""
    appointment_time = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)
    appointment_date_str = appointment_time.strftime("%Y-%m-%d")
    appointment_time_str = appointment_time.strftime("%H:%M UTC")

    if resolved_patient_id is not None:
        db.record_appointment(
            patient_id=resolved_patient_id,
            doctor_name="To be assigned",
            specialty="Prescription follow-up",
            calcom_booking_uid=None,
            start_time=appointment_time.isoformat(),
            status="scheduled (auto-escalation)",
        )

    db.update_ticket_status(ticket_number, "In Progress")
    log_event("refill_escalated_to_doctor", {
        "ticket_number": ticket_number, "patient_name": patient_name, "reason": reason,
        "appointment_date": appointment_date_str, "appointment_time": appointment_time_str,
        "resolved_patient_id": resolved_patient_id,
    })
    return {
        "escalated": True, "ticket_number": ticket_number, "reason": reason,
        "appointment_date": appointment_date_str, "appointment_time": appointment_time_str,
    }


def confirm_positive(ticket_number: str) -> dict:
    """Step 2a: user confirmed the claimed patient/medication is correct.
    Resolves against real records and runs the schedule eligibility check."""
    with track("refill_agent.confirm_positive") as status:
        confirmation = db.get_latest_refill_confirmation(ticket_number)
        if not confirmation:
            status["success"] = False
            return {"outcome": "error", "message": f"No confirmation session found for ticket #{ticket_number}."}

        prescription = db.find_prescription_by_patient_and_medication(
            confirmation["claimed_patient_name"], confirmation["claimed_medication_name"]
        )
        if not prescription:
            escalation = _escalate_to_doctor(
                ticket_number, confirmation["claimed_patient_name"],
                "Could not verify the claimed patient/medication against records after positive confirmation.",
            )
            return {"outcome": "escalated", **escalation}

        db.update_refill_confirmation(
            confirmation["id"], stage="verified",
            resolved_patient_id=prescription["patient_id"], resolved_prescription_id=prescription["prescription_id"],
        )

        eligibility = check_schedule_eligibility(prescription)
        if not eligibility["eligible"]:
            escalation = _escalate_to_doctor(
                ticket_number, confirmation["claimed_patient_name"], eligibility["reason"],
                resolved_patient_id=prescription["patient_id"],
            )
            return {"outcome": "escalated", "eligibility": eligibility, **escalation}

        return {
            "outcome": "eligible", "eligibility": eligibility,
            "patient_id": prescription["patient_id"], "prescription_id": prescription["prescription_id"],
            "medication_name": prescription["medication_name"],
        }


def confirm_negative(ticket_number: str) -> dict:
    """Step 2b: user said the claimed patient/medication is wrong. Marks
    the session as awaiting a correction."""
    with track("refill_agent.confirm_negative"):
        confirmation = db.get_latest_refill_confirmation(ticket_number)
        if confirmation:
            db.update_refill_confirmation(confirmation["id"], stage="awaiting_correction")
        return {"ticket_number": ticket_number, "stage": "awaiting_correction"}


def submit_correction(ticket_number: str, corrected_patient_name: str, corrected_medication_name: str) -> dict:
    """Step 3: user provided corrected patient/medication info. Verifies
    against real records; escalates to the doctor if still unverifiable."""
    with track("refill_agent.submit_correction") as status:
        confirmation = db.get_latest_refill_confirmation(ticket_number)
        if not confirmation:
            status["success"] = False
            return {"outcome": "error", "message": f"No confirmation session found for ticket #{ticket_number}."}

        db.update_refill_confirmation(
            confirmation["id"], claimed_patient_name=corrected_patient_name,
            claimed_medication_name=corrected_medication_name,
        )

        prescription = db.find_prescription_by_patient_and_medication(corrected_patient_name, corrected_medication_name)
        if not prescription:
            escalation = _escalate_to_doctor(
                ticket_number, corrected_patient_name,
                "Could not confirm the corrected patient/medication information against records.",
            )
            db.update_refill_confirmation(confirmation["id"], stage="escalated")
            return {"outcome": "escalated", **escalation}

        db.update_refill_confirmation(
            confirmation["id"], stage="verified",
            resolved_patient_id=prescription["patient_id"], resolved_prescription_id=prescription["prescription_id"],
        )
        eligibility = check_schedule_eligibility(prescription)
        if not eligibility["eligible"]:
            escalation = _escalate_to_doctor(
                ticket_number, corrected_patient_name, eligibility["reason"],
                resolved_patient_id=prescription["patient_id"],
            )
            return {"outcome": "escalated", "eligibility": eligibility, **escalation}

        return {
            "outcome": "verified_eligible", "eligibility": eligibility,
            "patient_id": prescription["patient_id"], "prescription_id": prescription["prescription_id"],
            "medication_name": prescription["medication_name"],
        }


# ---------------------------------------------------------------------------
# Deterministic message formatting -- matches the spec's exact wording.
# Used as the non-LLM fallback path AND as the ground-truth reference the
# LLM's genuine/individualized phrasing is guided by (see agents/prompts.py).
# ---------------------------------------------------------------------------

def format_confirmation_question(ticket_number: str, patient_name: str, medication_name: str) -> str:
    return f"Your ticket #{ticket_number} is for {patient_name} requesting a refill of {medication_name}, correct?"


def format_positive_confirmation_ack() -> str:
    return ("I really appreciate you bringing this to our attention. I can imagine this has "
            "been a bit of a hassle, and I want you to feel fully supported while I take care of this.")


def format_negative_confirmation_ack() -> str:
    return ("I'm truly sorry for the confusion. I understand how frustrating that can be. "
            "I'm going to correct the information now and make sure I have the right details "
            "moving forward. Can you confirm your Ticket #, Patient name, and Medication name for me?")


def format_verified_ack() -> str:
    return ("Your information has been fully verified \u2014 thank you for taking the time to send "
            "it. This helps us move ahead without any delays. To make sure everything is "
            "processed accurately, I'll just need to verify a bit more information for your prescription.")


def format_escalation_message(ticket_number: str, appointment_date: str, appointment_time: str) -> str:
    return (f"I'm really sorry for the mixup. I was not able to confirm that prescription in "
            f"your records. You deserve clear and accurate information, and I understand the "
            f"inconvenience. I have sent ticket#{ticket_number} and conversation details to the "
            f"doctor and scheduled a follow-up appointment on {appointment_date} and {appointment_time}.")
