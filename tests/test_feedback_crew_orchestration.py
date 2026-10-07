import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from tests._helpers import IsolatedDBTestCase
from workflows import feedback_support as fs


class TestFeedbackCrewOrchestration(IsolatedDBTestCase):
    def _make_prescription(self, patient_name="Jane Doe", medication="Metformin", refills=3):
        patient = self.make_patient(name=patient_name)
        db.add_prescription(patient_id=patient["patient_id"], medication_name=medication,
                             remaining_refills=refills)
        return patient

    def test_fresh_refill_request_starts_confirmation(self):
        from agents.feedback_crew import run_feedback_workflow
        self._make_prescription()
        result = run_feedback_workflow(
            "I need to refill my Metformin prescription.", patient_name="Jane Doe",
            corrected_medication_name="Metformin",
        )
        self.assertEqual(result["category"], fs.PRESCRIPTION_REFILL)
        self.assertEqual(result["stage"], "awaiting_confirmation")
        self.assertIn("ticket_number", result)
        self.assertIn(result["ticket_number"], result["message"])

    def test_fresh_refill_request_without_patient_name_asks_for_it(self):
        from agents.feedback_crew import run_feedback_workflow
        result = run_feedback_workflow("I need a refill on my prescription.")
        self.assertEqual(result["category"], fs.PRESCRIPTION_REFILL)
        self.assertNotIn("ticket_number", result)  # no ticket created without a patient name

    def test_positive_confirmation_via_session_routing(self):
        from agents.feedback_crew import run_feedback_workflow, start_prescription_refill
        self._make_prescription()
        ticket = db.create_support_ticket("refill", patient_name="Jane Doe")
        start_prescription_refill(ticket, "Jane Doe", "Metformin")

        result = run_feedback_workflow("Yes, that is correct", active_ticket_number=ticket)
        self.assertEqual(result["category"], fs.PRESCRIPTION_REFILL)
        self.assertEqual(result["classification_method"], "session_continuation")
        self.assertEqual(result["stage"], "resolved")

    def test_negative_confirmation_then_correction_full_loop(self):
        from agents.feedback_crew import run_feedback_workflow, start_prescription_refill
        self._make_prescription()
        ticket = db.create_support_ticket("refill", patient_name="Wrong")
        start_prescription_refill(ticket, "Wrong Name", "Wrong Med")

        neg = run_feedback_workflow("No, that's wrong", active_ticket_number=ticket)
        self.assertEqual(neg["stage"], "awaiting_correction")

        corrected = run_feedback_workflow(
            "Patient is Jane Doe, medication is Metformin", active_ticket_number=ticket,
            corrected_patient_name="Jane Doe", corrected_medication_name="Metformin",
        )
        self.assertEqual(corrected["stage"], "resolved")
        self.assertIn("verified", corrected["message"].lower())

    def test_correction_stage_without_corrected_fields_asks_again(self):
        from agents.feedback_crew import run_feedback_workflow, start_prescription_refill
        ticket = db.create_support_ticket("refill", patient_name="Wrong")
        start_prescription_refill(ticket, "Wrong Name", "Wrong Med")
        run_feedback_workflow("No, that's wrong", active_ticket_number=ticket)

        # Reply with no structured correction fields -- should ask again, not crash.
        result = run_feedback_workflow("I'll get back to you", active_ticket_number=ticket)
        self.assertEqual(result["stage"], "awaiting_correction")

    def test_spec_example_2_chains_negative_feedback_into_refill_agent(self):
        from agents.feedback_crew import run_feedback_workflow
        self._make_prescription(patient_name="John Smith", medication="Lisinopril")
        result = run_feedback_workflow(
            "My medication refill still hasn't been filled.", patient_name="John Smith",
        )
        self.assertEqual(result["category"], fs.NEGATIVE_FEEDBACK)
        self.assertIn("ticket", result["message"].lower())
        self.assertIn("chained_refill_agent", result)
        self.assertEqual(result["chained_refill_agent"]["stage"], "awaiting_confirmation")

    def test_plain_complaint_without_refill_keywords_does_not_chain(self):
        from agents.feedback_crew import run_feedback_workflow
        result = run_feedback_workflow(
            "This is unacceptable, the appointment was cancelled with no notice.",
            patient_name="Someone",
        )
        self.assertEqual(result["category"], fs.NEGATIVE_FEEDBACK)
        self.assertNotIn("chained_refill_agent", result)

    def test_continue_workflow_with_no_session_falls_through_to_classifier(self):
        from agents.feedback_crew import run_feedback_workflow
        result = run_feedback_workflow("Yes", active_ticket_number="000000")
        # No open session exists for this ticket number, so it isn't
        # treated as a session-continuation reply -- classifier runs normally.
        self.assertIn(result["category"], fs.VALID_CATEGORIES)
        self.assertNotEqual(result.get("classification_method"), "session_continuation")


if __name__ == "__main__":
    unittest.main()
