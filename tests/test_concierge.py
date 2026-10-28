"""The deterministic concierge and the assistant facade (no CrewAI, no network)."""

import os
import sys
import unittest
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedDBTestCase
from data import patient_db as db
from agents import concierge
from agents.assistant import answer


class ConciergeTests(IsolatedDBTestCase):
    def setUp(self):
        super().setUp()
        self.patient = self.make_patient(name="Ada Lovelace", dob="1965-12-10")
        db.add_history_entry(self.patient["patient_id"], "diagnosis", {"condition": "Hypertension"}, True)
        db.add_history_entry(self.patient["patient_id"], "note", "Internal: prefers morning calls", False)
        self.rx = self.make_prescription(self.patient["patient_id"], medication_name="Lisinopril 10mg",
                                         remaining_refills=0)
        self._no_network = mock.patch.object(concierge, "_medlineplus_lookup", return_value=[])
        self._no_network.start()

    def tearDown(self):
        self._no_network.stop()
        super().tearDown()

    def test_booking_creates_a_request_for_staff(self):
        r = concierge.run_concierge("Please book a cardiologist for me", self.patient["patient_id"])
        appts = db.list_appointments(self.patient["patient_id"])
        self.assertEqual(len(appts), 1)
        self.assertEqual(appts[0]["status"], "requested")
        self.assertEqual(appts[0]["specialty"], "Cardiology")
        self.assertIn("Cardiology", r["final_response"])
        self.assertEqual(r["cards"][0]["type"], "appointment_request")

    def test_booking_without_patient_asks_who(self):
        r = concierge.run_concierge("I want to book an appointment", None)
        self.assertEqual(db.list_appointments(), [])
        self.assertEqual(r["actions"][0]["intent"], "select_patient")

    def test_records_hide_internal_notes(self):
        r = concierge.run_concierge("show me my medical history", self.patient["patient_id"])
        self.assertIn("Hypertension", r["final_response"])
        self.assertNotIn("prefers morning calls", r["final_response"])

    def test_medications_flag_empty_refills(self):
        r = concierge.run_concierge("what are my medications?", self.patient["patient_id"])
        self.assertIn("no refills left", r["final_response"])
        self.assertEqual(r["actions"][0]["intent"], "refill")
        self.assertNotIn("sources", [c["type"] for c in r["cards"]], "a meds question isn't a condition lookup")

    def test_condition_info_falls_back_to_link_offline(self):
        r = concierge.run_concierge("tell me about psoriasis", None)
        sources = [c for c in r["cards"] if c["type"] == "sources"][0]
        self.assertIn("medlineplus.gov", sources["items"][0]["url"])

    def test_multi_intent_query(self):
        q = ("My father has chronic kidney disease. I want to book a nephrologist for him. "
             "Also, can you summarize latest treatment methods?")
        r = concierge.run_concierge(q, self.patient["patient_id"])
        goals = [g["tool_category"] for g in r["plan"]["sub_goals"]]
        self.assertEqual(goals, ["calendar", "medical_search"])
        self.assertIn("Nephrology", r["final_response"])

    def test_facade_stops_on_emergency(self):
        r = answer("I'm having chest pain right now", self.patient["patient_id"], engine="concierge")
        self.assertEqual(r["engine"], "safety")
        self.assertIn("911", r["final_response"])
        self.assertEqual(db.list_appointments(), [], "nothing else happens on an emergency")

    def test_facade_auto_falls_back_without_crewai(self):
        with mock.patch("agents.assistant.crew_available", return_value=False):
            r = answer("hello", self.patient["patient_id"])
        self.assertEqual(r["engine"], "concierge")
        self.assertIsNone(r["safety"])


if __name__ == "__main__":
    unittest.main()
