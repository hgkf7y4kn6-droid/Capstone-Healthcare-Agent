"""
Prescription Refill Automation Pipeline.

Implements the end-to-end workflow:

    [Patient Request] -> [Identity Verification] -> [Clinical Rule Engine]
                                                            |
                          +---------------------------------+---------------------------------+
                          v                                                                     v
                [PASSED: Auto-Queue Injection]                                     [FAILED: Context Pre-Assembly]
                          |                                                                     |
                          v                                                                     v
        [Write to EHR eRx Queue & Notify Pharmacy]                          [Route to Provider Chart for 1-Click Action]

This module is transport-agnostic: it's called directly by the conversational
agent (tools/prescription_tool.py) and by the product API (api/app.py), so
both entry points share one rule engine instead of duplicating clinical logic.

DATA NOTE: this operates against the local SQLite EHR (data/patient_db.py),
not a live hospital FHIR server — there's no real EHR to connect to in this
environment. The rule engine logic here is what actually matters and is identical
whichever EHR sits behind it.

SAFETY NOTE: this is a demo/capstone-grade identity check (patient ID + DOB
match) and rule engine, not a certified clinical decision support system or
a HIPAA-compliant identity provider. Do not use with real patient data.
"""

import os
import sys
import re
import datetime as dt
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from monitoring.monitor import track
from monitoring.logging_utils import log_event


# ---------------------------------------------------------------------------
# Step 1: Identity verification
# ---------------------------------------------------------------------------

def verify_identity(patient_id: int, date_of_birth: str, address: Optional[str] = None,
                     ssn_last4: Optional[str] = None) -> dict:
    """HIPAA-style identity check: patient_id + DOB match, plus any
    additional factors supplied (home address, last 4 of SSN) — those are
    optional but if given, must also match. Returns a result dict rather
    than raising, so callers (agent tool / API route) can decide how to
    surface the failure."""
    with track("prescription.identity_verification", meta={"patient_id": patient_id}) as status:
        result = db.verify_patient_identity_detailed(patient_id, date_of_birth, address, ssn_last4)
        if not result["verified"]:
            status["success"] = False
        log_event("prescription_identity_check", {
            "patient_id": patient_id, "verified": result["verified"], "factors": result["factors"],
        })
        return result


# ---------------------------------------------------------------------------
# Step 2: Clinical rule engine
# ---------------------------------------------------------------------------

def _parse_lab_value(raw_value) -> Optional[float]:
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    if not isinstance(raw_value, str):
        return None
    match = re.search(r"-?\d+\.?\d*", raw_value)
    return float(match.group()) if match else None


def _parse_safe_range(range_str: str):
    """Supports 'min-max', '<max', '>min'. Returns a predicate function."""
    range_str = range_str.strip()
    if range_str.startswith("<"):
        limit = float(range_str[1:].strip())
        return lambda v: v < limit
    if range_str.startswith(">"):
        limit = float(range_str[1:].strip())
        return lambda v: v > limit
    if "-" in range_str:
        lo, hi = range_str.split("-", 1)
        lo, hi = float(lo.strip()), float(hi.strip())
        return lambda v: lo <= v <= hi
    return lambda v: True  # unrecognized format: don't block on it


VISIT_RECENCY_DAYS = 365

# Allergy names that imply a drug family. Deliberately short: this routes to a
# clinician, so a miss falls back to the clinician's own review, and a false
# positive costs one review. A clinician owns the real list.
_ALLERGY_FAMILIES = {
    "penicillin": ("penicillin", "amoxicillin", "ampicillin", "augmentin"),
    "sulfa": ("sulfamethoxazole", "trimethoprim", "sulfasalazine", "bactrim"),
    "nsaid": ("ibuprofen", "naproxen", "diclofenac", "meloxicam", "aspirin"),
    "aspirin": ("aspirin",),
    "codeine": ("codeine", "hydrocodone", "oxycodone", "morphine"),
}


def _documented_allergy_match(patient_id: int, medication_name: str) -> Optional[str]:
    med = (medication_name or "").lower()
    for entry in db.get_history(patient_id):
        content = entry["content"]
        if entry["entry_type"] != "alert" or not isinstance(content, dict) or not content.get("allergy"):
            continue
        allergy = str(content["allergy"]).lower()
        names = {allergy.split()[0], allergy}
        for family, drugs in _ALLERGY_FAMILIES.items():
            if family in allergy:
                names.update(drugs)
        if any(n and n in med for n in names):
            return str(content["allergy"])
    return None


def _days_since(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        when = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - when).days


