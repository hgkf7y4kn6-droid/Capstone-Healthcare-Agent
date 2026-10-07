"""
Verifies JWTs issued by the existing web portal — this IS the identity
verification step for portal-originated requests (see workflows/
prescription_refill.py::process_refill_request_authenticated). The portal
authenticates the patient once in its own session; this backend trusts that
authentication by verifying the token's signature rather than asking the
patient to re-prove who they are.

Supports HS256 (shared secret) or RS256 (portal's public key) — configure
via PORTAL_JWT_SECRET / PORTAL_JWT_ALGORITHM / PORTAL_JWT_PUBLIC_KEY.

Token contract expected from the portal:
    {
      "sub": "<patient external_id, e.g. 'P001'>",
      "scopes": ["prescriptions:read", "refills:write"],
      "exp": <unix timestamp>
    }

The "sub" claim is resolved against this system's patients.external_id — in
other words, the portal is expected to identify patients using the same MRN
/ external ID your EHR import already uses (data/patient_db.py). Adjust the
lookup in resolve_patient() if your portal uses a different identifier.
"""

import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PORTAL_JWT_SECRET, PORTAL_JWT_ALGORITHM, PORTAL_JWT_PUBLIC_KEY
from data import patient_db as db

try:
    import jwt as pyjwt
    PYJWT_AVAILABLE = True
except ImportError:
    pyjwt = None
    PYJWT_AVAILABLE = False


class PortalAuthError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _verification_key():
    if PORTAL_JWT_ALGORITHM.upper().startswith("RS"):
        if not PORTAL_JWT_PUBLIC_KEY:
            raise PortalAuthError("PORTAL_JWT_PUBLIC_KEY is not configured for RS256 verification.", 500)
        return PORTAL_JWT_PUBLIC_KEY
    if not PORTAL_JWT_SECRET:
        raise PortalAuthError("PORTAL_JWT_SECRET is not configured for HS256 verification.", 500)
    return PORTAL_JWT_SECRET


def decode_portal_token(token: str) -> dict:
    """Verifies signature + expiry and returns the decoded payload. Raises
    PortalAuthError (never a bare exception) on any failure."""
    if not PYJWT_AVAILABLE:
        raise PortalAuthError(
            "PyJWT is not installed in this environment. Run `pip install -r requirements.txt`.", 500
        )
    try:
        payload = pyjwt.decode(token, _verification_key(), algorithms=[PORTAL_JWT_ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        raise PortalAuthError("Portal session token has expired.")
    except pyjwt.PyJWTError as e:
        raise PortalAuthError(f"Invalid portal session token: {e}")

    if "sub" not in payload:
        raise PortalAuthError("Token payload missing required 'sub' claim.")
    return payload


def resolve_patient(token_payload: dict) -> dict:
    """Maps the token's 'sub' claim (external patient ID) to the local
    patient record. Raises PortalAuthError if no match — a validly signed
    token for an unknown patient is still an authorization failure."""
    external_id = token_payload["sub"]
    patient = db.get_patient_by_external_id(external_id)
    if not patient:
        raise PortalAuthError(f"No patient record found for portal identity '{external_id}'.", 404)
    return patient


def require_scope(token_payload: dict, required_scope: str) -> None:
    scopes = token_payload.get("scopes", [])
    if required_scope not in scopes:
        raise PortalAuthError(f"Token missing required scope '{required_scope}'.", 403)


# ---------------------------------------------------------------------------
# Dev/test-only helper — NOT used by the real portal, which mints its own
# tokens. Handy for exercising the endpoints locally without a live portal.
# ---------------------------------------------------------------------------

def create_test_token(external_id: str, scopes: list[str], expires_in_seconds: int = 3600) -> str:
    """FOR LOCAL TESTING ONLY. The real web portal signs its own tokens;
    this exists so the integration can be exercised end-to-end without
    standing up the actual portal."""
    if not PYJWT_AVAILABLE:
        raise PortalAuthError("PyJWT is not installed.", 500)
    if PORTAL_JWT_ALGORITHM.upper().startswith("RS"):
        raise PortalAuthError("create_test_token only supports HS256 for local testing.", 500)
    payload = {"sub": external_id, "scopes": scopes, "exp": int(time.time()) + expires_in_seconds}
    return pyjwt.encode(payload, PORTAL_JWT_SECRET or "dev-only-insecure-secret", algorithm="HS256")
