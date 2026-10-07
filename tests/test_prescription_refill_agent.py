import os
import sys
import datetime as dt
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from tests._helpers import IsolatedDBTestCase
from workflows import prescription_refill_agent as ra


class TestScheduleEligibility(unittest.TestCase):
    def _recent(self, days_ago=30):
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()

    def test_schedule_ii_always_ineligible(self):
        rx = {"is_controlled_substance": True, "controlled_schedule": "CII",
              "remaining_refills": 5, "written_date": self._recent()}
        result = ra.check_schedule_eligibility(rx)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["schedule"], "II")

    def test_schedule_iv_within_window_with_refills_eligible(self):
        rx = {"is_controlled_substance": True, "controlled_schedule": "CIV",
              "remaining_refills": 2, "written_date": self._recent(30)}
        result = ra.check_schedule_eligibility(rx)
        self.assertTrue(result["eligible"])

    def test_schedule_iv_past_six_months_ineligible(self):
        rx = {"is_controlled_substance": True, "controlled_schedule": "CIV",
              "remaining_refills": 2, "written_date": self._recent(200)}
        result = ra.check_schedule_eligibility(rx)
        self.assertFalse(result["eligible"])
        self.assertIn("6 month", result["reason"])

    def test_schedule_iv_no_refills_left_ineligible(self):
        rx = {"is_controlled_substance": True, "controlled_schedule": "CIV",
              "remaining_refills": 0, "written_date": self._recent(30)}
        result = ra.check_schedule_eligibility(rx)
        self.assertFalse(result["eligible"])

    def test_schedule_iv_missing_written_date_fails_closed(self):
        rx = {"is_controlled_substance": True, "controlled_schedule": "CIV",
              "remaining_refills": 2, "written_date": None}
        result = ra.check_schedule_eligibility(rx)
        self.assertFalse(result["eligible"])

    def test_schedule_iii_same_rules_as_iv(self):
        rx = {"is_controlled_substance": True, "controlled_schedule": "CIII",
              "remaining_refills": 1, "written_date": self._recent(400)}
        result = ra.check_schedule_eligibility(rx)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["schedule"], "III")

    def test_schedule_v_not_subject_to_six_month_limit(self):
        rx = {"is_controlled_substance": True, "controlled_schedule": "CV",
              "remaining_refills": 1, "written_date": self._recent(400)}  # very old, shouldn't matter
        result = ra.check_schedule_eligibility(rx)
        self.assertTrue(result["eligible"])

    def test_schedule_v_no_refills_left_ineligible(self):
        rx = {"is_controlled_substance": True, "controlled_schedule": "CV",
              "remaining_refills": 0, "written_date": self._recent(10)}
        result = ra.check_schedule_eligibility(rx)
        self.assertFalse(result["eligible"])

    def test_non_controlled_always_eligible_here(self):
        rx = {"is_controlled_substance": False}
        result = ra.check_schedule_eligibility(rx)
        self.assertTrue(result["eligible"])
        self.assertIsNone(result["schedule"])

    def test_unrecognized_schedule_fails_closed(self):
        rx = {"is_controlled_substance": True, "controlled_schedule": "CX",
              "remaining_refills": 5, "written_date": self._recent()}
        result = ra.check_schedule_eligibility(rx)
        self.assertFalse(result["eligible"])


