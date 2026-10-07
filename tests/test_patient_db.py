import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from tests._helpers import IsolatedDBTestCase


class TestPatientCRUD(IsolatedDBTestCase):
    def test_upsert_creates_new_patient(self):
        pid = db.upsert_patient(full_name="Alice Example", external_id="T001", age=30, sex="F")
        patient = db.get_patient_by_external_id("T001")
        self.assertIsNotNone(patient)
        self.assertEqual(patient["patient_id"], pid)
        self.assertEqual(patient["full_name"], "Alice Example")

    def test_upsert_is_idempotent_on_external_id(self):
        pid1 = db.upsert_patient(full_name="Bob Example", external_id="T002", age=40)
        pid2 = db.upsert_patient(full_name="Bob Example Updated", external_id="T002", age=41)
        self.assertEqual(pid1, pid2, "re-upserting the same external_id should update, not duplicate")
        self.assertEqual(len(db.list_patients()), 1)
        self.assertEqual(db.get_patient_by_external_id("T002")["age"], 41)

    def test_upsert_coalesces_optional_fields_on_update(self):
        # First insert sets dob; a later upsert that omits dob should NOT wipe it.
        db.upsert_patient(full_name="Carol Example", external_id="T003", dob="1990-05-05")
        db.upsert_patient(full_name="Carol Example", external_id="T003", dob=None)
        self.assertEqual(db.get_patient_by_external_id("T003")["dob"], "1990-05-05")

    def test_find_patient_partial_match(self):
        db.upsert_patient(full_name="Dana Farrow", external_id="T004")
        found = db.find_patient("Farrow")
        self.assertIsNotNone(found)
        self.assertEqual(found["full_name"], "Dana Farrow")

    def test_find_patient_no_match_returns_none(self):
        self.assertIsNone(db.find_patient("Nobody Here"))


class TestHistoryEntries(IsolatedDBTestCase):
    def test_add_and_get_history_structured(self):
        patient = self.make_patient()
        db.add_history_entry(patient["patient_id"], "diagnosis", {"condition": "Hypertension"}, is_structured=True)
        history = db.get_history(patient["patient_id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"]["condition"], "Hypertension")

    def test_add_history_skip_if_duplicate(self):
        patient = self.make_patient()
        db.add_history_entry(patient["patient_id"], "diagnosis", {"condition": "Asthma"},
                              is_structured=True, skip_if_duplicate=True)
        result = db.add_history_entry(patient["patient_id"], "diagnosis", {"condition": "Asthma"},
                                       is_structured=True, skip_if_duplicate=True)
        self.assertIsNone(result, "duplicate entry should be skipped, returning None")
        self.assertEqual(len(db.get_history(patient["patient_id"])), 1)

    def test_history_preserves_explicit_created_at(self):
        patient = self.make_patient()
        db.add_history_entry(patient["patient_id"], "visit", "Annual checkup",
                              created_at="2020-01-15T00:00:00+00:00")
        history = db.get_history(patient["patient_id"])
        self.assertEqual(history[0]["created_at"], "2020-01-15T00:00:00+00:00")


class TestAppointments(IsolatedDBTestCase):
    def test_record_appointment_dedup(self):
        patient = self.make_patient()
        db.record_appointment(patient["patient_id"], "Dr. Test", "Cardiology", None,
                               "2025-06-01T09:00:00Z", skip_if_duplicate=True)
        result = db.record_appointment(patient["patient_id"], "Dr. Test", "Cardiology", None,
                                        "2025-06-01T09:00:00Z", skip_if_duplicate=True)
        self.assertIsNone(result)
        self.assertEqual(len(db.list_appointments(patient["patient_id"])), 1)


class TestIdentityVerification(IsolatedDBTestCase):
    def test_dob_only_correct(self):
        patient = self.make_patient(dob="1979-03-15")
        self.assertTrue(db.verify_patient_identity(patient["patient_id"], "1979-03-15"))

    def test_dob_only_incorrect(self):
        patient = self.make_patient(dob="1979-03-15")
        self.assertFalse(db.verify_patient_identity(patient["patient_id"], "1900-01-01"))

    def test_unknown_patient_fails(self):
        self.assertFalse(db.verify_patient_identity(9999, "1979-03-15"))

    def test_multi_factor_all_correct(self):
        patient = self.make_patient(dob="1979-03-15", address="1 Main St, Anytown, US",
                                     ssn_last4="1234")
        self.assertTrue(db.verify_patient_identity(
            patient["patient_id"], "1979-03-15", "1 Main St, Anytown, US", "1234"))

    def test_multi_factor_wrong_address_fails_overall(self):
        patient = self.make_patient(dob="1979-03-15", address="1 Main St, Anytown, US",
                                     ssn_last4="1234")
        self.assertFalse(db.verify_patient_identity(
            patient["patient_id"], "1979-03-15", "999 Fake Ave, Nowhere, US", "1234"))

    def test_multi_factor_wrong_ssn_fails_overall(self):
        patient = self.make_patient(dob="1979-03-15", address="1 Main St, Anytown, US",
                                     ssn_last4="1234")
        self.assertFalse(db.verify_patient_identity(
            patient["patient_id"], "1979-03-15", "1 Main St, Anytown, US", "0000"))

    def test_address_matching_is_case_and_whitespace_insensitive(self):
        patient = self.make_patient(dob="1979-03-15", address="1 Main St, Anytown, US")
        self.assertTrue(db.verify_patient_identity(
            patient["patient_id"], "1979-03-15", "  1   MAIN st,   ANYTOWN, us  "))

    def test_detailed_breakdown_reports_each_factor(self):
        patient = self.make_patient(dob="1979-03-15", address="1 Main St", ssn_last4="1234")
        result = db.verify_patient_identity_detailed(
            patient["patient_id"], "1979-03-15", "wrong address", "9999")
        self.assertTrue(result["factors"]["dob"])
        self.assertFalse(result["factors"]["address"])
        self.assertFalse(result["factors"]["ssn_last4"])
        self.assertFalse(result["verified"])

    def test_backward_compatible_dob_only_call_unaffected_by_new_columns(self):
        # A patient with no address/ssn on file should still verify fine
        # when the caller only checks DOB (the original, pre-existing behavior).
        patient = self.make_patient(dob="1979-03-15", address=None, ssn_last4=None)
        self.assertTrue(db.verify_patient_identity(patient["patient_id"], "1979-03-15"))


