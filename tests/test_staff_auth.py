"""Individual staff accounts: MFA, roles, idle timeout, revocation, cookies, and the audit trail they feed."""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedDBTestCase

try:
    from fastapi.testclient import TestClient
    from api.app import app
    from api import staff_auth
    import config
    HAVE_API = True
except Exception:  # pragma: no cover
    HAVE_API = False

from data import audit_log, staff_store

PW = "correct horse battery"
CSRF = {"X-Requested-With": "aegis"}


@unittest.skipUnless(HAVE_API, "FastAPI not installed in this environment")
class StaffIdentityTests(IsolatedDBTestCase):
    def setUp(self):
        super().setUp()
        staff_auth._failed.clear()
        self.admin = staff_auth.create_account("admin@clinic.test", "Avery Admin", "admin", PW)
        self.doc = staff_auth.create_account("doc@clinic.test", "Dr. Rao", "provider", PW)
        self.desk = staff_auth.create_account("desk@clinic.test", "Dana Desk", "front_desk", PW)
        self.patient = self.make_patient(name="Ada Lovelace", dob="1965-12-10", recent_visit=True)

    def _otp(self, user, offset=0):
        step = int(time.time() // 30) + offset
        return staff_auth._totp(user["totp_secret"], step)

    def _login(self, user, password=PW, otp=None, client=None):
        client = client or TestClient(app)
        r = client.post("/api/auth/staff/login", json={"email": user["email"], "password": password,
                                                       "otp": otp or self._otp(user)})
        return client, r

    # --- crypto
    def test_totp_matches_rfc6238_vector(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
        self.assertEqual(staff_auth._totp(secret, 59 // 30), "287082")  # RFC 6238, last six digits of 94287082

    def test_totp_replay_and_window(self):
        u = self.doc
        step = staff_auth.verify_totp(u["totp_secret"], self._otp(u), 0)
        self.assertIsNotNone(step)
        self.assertIsNone(staff_auth.verify_totp(u["totp_secret"], self._otp(u), step))  # replay
        self.assertIsNone(staff_auth.verify_totp(u["totp_secret"], self._otp(u, 5), 0))  # far outside the window

    def test_password_rules(self):
        with self.assertRaises(ValueError):
            staff_auth.create_account("x@clinic.test", "X", "provider", "short")
        with self.assertRaises(ValueError):
            staff_auth.create_account("x@clinic.test", "X", "janitor", PW)
        self.assertFalse(staff_auth.verify_password("wrong", staff_auth.hash_password(PW)))

    # --- login
    def test_login_sets_httponly_cookie_and_no_token_in_body(self):
        client, r = self._login(self.doc)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("token", r.json())
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(client.get("/api/auth/staff/me").json()["role"], "provider")

    def test_wrong_password_and_wrong_code_read_the_same(self):
        _, bad_pw = self._login(self.doc, password="nope nope nope nope")
        _, bad_otp = self._login(self.doc, otp="000000")
        _, no_user = self._login({"email": "ghost@clinic.test", "totp_secret": self.doc["totp_secret"]})
        self.assertEqual({bad_pw.status_code, bad_otp.status_code, no_user.status_code}, {401})
        self.assertEqual(bad_pw.json(), bad_otp.json())
        self.assertEqual(bad_pw.json(), no_user.json())

    def test_account_locks_after_repeated_failures(self):
        for _ in range(staff_auth.LOCK_AFTER):
            self._login(self.doc, password="nope nope nope nope")
        _, r = self._login(self.doc)  # even the right credentials are refused while locked
        self.assertEqual(r.status_code, 401)

    def test_a_code_cannot_be_used_twice(self):
        otp = self._otp(self.doc)
        self.assertEqual(self._login(self.doc, otp=otp)[1].status_code, 200)
        self.assertEqual(self._login(self.doc, otp=otp)[1].status_code, 401)

    # --- sessions
    def test_cookie_writes_need_the_csrf_header(self):
        client, _ = self._login(self.doc)
        body = {"full_name": "New Person", "age": 40, "sex": "F", "relation_to_user": "self"}
        self.assertEqual(client.post("/api/staff/patients", json=body).status_code, 403)
        self.assertEqual(client.post("/api/staff/patients", json=body, headers=CSRF).status_code, 201)

    def test_idle_timeout_ends_the_session(self):
        client, _ = self._login(self.doc)
        self.assertEqual(client.get("/api/staff/patients").status_code, 200)
        later = time.time() + config.STAFF_IDLE_MINUTES * 60 + 5
        with mock.patch.object(staff_auth.time, "time", return_value=later):
            self.assertEqual(client.get("/api/staff/patients").status_code, 401)

    def test_activity_keeps_the_session_alive(self):
        client, _ = self._login(self.doc)
        t = time.time()
        for minutes in (10, 20, 30):  # each gap is under 15 minutes
            with mock.patch.object(staff_auth.time, "time", return_value=t + minutes * 60):
                self.assertEqual(client.get("/api/staff/patients").status_code, 200)

    def test_logout_revokes_server_side(self):
        client, _ = self._login(self.doc)
        stolen = client.cookies.get(staff_auth.COOKIE)
        self.assertEqual(client.post("/api/auth/staff/logout", headers=CSRF).status_code, 200)
        thief = TestClient(app)
        self.assertEqual(thief.get("/api/staff/patients", headers={"Authorization": f"Bearer {stolen}"}).status_code, 401)

    def test_deactivation_ends_live_sessions_immediately(self):
        admin, _ = self._login(self.admin)
        desk, _ = self._login(self.desk)
        self.assertEqual(desk.get("/api/staff/patients").status_code, 200)
        r = admin.post(f"/api/staff/users/{self.desk['user_id']}/deactivate", headers=CSRF)
        self.assertEqual(r.json()["sessions_ended"], 1)
        self.assertEqual(desk.get("/api/staff/patients").status_code, 401)
        self.assertEqual(self._login(self.desk)[1].status_code, 401)

    def test_revoke_sessions_leaves_the_account_usable(self):
        admin, _ = self._login(self.admin)
        doc, _ = self._login(self.doc)
        admin.post(f"/api/staff/users/{self.doc['user_id']}/revoke-sessions", headers=CSRF)
        self.assertEqual(doc.get("/api/staff/patients").status_code, 401)
        staff_auth._failed.clear()
        self.assertEqual(staff_store.get_user(self.doc["user_id"])["active"], 1)

    def test_last_admin_cannot_be_deactivated(self):
        admin, _ = self._login(self.admin)
        r = admin.post(f"/api/staff/users/{self.admin['user_id']}/deactivate", headers=CSRF)
        self.assertEqual(r.status_code, 409)

    # --- roles
    def test_roles_gate_admin_and_clinical_routes(self):
        desk, _ = self._login(self.desk)
        doc, _ = self._login(self.doc)
        for path in ("/api/staff/users", "/api/staff/audit", "/api/staff/audit/verify"):
            self.assertEqual(desk.get(path).status_code, 403, path)
            self.assertEqual(doc.get(path).status_code, 403, path)
        self.assertEqual(desk.post("/api/staff/reviews/1/decision", json={"decision": "approve"}, headers=CSRF).status_code, 403)
        self.assertNotEqual(doc.post("/api/staff/reviews/1/decision", json={"decision": "approve"}, headers=CSRF).status_code, 403)

    def test_admin_creates_accounts_and_secret_is_shown_once(self):
        admin, _ = self._login(self.admin)
        r = admin.post("/api/staff/users", headers=CSRF, json={
            "email": "new@clinic.test", "name": "Nia New", "role": "front_desk", "password": PW})
        self.assertEqual(r.status_code, 201)
        self.assertIn("totp_secret", r.json())
        listed = admin.get("/api/staff/users").json()["users"]
        self.assertNotIn("totp_secret", str(listed))
        self.assertNotIn("password_hash", str(listed))
        dup = admin.post("/api/staff/users", headers=CSRF, json={
            "email": "new@clinic.test", "name": "Again", "role": "provider", "password": PW})
        self.assertEqual(dup.status_code, 409)

    # --- demo code
    def test_shared_code_works_only_in_demo_mode(self):
        c = TestClient(app)
        self.assertEqual(c.post("/api/auth/staff", json={"code": config.STAFF_ACCESS_CODE}).status_code, 200)
        with mock.patch.object(config, "DEMO_MODE", False):
            self.assertEqual(TestClient(app).post("/api/auth/staff", json={"code": config.STAFF_ACCESS_CODE}).status_code, 403)

    # --- audit trail
    def test_chart_reads_and_writes_are_audited_before_data_returns(self):
        doc, _ = self._login(self.doc)
        pid = self.patient["patient_id"]
        doc.get(f"/api/staff/patients/{pid}")
        doc.post(f"/api/staff/patients/{pid}/history", json={"entry_type": "note", "content": "Called pharmacy"}, headers=CSRF)
        entries = [e for e in audit_log.list_entries(50) if e["patient_id"] == pid]
        actions = {e["action"] for e in entries}
        self.assertIn("GET /api/staff/patients/{patient_id}", actions)
        self.assertIn("POST /api/staff/patients/{patient_id}/history", actions)
        self.assertEqual({e["actor"] for e in entries}, {"Dr. Rao"})
        self.assertNotIn("Called pharmacy", str(audit_log.list_entries(50)))  # ids and actions, never content
        self.assertTrue(audit_log.verify_chain()["ok"])

    def test_if_the_audit_log_cannot_write_no_chart_data_is_served(self):
        doc, _ = self._login(self.doc)
        with mock.patch.object(audit_log, "record", side_effect=RuntimeError("disk full")):
            r = TestClient(app, raise_server_exceptions=False).get(
                f"/api/staff/patients/{self.patient['patient_id']}", cookies=dict(doc.cookies))
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("Ada Lovelace", r.text)

    def test_sign_in_failures_and_denials_are_audited(self):
        self._login(self.doc, password="nope nope nope nope")
        desk, _ = self._login(self.desk)
        desk.get("/api/staff/audit")
        got = {(e["action"], e["outcome"]) for e in audit_log.list_entries(50)}
        self.assertIn(("auth.login", "http_401"), got)
        self.assertIn(("auth.login", "ok"), got)
        self.assertIn(("GET /api/staff/audit", "http_403"), got)

    def test_patient_portal_access_is_audited(self):
        staff, _ = self._login(self.admin)
        tok = staff.post(f"/api/staff/patients/{self.patient['patient_id']}/portal-session", headers=CSRF).json()["token"]
        TestClient(app).get("/api/me", headers={"Authorization": f"Bearer {tok}"})
        mine = [e for e in audit_log.list_entries(50) if e["actor_role"] == "patient"]
        self.assertTrue(mine)
        self.assertEqual(mine[0]["patient_id"], self.patient["patient_id"])

    def test_admin_can_read_and_verify_the_trail(self):
        admin, _ = self._login(self.admin)
        self.assertTrue(admin.get("/api/staff/audit/verify").json()["ok"])
        self.assertTrue(admin.get(f"/api/staff/audit?patient_id={self.patient['patient_id']}").status_code == 200)


if __name__ == "__main__":
    unittest.main()
