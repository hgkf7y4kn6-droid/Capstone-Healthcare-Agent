"""
Shared test infrastructure.

Tests use Python's stdlib `unittest` rather than requiring pytest to be
installed — this suite is verified to actually run in a minimal
environment (no network access to pip install anything extra), and
unittest.TestCase classes are fully discoverable by pytest too if you have
it installed, so nothing is lost either way:

    python -m unittest discover -s tests -v      # always works, no deps
    pytest tests/ -v                              # also works if installed
"""

import os
import sys
import tempfile
import shutil
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db


class IsolatedDBTestCase(unittest.TestCase):
    """Gives each test its own throwaway SQLite file so tests never share
    state or touch the real data/patients.db. Patches
    data.patient_db.PATIENT_DB_PATH directly (that's the module-global
    get_conn() actually reads), then restores it on teardown."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="healthcare_test_")
        self._original_path = db.PATIENT_DB_PATH
        db.PATIENT_DB_PATH = os.path.join(self._tmpdir, "test_patients.db")
        db.init_db(seed=False)

    def tearDown(self):
        db.PATIENT_DB_PATH = self._original_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # --- small helpers reused across test files ---

    def make_patient(self, name="Test Patient", dob="1980-01-01", address=None,
                      ssn_last4=None, external_id=None, recent_visit=False) -> dict:
        patient_id = db.upsert_patient(
            full_name=name, external_id=external_id, age=45, sex="F",
            dob=dob, address=address, ssn_last4=ssn_last4,
        )
        if recent_visit:  # refills need a visit in the last 12 months
            db.add_history_entry(patient_id, "visit", {"reason": "Checkup"}, True)
        return db.find_patient(name)

    def make_prescription(self, patient_id: int, **overrides) -> dict:
        defaults = dict(
            medication_name="Test Med 10mg", is_controlled_substance=False,
            controlled_schedule=None, remaining_refills=2, requires_lab_check=False,
            lab_check_type=None, lab_safe_range=None, prescribing_doctor="Dr. Test",
        )
        defaults.update(overrides)
        prescription_id = db.add_prescription(patient_id=patient_id, **defaults)
        return db.get_prescription(prescription_id)