class TestPrescriptionsAndQueues(IsolatedDBTestCase):
    def test_add_prescription_with_written_date_persists_correctly(self):
        # Regression test: add_prescription's INSERT once had 12 columns/
        # placeholders but only 11 bound values, silently dropping
        # written_date and raising sqlite3.ProgrammingError.
        patient = self.make_patient()
        rx_id = db.add_prescription(patient_id=patient["patient_id"], medication_name="Xanax",
                                     written_date="2024-01-15")
        rx = db.get_prescription(rx_id)
        self.assertEqual(rx["written_date"], "2024-01-15")

    def test_add_prescription_without_written_date_defaults_none(self):
        patient = self.make_patient()
        rx_id = db.add_prescription(patient_id=patient["patient_id"], medication_name="Metformin")
        rx = db.get_prescription(rx_id)
        self.assertIsNone(rx["written_date"])


    def test_add_prescription_skip_if_duplicate(self):
        patient = self.make_patient()
        db.add_prescription(patient_id=patient["patient_id"], medication_name="Metformin",
                             remaining_refills=2, skip_if_duplicate=True)
        result = db.add_prescription(patient_id=patient["patient_id"], medication_name="Metformin",
                                      remaining_refills=2, skip_if_duplicate=True)
        self.assertIsNone(result)
        self.assertEqual(len(db.list_prescriptions(patient["patient_id"])), 1)

    def test_decrement_refill_floors_at_zero(self):
        patient = self.make_patient()
        rx = self.make_prescription(patient["patient_id"], remaining_refills=1)
        db.decrement_refill(rx["prescription_id"])
        db.decrement_refill(rx["prescription_id"])  # second call should not go negative
        updated = db.get_prescription(rx["prescription_id"])
        self.assertEqual(updated["remaining_refills"], 0)

    def test_erx_queue_and_provider_review_round_trip(self):
        patient = self.make_patient()
        rx = self.make_prescription(patient["patient_id"])
        db.add_erx_queue_entry(rx["prescription_id"], patient["patient_id"], "PHARM-001", "CONF-1")
        self.assertEqual(len(db.list_erx_queue(patient["patient_id"])), 1)

        review_id = db.add_provider_review(
            rx["prescription_id"], patient["patient_id"], "Zero Refills Remaining", {"foo": "bar"})
        reviews = db.list_provider_review(patient["patient_id"], status="pending")
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["context_payload"]["foo"], "bar")

        db.update_review_status(review_id, "approved")
        self.assertEqual(len(db.list_provider_review(patient["patient_id"], status="pending")), 0)

    def test_latest_structured_lab_value_returns_most_recent(self):
        patient = self.make_patient()
        db.add_history_entry(patient["patient_id"], "lab_result",
                              {"results": {"potassium": "3.8 mmol/L"}, "as_of": "2024-01-01"},
                              is_structured=True, created_at="2024-01-01T00:00:00+00:00")
        db.add_history_entry(patient["patient_id"], "lab_result",
                              {"results": {"potassium": "4.5 mmol/L"}, "as_of": "2024-06-01"},
                              is_structured=True, created_at="2024-06-01T00:00:00+00:00")
        lab = db.get_latest_structured_lab_value(patient["patient_id"], "potassium")
        self.assertEqual(lab["raw_value"], "4.5 mmol/L")

    def test_latest_structured_lab_value_missing_returns_none(self):
        patient = self.make_patient()
        self.assertIsNone(db.get_latest_structured_lab_value(patient["patient_id"], "potassium"))


class TestRefillTasksAndIdempotency(IsolatedDBTestCase):
    def test_refill_task_lifecycle(self):
        patient = self.make_patient()
        rx = self.make_prescription(patient["patient_id"])
        db.create_refill_task("track-1", patient["patient_id"], rx["prescription_id"], "PHARM-001",
                               identity_method="jwt")
        task = db.get_refill_task("track-1")
        self.assertEqual(task["status"], "queued")

        db.update_refill_task_status("track-1", "sent_to_pharmacy", {"confirmation_id": "RX-1"})
        updated = db.get_refill_task("track-1")
        self.assertEqual(updated["status"], "sent_to_pharmacy")
        self.assertEqual(updated["result_payload"]["confirmation_id"], "RX-1")

    def test_get_refill_task_missing_returns_none(self):
        self.assertIsNone(db.get_refill_task("does-not-exist"))

    def test_idempotency_key_round_trip(self):
        self.assertIsNone(db.get_tracking_id_for_idempotency_key("key-1"))
        db.record_idempotency_key("key-1", "track-abc")
        self.assertEqual(db.get_tracking_id_for_idempotency_key("key-1"), "track-abc")

    def test_idempotency_key_insert_or_ignore_keeps_first_mapping(self):
        db.record_idempotency_key("key-2", "track-first")
        db.record_idempotency_key("key-2", "track-second")  # should be ignored
        self.assertEqual(db.get_tracking_id_for_idempotency_key("key-2"), "track-first")


if __name__ == "__main__":
    unittest.main()