class TestConfirmationFlow(IsolatedDBTestCase):
    def _make_prescription(self, patient_name="Jane Doe", medication="Cough Syrup with Codeine",
                            schedule="CV", refills=2, written_date=None):
        patient = self.make_patient(name=patient_name)
        rx_id = db.add_prescription(
            patient_id=patient["patient_id"], medication_name=medication,
            is_controlled_substance=True, controlled_schedule=schedule,
            remaining_refills=refills, written_date=written_date,
        )
        return patient, rx_id

    def test_correct_info_positive_confirmation_eligible(self):
        self._make_prescription()
        ticket = db.create_support_ticket("need refill", patient_name="Jane Doe")
        ra.start_confirmation(ticket, "Jane Doe", "Cough Syrup with Codeine")
        result = ra.confirm_positive(ticket)
        self.assertEqual(result["outcome"], "eligible")

    def test_wrong_info_then_corrected_becomes_eligible(self):
        self._make_prescription()
        ticket = db.create_support_ticket("need refill", patient_name="Jane D")
        ra.start_confirmation(ticket, "Wrong Name", "Wrong Med")
        neg = ra.confirm_negative(ticket)
        self.assertEqual(neg["stage"], "awaiting_correction")
        correction = ra.submit_correction(ticket, "Jane Doe", "Cough Syrup with Codeine")
        self.assertEqual(correction["outcome"], "verified_eligible")

    def test_unverifiable_info_escalates_without_fabricating_appointment(self):
        ticket = db.create_support_ticket("need refill", patient_name="Ghost Patient")
        ra.start_confirmation(ticket, "Ghost Patient", "Imaginary Med")
        result = ra.confirm_positive(ticket)
        self.assertEqual(result["outcome"], "escalated")
        self.assertIn("appointment_date", result)
        # No real patient was resolved, so no appointments-table row should exist.
        self.assertEqual(len(db.list_appointments()), 0)

    def test_schedule_ii_escalates_with_real_appointment_for_verified_patient(self):
        patient, _ = self._make_prescription(medication="Oxycodone", schedule="CII", refills=5)
        ticket = db.create_support_ticket("refill", patient_name="Jane Doe")
        ra.start_confirmation(ticket, "Jane Doe", "Oxycodone")
        result = ra.confirm_positive(ticket)
        self.assertEqual(result["outcome"], "escalated")
        self.assertEqual(result["eligibility"]["schedule"], "II")
        appts = db.list_appointments(patient["patient_id"])
        self.assertEqual(len(appts), 1)
        self.assertEqual(appts[0]["specialty"], "Prescription follow-up")

    def test_escalation_updates_ticket_status(self):
        ticket = db.create_support_ticket("need refill", patient_name="Ghost")
        ra.start_confirmation(ticket, "Ghost", "Nothing")
        ra.confirm_positive(ticket)
        updated = db.get_support_ticket(ticket)
        self.assertEqual(updated["status"], "In Progress")

    def test_confirm_positive_without_session_returns_error(self):
        result = ra.confirm_positive("999999")
        self.assertEqual(result["outcome"], "error")

    def test_confirmation_session_persists_claimed_fields(self):
        ticket = db.create_support_ticket("need refill")
        ra.start_confirmation(ticket, "Jane Doe", "Metformin")
        session = db.get_latest_refill_confirmation(ticket)
        self.assertEqual(session["claimed_patient_name"], "Jane Doe")
        self.assertEqual(session["claimed_medication_name"], "Metformin")
        self.assertEqual(session["stage"], "awaiting_confirmation")

    def test_list_refill_confirmations_returns_latest_per_ticket(self):
        t1 = db.create_support_ticket("refill 1")
        t2 = db.create_support_ticket("refill 2")
        ra.start_confirmation(t1, "A", "Med1")
        ra.start_confirmation(t2, "B", "Med2")
        all_confirmations = db.list_refill_confirmations()
        self.assertEqual(len(all_confirmations), 2)

    def test_list_refill_confirmations_filters_by_stage(self):
        t1 = db.create_support_ticket("refill 1")
        ra.start_confirmation(t1, "A", "Med1")
        ra.confirm_negative(t1)  # moves to awaiting_correction
        pending = db.list_refill_confirmations(stage="awaiting_confirmation")
        corrections = db.list_refill_confirmations(stage="awaiting_correction")
        self.assertEqual(len(pending), 0)
        self.assertEqual(len(corrections), 1)


if __name__ == "__main__":
    unittest.main()