def run_clinical_rule_engine(prescription: dict) -> dict:
    """Runs the safety gates in order and returns the first failure,
    or a pass result if all gates clear.

    Returns: {"passed": bool, "reason": str|None, "detail": str|None}
    """
    with track("prescription.rule_engine", meta={"prescription_id": prescription["prescription_id"]}) as status:
        # Gate 1: Controlled substance filtering — always routes to manual review,
        # regardless of refill count or lab status.
        if prescription.get("is_controlled_substance"):
            status["success"] = False
            return {
                "passed": False,
                "reason": "Controlled Substance Flag",
                "detail": f"{prescription['medication_name']} is a controlled substance"
                          f"{' (Schedule ' + prescription['controlled_schedule'] + ')' if prescription.get('controlled_schedule') else ''} "
                          f"and requires manual clinician review regardless of refill/lab status.",
            }

        # Gate 1b: a documented allergy that matches the drug always goes to a clinician.
        allergy = _documented_allergy_match(prescription["patient_id"], prescription["medication_name"])
        if allergy:
            status["success"] = False
            return {
                "passed": False,
                "reason": "Documented Allergy Match",
                "detail": f"{prescription['medication_name']} may conflict with the documented allergy "
                          f"'{allergy}'. A clinician must confirm before any refill.",
            }

        # Gate 1c: no visit in the last 12 months (or none on record).
        days = _days_since(db.get_last_visit_date(prescription["patient_id"]))
        if days is None or days > VISIT_RECENCY_DAYS:
            status["success"] = False
            return {
                "passed": False,
                "reason": "No Recent Visit",
                "detail": f"{prescription['medication_name']} can't be refilled automatically: "
                          f"{'no visit is on record' if days is None else f'the last visit was {days} days ago'} "
                          f"(a visit within {VISIT_RECENCY_DAYS} days is required).",
            }

        # Gate 2: Refill validity
        if prescription.get("remaining_refills", 0) <= 0:
            status["success"] = False
            return {
                "passed": False,
                "reason": "Zero Refills Remaining",
                "detail": f"{prescription['medication_name']} has 0 refills remaining on file; "
                          f"a new prescription/renewal is required from the provider.",
            }

        # Gate 3: Lab value verification (therapeutic threshold check)
        if prescription.get("requires_lab_check"):
            lab_key = prescription.get("lab_check_type", "")
            lab = db.get_latest_structured_lab_value(prescription["patient_id"], lab_key)
            if not lab:
                status["success"] = False
                return {
                    "passed": False,
                    "reason": "Missing Required Lab Data",
                    "detail": f"{prescription['medication_name']} requires a recent '{lab_key}' result "
                              f"on file, but none was found. A lab panel should be ordered before refill.",
                }
            value = _parse_lab_value(lab["raw_value"])
            safe_range = prescription.get("lab_safe_range", "")
            predicate = _parse_safe_range(safe_range) if safe_range else (lambda v: True)
            if value is None or not predicate(value):
                status["success"] = False
                return {
                    "passed": False,
                    "reason": "Lab Value Outside Therapeutic Threshold",
                    "detail": f"{prescription['medication_name']} requires '{lab_key}' within {safe_range}; "
                              f"most recent value is {lab['raw_value']} (as of {lab['as_of']}), which is out of range.",
                }

        return {"passed": True, "reason": None, "detail": "All clinical safety gates cleared."}


# ---------------------------------------------------------------------------
# Step 3: Golden path — EHR eRx queue injection
# ---------------------------------------------------------------------------

def golden_path_queue_injection(prescription: dict, pharmacy_id: str) -> dict:
    """Writes the validated refill directly into the eRx queue and
    'notifies' the pharmacy (simulated — logged as an event; swap in a real
    pharmacy notification integration, e.g. Telnyx SMS, here)."""
    with track("prescription.erx_queue_injection", meta={"prescription_id": prescription["prescription_id"]}):
        confirmation_id = f"RX-{prescription['prescription_id']}-{int(dt.datetime.now(dt.timezone.utc).timestamp())}"
        db.add_erx_queue_entry(
            prescription_id=prescription["prescription_id"],
            patient_id=prescription["patient_id"],
            pharmacy_id=pharmacy_id,
            confirmation_id=confirmation_id,
            status="queued",
        )
        db.decrement_refill(prescription["prescription_id"])
        log_event("pharmacy_notification", {
            "prescription_id": prescription["prescription_id"],
            "medication": prescription["medication_name"],
            "pharmacy_id": pharmacy_id,
            "confirmation_id": confirmation_id,
        })
        return {"confirmation_id": confirmation_id, "pharmacy_id": pharmacy_id}


# ---------------------------------------------------------------------------
# Step 4: Failed path — context pre-assembly for provider review
# ---------------------------------------------------------------------------

