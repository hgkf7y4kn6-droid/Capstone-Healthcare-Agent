"""The shared safety gate on the support form and the staff inbox."""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedDBTestCase
from data import patient_db as db
from agents.feedback_crew import run_feedback_workflow
from workflows.safety_gate import SAFETY_EMERGENCY


class SafetyGateTests(IsolatedDBTestCase):
    def test_too_many_pills_is_an_emergency_with_an_urgent_ticket(self):
        for text in ("My mom took too many pills this morning", "I think I've had too many pills",
                     "She may have overdosed"):
            r = run_feedback_workflow(text, patient_name="Ada Lovelace")
            self.assertEqual(r["category"], SAFETY_EMERGENCY, text)
            self.assertIn("Poison Control", r["message"])
            self.assertEqual(r["safety"]["rule_id"], "overdose_poisoning")
            ticket = db.get_support_ticket(r["ticket_number"])
            self.assertTrue(ticket["complaint_text"].startswith("[URGENT"))
            self.assertEqual(ticket["status"], "Unresolved")

    def test_emergency_beats_an_open_refill_confirmation_session(self):
        db.create_support_ticket("refill please", patient_name="Ada Lovelace")
        r = run_feedback_workflow("yes but also I can't breathe", patient_name="Ada Lovelace", active_ticket_number="000000")
        self.assertEqual(r["category"], SAFETY_EMERGENCY)

    def test_urgent_leads_with_guidance_and_still_helps(self):
        r = run_feedback_workflow("Thanks! My baby has a fever though", patient_name="Ada Lovelace")
        self.assertTrue(r["message"].startswith("**A fever in a baby"))
        self.assertEqual(r["safety"]["level"], "urgent")

    def test_ordinary_messages_are_untouched(self):
        r = run_feedback_workflow("Thank you, great service", patient_name="Ada Lovelace")
        self.assertNotIn("safety", r)
        self.assertEqual(r["category"], "positive_feedback")


if __name__ == "__main__":
    unittest.main()
