import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedDBTestCase
from data import patient_db as db

try:
    from api import portal_auth as pa
    PORTAL_AUTH_IMPORTABLE = pa.PYJWT_AVAILABLE
except ImportError:
    PORTAL_AUTH_IMPORTABLE = False


@unittest.skipUnless(PORTAL_AUTH_IMPORTABLE, "PyJWT not installed in this environment")
class TestPortalAuth(IsolatedDBTestCase):
    def setUp(self):
        super().setUp()
        import config
        self._original_secret = config.PORTAL_JWT_SECRET
        config.PORTAL_JWT_SECRET = "test-shared-secret"
        pa.PORTAL_JWT_SECRET = "test-shared-secret"

    def tearDown(self):
        import config
        config.PORTAL_JWT_SECRET = self._original_secret
        pa.PORTAL_JWT_SECRET = self._original_secret
        super().tearDown()

    def test_valid_token_decodes_and_resolves_patient(self):
        db.upsert_patient(full_name="Portal Patient", external_id="TP001")
        token = pa.create_test_token("TP001", scopes=["prescriptions:read", "refills:write"])
        payload = pa.decode_portal_token(token)
        self.assertEqual(payload["sub"], "TP001")

        patient = pa.resolve_patient(payload)
        self.assertEqual(patient["full_name"], "Portal Patient")

    def test_tampered_token_rejected(self):
        token = pa.create_test_token("TP001", scopes=["refills:write"])
        with self.assertRaises(pa.PortalAuthError):
            pa.decode_portal_token(token + "tampered")

    def test_expired_token_rejected(self):
        token = pa.create_test_token("TP001", scopes=["refills:write"], expires_in_seconds=-10)
        with self.assertRaises(pa.PortalAuthError):
            pa.decode_portal_token(token)

    def test_unknown_patient_rejected(self):
        token = pa.create_test_token("NOT-A-REAL-ID", scopes=["refills:write"])
        payload = pa.decode_portal_token(token)
        with self.assertRaises(pa.PortalAuthError):
            pa.resolve_patient(payload)

    def test_scope_check_passes_when_present(self):
        token = pa.create_test_token("TP001", scopes=["refills:write", "prescriptions:read"])
        payload = pa.decode_portal_token(token)
        pa.require_scope(payload, "refills:write")  # should not raise

    def test_scope_check_fails_when_missing(self):
        token = pa.create_test_token("TP001", scopes=["prescriptions:read"])
        payload = pa.decode_portal_token(token)
        with self.assertRaises(pa.PortalAuthError):
            pa.require_scope(payload, "refills:write")

    def test_missing_sub_claim_rejected(self):
        import jwt as pyjwt
        bad_token = pyjwt.encode({"scopes": ["x"]}, "test-shared-secret", algorithm="HS256")
        with self.assertRaises(pa.PortalAuthError):
            pa.decode_portal_token(bad_token)


if __name__ == "__main__":
    unittest.main()