def context_pre_assembly(prescription: dict, rule_result: dict) -> dict:
    """Bundles everything a provider needs for a 1-click decision: patient
    profile, the specific failed rule, active medication reconciliation, and
    last visit date — instead of the provider having to dig through the
    chart themselves."""
    with track("prescription.context_pre_assembly", meta={"prescription_id": prescription["prescription_id"]}):
        patient_id = prescription["patient_id"]
        with db.get_conn() as conn:
            patient_row = conn.execute(
                "SELECT * FROM patients WHERE patient_id = ?", (patient_id,)
            ).fetchone()
        patient_profile = dict(patient_row) if patient_row else {}

        active_meds = db.get_active_medications(patient_id)
        last_visit = db.get_last_visit_date(patient_id)

        payload = {
            "patient_profile": {
                "patient_id": patient_id,
                "full_name": patient_profile.get("full_name"),
                "age": patient_profile.get("age"),
                "sex": patient_profile.get("sex"),
                "primary_doctor": patient_profile.get("primary_doctor"),
            },
            "failed_rule": rule_result["reason"],
            "failed_rule_detail": rule_result["detail"],
            "requested_medication": prescription["medication_name"],
            "medication_reconciliation": [
                {"medication": m["medication_name"], "remaining_refills": m["remaining_refills"],
                 "status": m["status"]} for m in active_meds
            ],
            "last_office_visit": last_visit,
        }

        review_id = db.add_provider_review(
            prescription_id=prescription["prescription_id"],
            patient_id=patient_id,
            reason=rule_result["reason"],
            context_payload=payload,
        )
        return {"review_id": review_id, "context_payload": payload}


# ---------------------------------------------------------------------------
# Orchestration: the full pipeline
# ---------------------------------------------------------------------------

def process_refill_request(patient_id: int, date_of_birth: str, prescription_id: int,
                            pharmacy_id: str, address: Optional[str] = None,
                            ssn_last4: Optional[str] = None) -> dict:
    """Runs the complete pipeline end to end and returns a structured result
    the caller (agent tool or API route) can present to the user/patient.

    date_of_birth is always required. address and ssn_last4 are optional
    additional identity factors — supply either or both for stronger
    verification (e.g. a first-time caller, or a higher-risk medication);
    if supplied, they must also match or the request is rejected the same
    as a DOB mismatch.

    Used by the conversational agent and any direct/unauthenticated entry
    point, where DOB (+ optional extra factors) re-verification IS the
    identity check. For requests already authenticated upstream (e.g. a web
    portal handing off a signed JWT), use process_refill_request_authenticated
    instead — re-asking for these there would defeat the point of the
    session handoff."""
    with track("prescription.full_pipeline", meta={"patient_id": patient_id, "prescription_id": prescription_id}) as status:
        # Step 1: Identity verification
        identity = verify_identity(patient_id, date_of_birth, address, ssn_last4)
        if not identity["verified"]:
            status["success"] = False
            failed_factors = [f for f, matched in identity["factors"].items() if not matched]
            return {
                "status": "unauthorized",
                "message": "Identity verification failed. The following did not match our records: "
                            f"{', '.join(failed_factors)}.",
            }

        return _run_rule_engine_and_route(patient_id, prescription_id, pharmacy_id, status)


def process_refill_request_authenticated(patient_id: int, prescription_id: int, pharmacy_id: str) -> dict:
    """Same pipeline, for requests where identity was already established
    upstream (a verified portal JWT — see api/portal_auth.py). Skips the DOB
    gate entirely; the trust boundary is the JWT signature check that ran
    before this was ever called. Still runs the full clinical rule engine —
    only the identity step is different."""
    with track("prescription.full_pipeline", meta={"patient_id": patient_id, "prescription_id": prescription_id,
                                                     "identity_method": "jwt"}) as status:
        log_event("prescription_identity_check", {"patient_id": patient_id, "verified": True, "method": "jwt"})
        return _run_rule_engine_and_route(patient_id, prescription_id, pharmacy_id, status)


def _run_rule_engine_and_route(patient_id: int, prescription_id: int, pharmacy_id: str, status: dict) -> dict:
    """Shared by both entry points above: fetch the prescription, run the
    rule engine, then route to the golden path or provider review."""
    prescription = db.get_prescription(prescription_id)
    if not prescription or prescription["patient_id"] != patient_id:
        status["success"] = False
        return {"status": "error", "message": f"No prescription {prescription_id} found for this patient."}

    # Step 2: Clinical rule engine
    rule_result = run_clinical_rule_engine(prescription)

    if rule_result["passed"]:
        # Step 3: Golden path
        queue_result = golden_path_queue_injection(prescription, pharmacy_id)
        return {
            "status": "processed",
            "message": f"Refill approved and sent directly to {pharmacy_id}. "
                        f"Confirmation: {queue_result['confirmation_id']}",
            "confirmation_id": queue_result["confirmation_id"],
        }
    else:
        # Step 4: Failed path — context pre-assembly + provider routing
        review = context_pre_assembly(prescription, rule_result)
        return {
            "status": "routed_to_review",
            "message": f"This refill could not be auto-approved ({rule_result['reason']}) and has "
                       f"been routed to your provider's chart with full context for a quick decision.",
            "reason": rule_result["reason"],
            "detail": rule_result["detail"],
            "review_id": review["review_id"],
        }
