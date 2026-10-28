"""
Prescription Refill Automation Engine — external webhook interface.

This is the FastAPI layer external systems (a patient portal, an IVR/SMS
front-end, a pharmacy system) call into. It's a thin transport wrapper
around workflows/prescription_refill.py — all the actual identity
verification, clinical rule engine, and queue-routing logic lives there and
is shared with the conversational agent (tools/prescription_tool.py), so
there is exactly one implementation of the clinical rules, not two.

Run standalone:
    uvicorn api.webhook_server:app --reload --port 8000

POINTING AT A REAL EHR: this environment has no live hospital FHIR server to
connect to, so the rule engine reads/writes the local SQLite EHR
(data/patient_db.py) instead of a remote FHIR endpoint. To integrate with a
real system, swap the calls inside workflows/prescription_refill.py for FHIR
resource calls (MedicationRequest, Patient, Observation) using the httpx
client stubbed below — the request/response contract of this API does not
need to change.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from data import patient_db as db
from workflows.prescription_refill import process_refill_request
from config import require_keys

app = FastAPI(
    title="Prescription Refill Automation Engine",
    description="Identity verification -> clinical rule engine -> golden-path eRx queue "
                "injection or provider review routing.",
    version="1.0.0",
)

# Mount the web-portal integration layer (JWT auth, async offloading,
# idempotency, live status) on the same app — see api/portal_integration.py
try:
    from api.portal_integration import router as portal_router
    from api.websocket_manager import manager as ws_manager
    import asyncio

    app.include_router(portal_router)
    from api.rate_limiting import limiter, SLOWAPI_AVAILABLE
    if SLOWAPI_AVAILABLE:
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.on_event("startup")
    async def _register_ws_loop():
        ws_manager.set_loop(asyncio.get_running_loop())
except ImportError as e:
    # FastAPI is present (we're already running) but a portal-layer
    # dependency (PyJWT, etc.) is missing — the base refill endpoints below
    # still work; only the portal integration routes are unavailable.
    import logging
    logging.getLogger(__name__).warning(f"Portal integration layer not mounted: {e}")

# Real-FHIR integration point (unused by default — see module docstring).
# Set these env vars and swap the internal DB calls for query_fhir() calls
# in workflows/prescription_refill.py to point this at a live EHR.
FHIR_BASE_URL = os.getenv("FHIR_BASE_URL", "")
FHIR_ACCESS_TOKEN = os.getenv("FHIR_ACCESS_TOKEN", "")


async def query_fhir(endpoint: str, params: Optional[dict] = None) -> dict:
    """Stub for a real FHIR EHR connection. Not called by default in this
    demo (see module docstring) — provided so a real integration is a
    drop-in swap rather than a rewrite."""
    if not FHIR_BASE_URL:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="No FHIR_BASE_URL configured. This deployment uses the local demo EHR "
                   "(data/patient_db.py) instead of a live FHIR server.",
        )
    headers = {"Authorization": f"Bearer {FHIR_ACCESS_TOKEN}", "Accept": "application/fhir+json"}
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{FHIR_BASE_URL}/{endpoint}", headers=headers, params=params, timeout=15)
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="EHR Connection Error")
        return response.json()


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class RefillRequest(BaseModel):
    patient_id: int = Field(..., description="Local patient_id (see /api/v1/patients)")
    date_of_birth: str = Field(..., description="YYYY-MM-DD, used for identity verification")
    prescription_id: int
    pharmacy_id: str
    home_address: Optional[str] = Field(
        default=None, description="Optional additional identity factor — if provided, must match on file."
    )
    ssn_last4: Optional[str] = Field(
        default=None, description="Optional additional identity factor: last 4 digits of SSN only, never the full SSN."
    )


class RefillResponse(BaseModel):
    status: str  # "processed" | "routed_to_review" | "unauthorized" | "error"
    message: str
    confirmation_id: Optional[str] = None
    reason: Optional[str] = None
    detail: Optional[str] = None
    review_id: Optional[int] = None


class ReviewDecision(BaseModel):
    decision: str = Field(..., description="'approved' or 'denied'")


# ---------------------------------------------------------------------------
# Core workflow endpoint
# ---------------------------------------------------------------------------

@app.post("/api/v1/refill-request", response_model=RefillResponse)
async def refill_request(request: RefillRequest):
    """The end-to-end pipeline: identity verification -> clinical rule
    engine -> golden-path eRx queue injection, or context pre-assembly +
    routing to the provider's review queue."""
    result = process_refill_request(
        patient_id=request.patient_id,
        date_of_birth=request.date_of_birth,
        prescription_id=request.prescription_id,
        pharmacy_id=request.pharmacy_id,
        address=request.home_address,
        ssn_last4=request.ssn_last4,
    )

    if result["status"] == "unauthorized":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=result["message"])
    if result["status"] == "error":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["message"])

    return RefillResponse(**result)


# ---------------------------------------------------------------------------
# Supporting endpoints (queue visibility + 1-click provider action)
# ---------------------------------------------------------------------------

@app.get("/api/v1/patients/{patient_id}/prescriptions")
async def get_prescriptions(patient_id: int):
    """List prescriptions on file for a patient (for a portal to display
    refill options)."""
    prescriptions = db.list_prescriptions(patient_id)
    if not prescriptions:
        raise HTTPException(status_code=404, detail="No prescriptions found for this patient.")
    return prescriptions


@app.get("/api/v1/erx-queue")
async def get_erx_queue(patient_id: Optional[int] = None):
    """The golden-path queue: refills auto-approved and sent to pharmacy."""
    return db.list_erx_queue(patient_id=patient_id)


@app.get("/api/v1/provider-review-queue")
async def get_provider_review_queue(patient_id: Optional[int] = None, pending_only: bool = True):
    """The flagged-path queue: refills routed to the provider for review,
    each with the full pre-assembled context payload for a 1-click decision."""
    return db.list_provider_review(patient_id=patient_id, status="pending" if pending_only else None)


@app.post("/api/v1/provider-review/{review_id}/decision")
async def decide_provider_review(review_id: int, decision: ReviewDecision):
    """The '1-click action' referenced in the workflow: a provider approves
    or denies a flagged refill directly from their review queue. Approving
    pushes it into the eRx queue exactly like the golden path would have."""
    if decision.decision not in ("approved", "denied"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'denied'")

    reviews = db.list_provider_review()
    review = next((r for r in reviews if r["review_id"] == review_id), None)
    if not review:
        raise HTTPException(status_code=404, detail=f"No review queue entry with id {review_id}")

    db.update_review_status(review_id, decision.decision)

    if decision.decision == "approved":
        prescription = db.get_prescription(review["prescription_id"])
        from workflows.prescription_refill import golden_path_queue_injection
        queue_result = golden_path_queue_injection(prescription, pharmacy_id="PROVIDER-APPROVED")
        return {"status": "approved_and_queued", "confirmation_id": queue_result["confirmation_id"]}

    return {"status": "denied"}


@app.get("/api/v1/health")
async def health():
    # The LLM is local and needs no API key; CALCOM_API_KEY is the only
    # thing that meaningfully needs to be configured for this service.
    missing = require_keys("CALCOM_API_KEY")
    return {"status": "ok", "missing_config": missing}
