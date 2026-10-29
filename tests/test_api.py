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
        r = self.client.post("/api/auth/staff", json={"code": "aegistriage-demo", "name": "Test"})
        self.assertNotIn("token", r.json())  # the session lives in an httpOnly cookie, not the body
        self.staff = {"X-Requested-With": "aegis"}  # the cookie rides in the client's jar
        self.alice = self.make_patient(name="Alice Avery", dob="1970-02-02", ssn_last4="1234", external_id="A1", recent_visit=True)
        self.bob = self.make_patient(name="Bob Brook", dob="1980-03-03", external_id="B1", recent_visit=True)
        self.alice_rx = self.make_prescription(self.alice["patient_id"], medication_name="Metformin 500mg")
        self.bob_rx = self.make_prescription(self.bob["patient_id"], medication_name="Lorazepam 0.5mg",
                                             is_controlled_substance=True, controlled_schedule="CIV")

    def _portal(self, patient):
        r = self.client.post(f"/api/staff/patients/{patient['patient_id']}/portal-session", headers=self.staff)
        self.assertEqual(r.status_code, 200)
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def test_staff_routes_require_a_session(self):
        anon = TestClient(app)
        for path in ("/api/staff/overview", "/api/staff/patients", "/api/staff/refills", "/api/staff/insights",
                     "/api/staff/users", "/api/staff/audit"):
            self.assertEqual(anon.get(path).status_code, 401, path)
        # an explicit bad bearer wins over a valid cookie
        self.assertEqual(self.client.get("/api/staff/patients",
                                         headers={"Authorization": "Bearer junk"}).status_code, 401)

    def test_security_headers(self):
        r = self.client.get("/api/health")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")

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

    def test_support_form_and_inbox_share_the_emergency_gate(self):
        alice = self._portal(self.alice)
        text = "My dad took too many pills"
        mine = self.client.post("/api/me/support", headers=alice, json={"message": text}).json()
        self.assertEqual(mine["category"], "safety_emergency")
        self.assertIn("Poison Control", mine["message"])
        self.assertEqual(mine["safety"]["rule_id"], "overdose_poisoning")
        inbox = self.client.post("/api/staff/inbox/messages", headers=self.staff, json={"message": text}).json()
        self.assertEqual(inbox["category"], "safety_emergency")
        tickets = self.client.get("/api/staff/inbox", headers=self.staff).json()["tickets"]
        self.assertEqual(sum(t["complaint_text"].startswith("[URGENT") for t in tickets), 2)

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
