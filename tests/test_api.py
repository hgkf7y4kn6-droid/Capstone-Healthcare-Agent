"""Product API: auth boundaries, patient isolation, and the main staff flows."""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedDBTestCase
from data import patient_db as db

try:
    from fastapi.testclient import TestClient
    from api.app import app
    API_IMPORTABLE = True
except ImportError:
    API_IMPORTABLE = False


@unittest.skipUnless(API_IMPORTABLE, "FastAPI not installed in this environment")
class ProductApiTests(IsolatedDBTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)  # no context manager: skips demo seeding on startup
        token = self.client.post("/api/auth/staff", json={"code": "tendwell-demo", "name": "Test"}).json()["token"]
        self.staff = {"Authorization": f"Bearer {token}"}
        self.alice = self.make_patient(name="Alice Avery", dob="1970-02-02", ssn_last4="1234", external_id="A1")
        self.bob = self.make_patient(name="Bob Brook", dob="1980-03-03", external_id="B1")
        self.alice_rx = self.make_prescription(self.alice["patient_id"], medication_name="Metformin 500mg")
        self.bob_rx = self.make_prescription(self.bob["patient_id"], medication_name="Lorazepam 0.5mg",
                                             is_controlled_substance=True, controlled_schedule="CIV")

    def _portal(self, patient):
        r = self.client.post(f"/api/staff/patients/{patient['patient_id']}/portal-session", headers=self.staff)
        self.assertEqual(r.status_code, 200)
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def test_staff_routes_require_a_session(self):
        for path in ("/api/staff/overview", "/api/staff/patients", "/api/staff/refills", "/api/staff/insights"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.get("/api/staff/patients",
                                         headers={"Authorization": "Bearer junk"}).status_code, 401)

    def test_wrong_access_code_is_rejected(self):
        self.assertEqual(self.client.post("/api/auth/staff", json={"code": "guess"}).status_code, 401)

    def test_ssn_digits_never_leave_the_server(self):
        rows = self.client.get("/api/staff/patients", headers=self.staff).json()
        alice = next(r for r in rows if r["full_name"] == "Alice Avery")
        self.assertNotIn("ssn_last4", alice)
        self.assertTrue(alice["identity_factors"]["ssn_last4"])
        detail = self.client.get(f"/api/staff/patients/{self.alice['patient_id']}", headers=self.staff).json()
        self.assertNotIn("1234", str(detail))

    def test_patient_sees_only_their_own_data(self):
        alice = self._portal(self.alice)
        me = self.client.get("/api/me", headers=alice).json()
        self.assertEqual(me["patient"]["full_name"], "Alice Avery")
        self.assertEqual([rx["name"] for rx in me["prescriptions"]], ["Metformin 500mg"])
        self.assertNotIn("dob", me["patient"])
        r = self.client.post("/api/me/refills", headers=alice, json={"prescription_id": self.bob_rx["prescription_id"]})
        self.assertEqual(r.status_code, 404)

    def test_patient_refill_runs_the_rule_engine(self):
        bob = self._portal(self.bob)
        r = self.client.post("/api/me/refills", headers=bob, json={"prescription_id": self.bob_rx["prescription_id"]})
        self.assertEqual(r.json()["status"], "routed_to_review")
        self.assertEqual(len(db.list_provider_review(status="pending")), 1)

    def test_review_decision_is_single_use(self):
        bob = self._portal(self.bob)
        self.client.post("/api/me/refills", headers=bob, json={"prescription_id": self.bob_rx["prescription_id"]})
        review_id = db.list_provider_review()[0]["review_id"]
        url = f"/api/staff/reviews/{review_id}/decision"
        self.assertEqual(self.client.post(url, headers=self.staff, json={"decision": "approved"}).status_code, 200)
        self.assertEqual(self.client.post(url, headers=self.staff, json={"decision": "denied"}).status_code, 409)
        self.assertEqual(len(db.list_erx_queue()), 1)

    def test_assistant_booking_reaches_the_staff_schedule(self):
        alice = self._portal(self.alice)
        r = self.client.post("/api/me/assistant", headers=alice, json={"message": "please book a cardiologist"}).json()
        self.assertEqual(r["engine"] in ("concierge", "crew"), True)
        appts = self.client.get("/api/staff/appointments", headers=self.staff).json()
        self.assertEqual(appts[0]["status"], "requested")
        appt_id = appts[0]["appointment_id"]
        bad = self.client.patch(f"/api/staff/appointments/{appt_id}", headers=self.staff, json={"status": "confirmed"})
        self.assertEqual(bad.status_code, 422)
        ok = self.client.patch(f"/api/staff/appointments/{appt_id}", headers=self.staff,
                               json={"status": "confirmed", "start_time": "2026-11-03T09:30"})
        self.assertEqual(ok.json()["status"], "confirmed")

    def test_assistant_emergency_short_circuits(self):
        alice = self._portal(self.alice)
        r = self.client.post("/api/me/assistant", headers=alice, json={"message": "I have chest pain"}).json()
        self.assertEqual(r["engine"], "safety")
        self.assertEqual(r["safety"]["level"], "emergency")

    def test_staff_phone_refill_reverifies_identity(self):
        body = {"patient_id": self.alice["patient_id"], "prescription_id": self.alice_rx["prescription_id"],
                "date_of_birth": "1999-09-09"}
        self.assertEqual(self.client.post("/api/staff/refills", headers=self.staff, json=body).json()["status"],
                         "unauthorized")
        body["date_of_birth"] = "1970-02-02"
        self.assertEqual(self.client.post("/api/staff/refills", headers=self.staff, json=body).json()["status"],
                         "processed")

    def test_overview_counts(self):
        bob = self._portal(self.bob)
        self.client.post("/api/me/refills", headers=bob, json={"prescription_id": self.bob_rx["prescription_id"]})
        o = self.client.get("/api/staff/overview", headers=self.staff).json()
        self.assertEqual(o["counts"]["reviews"], 1)
        self.assertEqual(o["attention"][0]["kind"], "review")


if __name__ == "__main__":
    unittest.main()
