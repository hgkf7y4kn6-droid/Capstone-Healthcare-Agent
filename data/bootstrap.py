"""
First-start setup for a Tendwell server.

`ensure_ready()` creates the schema, and in DEMO_MODE (never with real
patient data) loads the synthetic roster and runs a handful of real requests
through the real workflows so the console opens on a living clinic instead
of empty tables: a few appointment requests, refills that clear the safety
gates, refills that need a provider, and support messages. Nothing here is
fabricated output — every record is produced by the same code paths a live
request takes.
"""

import contextlib
import io
import os
import sys
import threading

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DEMO_MODE
from data import patient_db as db

_lock = threading.Lock()
_ready = False


def _load_synthetic_roster() -> None:
    from data import ingest_patient_data as ingest

    source = ingest.DEFAULT_SOURCE
    with contextlib.redirect_stdout(io.StringIO()):
        id_map = ingest.import_patients_json(os.path.join(source, "patients.json"))
        ingest.import_pdf_documents(os.path.join(source, "docs"))
        ingest.seed_prescriptions(id_map)


def _seed_activity() -> None:
    from workflows.prescription_refill import process_refill_request_authenticated
    from agents.feedback_crew import run_feedback_workflow

    by_name = {p["full_name"]: p for p in db.list_patients()}

    def rx_for(name: str, med_prefix: str):
        patient = by_name.get(name)
        if not patient:
            return None, None
        for rx in db.list_prescriptions(patient["patient_id"]):
            if rx["medication_name"].lower().startswith(med_prefix.lower()):
                return patient, rx
        return patient, None

    with contextlib.redirect_stdout(io.StringIO()):
        for name, med in [("John Smith", "Metformin"), ("Robert Lee", "Atorvastatin"),
                          ("Emily Chen", "Lorazepam"), ("Sarah Johnson", "Albuterol"),
                          ("John Smith", "Lisinopril")]:
            patient, rx = rx_for(name, med)
            if patient and rx:
                process_refill_request_authenticated(patient["patient_id"], rx["prescription_id"], "CVS-HORSHAM")

        for name, specialty in [("Daniel Kim", "Neurology"), ("Grace Mwangi", "Dermatology"),
                                ("Fatima Al-Sayed", "Rheumatology")]:
            patient = by_name.get(name)
            if patient:
                db.record_appointment(patient["patient_id"], "Next available", specialty, None, "",
                                      status="requested")

        for message, name in [("Thank you so much — the front desk sorted out my insurance question quickly.", "Priya Patel"),
                              ("I've been waiting three days and nobody called me back about my lab results.", "Carlos Mendoza")]:
            try:
                run_feedback_workflow(message, patient_name=name)
            except Exception:
                pass


def ensure_ready() -> None:
    """Idempotent and thread-safe; call on every server start."""
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        db.init_db(seed=False)
        if DEMO_MODE and not db.list_patients():
            _load_synthetic_roster()
            _seed_activity()
        _ready = True
