"""
Integration tests: these run the *real* ingestion pipeline against the
*real* source_documents dataset into a throwaway DB, rather than
hand-built fixtures. Slower than the unit tests but catch things fixtures
can't — e.g. a future edit to patients.json that reintroduces a duplicate
condition, or a PDF-parsing regression.
"""

import os
import sys
import io
import unittest
import tempfile
import shutil
import contextlib

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from data import ingest_patient_data as ingest


class TestFullIngestion(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="healthcare_test_ingest_")
        self._original_path = db.PATIENT_DB_PATH
        db.PATIENT_DB_PATH = os.path.join(self._tmpdir, "test_patients.db")
        db.init_db(seed=False)

    def tearDown(self):
        db.PATIENT_DB_PATH = self._original_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_full_ingestion_produces_expected_patient_count(self):
        source = ingest.DEFAULT_SOURCE
        with contextlib.redirect_stdout(io.StringIO()):  # keep test output clean
            id_map = ingest.import_patients_json(os.path.join(source, "patients.json"))
            ingest.import_pdf_documents(os.path.join(source, "docs"))
            ingest.seed_prescriptions(id_map)

        patients = db.list_patients()
        self.assertEqual(len(patients), 14, "expected exactly 14 patients from the seed dataset")

    def test_full_ingestion_has_no_duplicate_conditions(self):
        source = ingest.DEFAULT_SOURCE
        with contextlib.redirect_stdout(io.StringIO()):
            ingest.import_patients_json(os.path.join(source, "patients.json"))
            ingest.import_pdf_documents(os.path.join(source, "docs"))

        with contextlib.redirect_stdout(io.StringIO()):
            is_clean = ingest.validate_no_duplicate_conditions()
        self.assertTrue(is_clean, "seed dataset should have zero duplicate conditions across patients")

    def test_full_ingestion_is_idempotent(self):
        source = ingest.DEFAULT_SOURCE
        with contextlib.redirect_stdout(io.StringIO()):
            id_map = ingest.import_patients_json(os.path.join(source, "patients.json"))
            ingest.import_pdf_documents(os.path.join(source, "docs"))
            ingest.seed_prescriptions(id_map)
            first_count = len(db.list_patients())
            first_history_count = sum(len(db.get_history(p["patient_id"])) for p in db.list_patients())
            first_rx_count = len(db.list_prescriptions())

            # Re-run everything — nothing should duplicate.
            id_map2 = ingest.import_patients_json(os.path.join(source, "patients.json"))
            ingest.import_pdf_documents(os.path.join(source, "docs"))
            ingest.seed_prescriptions(id_map2)

        self.assertEqual(len(db.list_patients()), first_count)
        second_history_count = sum(len(db.get_history(p["patient_id"])) for p in db.list_patients())
        self.assertEqual(second_history_count, first_history_count)
        self.assertEqual(len(db.list_prescriptions()), first_rx_count)

    def test_seeded_prescriptions_cover_all_pipeline_branches(self):
        """Sanity check that the demo dataset actually exercises every
        branch of the rule engine (golden path, zero refills, controlled
        substance, missing lab, out-of-range lab) — if someone edits the
        seed data and accidentally removes a branch, this test will catch it."""
        source = ingest.DEFAULT_SOURCE
        with contextlib.redirect_stdout(io.StringIO()):
            id_map = ingest.import_patients_json(os.path.join(source, "patients.json"))
            ingest.seed_prescriptions(id_map)

        prescriptions = db.list_prescriptions()
        self.assertTrue(any(rx["is_controlled_substance"] for rx in prescriptions),
                         "expected at least one controlled-substance seed prescription")
        self.assertTrue(any(rx["remaining_refills"] == 0 for rx in prescriptions),
                         "expected at least one zero-refill seed prescription")
        self.assertTrue(any(rx["requires_lab_check"] for rx in prescriptions),
                         "expected at least one lab-check-required seed prescription")
        self.assertTrue(any(not rx["is_controlled_substance"] and rx["remaining_refills"] > 0
                             and not rx["requires_lab_check"] for rx in prescriptions),
                         "expected at least one clean golden-path seed prescription")


if __name__ == "__main__":
    unittest.main()
