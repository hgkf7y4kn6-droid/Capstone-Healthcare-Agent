"""Clerk session-token verification. Tokens are minted with a throwaway RSA key and
checked through the same path production uses; no network and no Clerk account involved."""

import base64
import os
import sys
import time
import unittest
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedDBTestCase

try:
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient
    from api.app import app
    from api import clerk_auth, staff_auth
    import config
    HAVE = True
except Exception:  # pragma: no cover
    HAVE = False

from data import audit_log

HOST = "test-clinic-123.clerk.accounts.dev"
PK = "pk_test_" + base64.b64encode(f"{HOST}$".encode()).decode().rstrip("=")


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return key, pub


@unittest.skipUnless(HAVE, "FastAPI / cryptography not installed in this environment")
class ClerkAuthTests(IsolatedDBTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key, cls.pub = _keypair()
        cls.other_key, _ = _keypair()

    def setUp(self):
        super().setUp()
        self._p = [mock.patch.object(config, "CLERK_PUBLISHABLE_KEY", PK),
                   mock.patch.object(config, "CLERK_JWT_KEY", self.pub),
                   mock.patch.object(config, "CLERK_AUTHORIZED_PARTIES", [])]
        for p in self._p:
            p.start()
        self.client = TestClient(app)
        self.patient = self.make_patient(name="Ada Lovelace", dob="1965-12-10", recent_visit=True)

    def tearDown(self):
        for p in self._p:
            p.stop()
        super().tearDown()

    def token(self, key=None, **over):
        now = int(time.time())
        claims = {"iss": f"https://{HOST}", "sub": "user_abc", "sid": "sess_1", "iat": now, "nbf": now - 1,
                  "exp": now + 60, "role": "provider", "name": "Dr. Rao", "azp": "https://app.example.com"}
        claims.update(over)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or self.key, algorithm="RS256")

    def get(self, path, token=None, **kw):
        return self.client.get(path, headers={"Authorization": f"Bearer {token}"} if token else {}, **kw)

    def test_valid_token_reaches_the_chart_and_is_audited(self):
        pid = self.patient["patient_id"]
        r = self.get(f"/api/staff/patients/{pid}", self.token())
        self.assertEqual(r.status_code, 200)
        entry = [e for e in audit_log.list_entries(10) if e["patient_id"] == pid][0]
        self.assertEqual((entry["actor"], entry["actor_role"]), ("user_abc", "provider"))
        me = self.get("/api/auth/staff/me", self.token()).json()
        self.assertEqual((me["name"], me["role"]), ("Dr. Rao", "provider"))

    def test_bad_tokens_are_refused(self):
        now = int(time.time())
        cases = {
            "expired": self.token(exp=now - 60, iat=now - 120, nbf=now - 120),
            "wrong signing key": self.token(key=self.other_key),
            "wrong issuer": self.token(iss="https://evil.example.com"),
            "not yet valid": self.token(nbf=now + 3600),
            "no expiry": self.token(exp=None),
            "pending MFA": self.token(sts="pending"),
            "garbage": "a.b.c",
            "alg none": jwt.encode({"iss": f"https://{HOST}", "sub": "x", "role": "admin", "exp": now + 60,
                                    "iat": now, "nbf": now}, None, algorithm="none"),
            "HS256 signed with the public key": jwt.encode(
                {"iss": f"https://{HOST}", "sub": "x", "role": "admin", "exp": now + 60, "iat": now, "nbf": now},
                "secret-" * 8, algorithm="HS256"),
        }
        for name, tok in cases.items():
            self.assertEqual(self.get("/api/staff/patients", tok).status_code, 401, name)

    def test_no_role_or_unknown_role_is_forbidden_not_unauthenticated(self):
        self.assertEqual(self.get("/api/staff/patients", self.token(role=None)).status_code, 403)
        self.assertEqual(self.get("/api/staff/patients", self.token(role="superuser")).status_code, 403)

    def test_authorized_party_is_enforced_when_set(self):
        with mock.patch.object(config, "CLERK_AUTHORIZED_PARTIES", ["https://app.example.com"]):
            self.assertEqual(self.get("/api/staff/patients", self.token()).status_code, 200)
            self.assertEqual(self.get("/api/staff/patients", self.token(azp="https://evil.example.com")).status_code, 401)
            self.assertEqual(self.get("/api/staff/patients", self.token(azp=None)).status_code, 401)

    def test_roles_still_gate_routes(self):
        desk, doc, admin = (self.token(role=r) for r in ("front_desk", "provider", "admin"))
        self.assertEqual(self.get("/api/staff/audit", desk).status_code, 403)
        self.assertEqual(self.get("/api/staff/audit", doc).status_code, 403)
        self.assertEqual(self.get("/api/staff/audit", admin).status_code, 200)
        body = {"decision": "approve"}
        h = lambda t: {"Authorization": f"Bearer {t}"}
        self.assertEqual(self.client.post("/api/staff/reviews/1/decision", json=body, headers=h(desk)).status_code, 403)
        self.assertNotEqual(self.client.post("/api/staff/reviews/1/decision", json=body, headers=h(doc)).status_code, 403)

    def test_local_sign_in_paths_are_off(self):
        self.assertEqual(self.client.post("/api/auth/staff", json={"code": config.STAFF_ACCESS_CODE}).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/staff/login", json={
            "email": "a@b.test", "password": "x" * 12, "otp": "123456"}).status_code, 403)

    def test_old_local_sessions_and_cookies_stop_working(self):
        with mock.patch.object(config, "CLERK_PUBLISHABLE_KEY", ""):
            local = TestClient(app)
            local.post("/api/auth/staff", json={"code": config.STAFF_ACCESS_CODE})
            stale = local.cookies.get(staff_auth.COOKIE)
            self.assertEqual(local.get("/api/staff/patients").status_code, 200)
        self.assertEqual(self.client.get("/api/staff/patients", cookies={staff_auth.COOKIE: stale}).status_code, 401)
        self.assertEqual(self.get("/api/staff/patients", stale).status_code, 401)

    def test_account_management_lives_in_clerk(self):
        r = self.client.post("/api/staff/users", headers={"Authorization": f"Bearer {self.token(role='admin')}"},
                             json={"email": "n@c.test", "name": "N", "role": "provider", "password": "x" * 12})
        self.assertEqual(r.status_code, 409)

    def test_public_config_exposes_only_the_publishable_key(self):
        cfg = self.client.get("/api/public/config").json()
        self.assertEqual(cfg["clerk"], {"publishable_key": PK})
        self.assertFalse(cfg["demo"]["enabled"])
        self.assertIsNone(cfg["demo"]["access_code"])
        self.assertNotIn("sk_", str(cfg))

    def test_csp_allows_only_the_clerk_host(self):
        csp = self.client.get("/api/health").headers["content-security-policy"]
        self.assertIn(f"script-src 'self' https://{HOST}", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("*", csp)
        with mock.patch.object(config, "CLERK_PUBLISHABLE_KEY", ""):
            plain = TestClient(app).get("/api/health").headers["content-security-policy"]
        self.assertNotIn("clerk", plain)
        self.assertIn("script-src 'self';", plain)

    def test_jwks_outage_fails_closed(self):
        with mock.patch.object(config, "CLERK_JWT_KEY", ""), \
             mock.patch.object(clerk_auth, "_signing_key", side_effect=OSError("network down")):
            self.assertEqual(self.get("/api/staff/patients", self.token()).status_code, 503)

    def test_issuer_derived_from_the_publishable_key(self):
        self.assertEqual(clerk_auth.issuer(), f"https://{HOST}")


if __name__ == "__main__":
    unittest.main()
