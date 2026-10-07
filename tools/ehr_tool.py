"""
EHR (patient record) tools: retrieving and updating structured/unstructured
patient history. Backed by the local SQLite store in data/patient_db.py.
"""

from typing import Optional
from crewai.tools import tool

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import patient_db as db
from monitoring.monitor import track


@tool("find_patient_record")
def find_patient_record(full_name: str) -> str:
    """Look up a patient by (partial) full name and return their patient_id
    and demographics. Use this before retrieving or adding history so you
    have the correct patient_id."""
    with track("ehr.find_patient_record") as status:
        patient = db.find_patient(full_name)
        if not patient:
            status["success"] = False
            return f"No patient found matching '{full_name}'. You may need to register them first with add_patient_record."
        return str(patient)


@tool("add_patient_record")
def add_patient_record(full_name: str, age: int, sex: str, relation_to_user: str = "self") -> str:
    """Register a new patient in the system. relation_to_user should be
    'self' if the requester is the patient, or e.g. 'father', 'mother',
    'child' if booking/managing care on behalf of a family member."""
    with track("ehr.add_patient_record"):
        patient_id = db.add_patient(full_name, age, sex, relation_to_user)
        return f"Registered new patient '{full_name}' with patient_id={patient_id}."


@tool("get_patient_history")
def get_patient_history(patient_id: int) -> str:
    """Retrieve the full structured + unstructured history (diagnoses,
    treatments, alerts, notes) for a given patient_id, ordered chronologically."""
    with track("ehr.get_patient_history") as status:
        history = db.get_history(patient_id)
        if not history:
            status["success"] = False
            return f"No history entries found for patient_id={patient_id}."
        return str(history)


@tool("add_history_entry")
def add_history_entry(patient_id: int, entry_type: str, content: str, is_structured: bool = False) -> str:
    """Add a new entry to a patient's history. entry_type must be one of
    'diagnosis', 'treatment', 'alert', or 'note'. Set is_structured=True and
    pass a JSON-style string in `content` for structured clinical data;
    otherwise pass free text for a plain note. Use this when an attendant
    wants to add or update patient history."""
    with track("ehr.add_history_entry"):
        entry_id = db.add_history_entry(patient_id, entry_type, content, is_structured)
        return f"Added {entry_type} entry (id={entry_id}) to patient_id={patient_id}."


@tool("list_all_patients")
def list_all_patients() -> str:
    """List all patients currently registered in the system."""
    with track("ehr.list_all_patients"):
        patients = db.list_patients()
        return str(patients)


@tool("get_patient_appointments")
def get_patient_appointments(patient_id: Optional[int] = None) -> str:
    """List booked appointments, optionally filtered to one patient_id.
    Omit patient_id to list all appointments (useful for the doctor/admin view)."""
    with track("ehr.get_patient_appointments") as status:
        appts = db.list_appointments(patient_id)
        if not appts:
            status["success"] = False
            return "No appointments found."
        return str(appts)


EHR_TOOLS = [
    find_patient_record,
    add_patient_record,
    get_patient_history,
    add_history_entry,
    list_all_patients,
    get_patient_appointments,
]
