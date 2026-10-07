"""
Web portal integration layer.

Implements the architecture:

    [ Existing Web Portal ] --(JWT / REST)--> [ Python Integration Backend ]
                                                        |
                                               (Async Celery Task)
                                                        |
                                                        v
    [ Hospital EHR Engine ] <--(HL7 FHIR)-- [ Automation Rules Engine ]

Endpoints (all under /api/v1, JWT-authenticated except /health):
    GET  /api/v1/prescriptions              — active/refillable meds for the token's patient
    POST /api/v1/refills                    — submit a refill request (idempotent, async)
    GET  /api/v1/refills/status/{tracking_id} — poll live status
    WS   /ws/refills/{patient_id}           — live status push (same-process fallback mode)

Identity comes entirely from the JWT (see api/portal_auth.py) — the patient
is never asked to re-authenticate or re-enter a DOB here, which is the
whole point of the portal handoff.
"""

import os
import sys
import uuid

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, Header, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from data import patient_db as db
from api.portal_auth import decode_portal_token, resolve_patient, require_scope, PortalAuthError
from api.websocket_manager import manager as ws_manager
from tasks.refill_tasks import run_synchronously_as_fallback

try:
    from tasks.celery_app import CELERY_AVAILABLE
    from tasks.refill_tasks import process_clinical_rules_engine
except ImportError:
    CELERY_AVAILABLE = False
    process_clinical_rules_engine = None

try:
    from api.rate_limiting import limiter, SLOWAPI_AVAILABLE
except ImportError:
    limiter, SLOWAPI_AVAILABLE = None, False


router = APIRouter(prefix="/api/v1", tags=["portal-integration"])
security = HTTPBearer()


# ---------------------------------------------------------------------------
# JWT auth dependency
# ---------------------------------------------------------------------------

def verify_portal_jwt(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Decodes and validates the JWT issued by the existing web portal,
    resolves it to a local patient record, and stashes the identity on
    request.state for rate limiting. Raises 401/403/404 as appropriate."""
    try:
        payload = decode_portal_token(credentials.credentials)
        patient = resolve_patient(payload)
    except PortalAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    request.state.portal_patient_sub = payload["sub"]
    return {"token": payload, "patient": patient}


def _require_scope_dep(scope: str):
    def dep(ctx: dict = Depends(verify_portal_jwt)) -> dict:
        try:
            require_scope(ctx["token"], scope)
        except PortalAuthError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)
        return ctx
    return dep


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PortalRefillPayload(BaseModel):
    prescription_id: int
    pharmacy_id: str
    notes: Optional[str] = None


class RefillAccepted(BaseModel):
    message: str
    tracking_id: str
    status: str
    duplicate_of_existing_request: bool = False


class RefillStatusResponse(BaseModel):
    tracking_id: str
    status: str
    result: Optional[dict] = None
    created_at: str
    updated_at: str


_STATUS_DISPLAY = {
    "queued": "Queued",
    "processing": "Processing",
    "sent_to_pharmacy": "Sent to Pharmacy",
    "pending_provider_review": "Pending Provider Review",
    "unauthorized": "Unauthorized",
    "error": "Error",
}


# ---------------------------------------------------------------------------
# Step 2: RESTful endpoints
# ---------------------------------------------------------------------------

@router.get("/prescriptions")
async def get_prescriptions(ctx: dict = Depends(_require_scope_dep("prescriptions:read"))):
    """Active, refillable medications for the authenticated patient."""
    patient = ctx["patient"]
    prescriptions = db.list_prescriptions(patient["patient_id"])
    active = [rx for rx in prescriptions if rx.get("status") == "active"]
    return active


@router.post("/refills", status_code=202, response_model=RefillAccepted)
async def submit_refill(
    payload: PortalRefillPayload,
    background_tasks: BackgroundTasks,
    ctx: dict = Depends(_require_scope_dep("refills:write")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Receives the refill selection from the portal, instantly acknowledges
    with a tracking ID, and offloads the actual clinical rule engine to a
    background task (Celery if configured, else FastAPI BackgroundTasks)."""
    patient = ctx["patient"]

    # Idempotency: a double-submitted request (double-click, retry-on-timeout)
    # returns the *original* task instead of creating a duplicate refill.
    if idempotency_key:
        existing_tracking_id = db.get_tracking_id_for_idempotency_key(idempotency_key)
        if existing_tracking_id:
            existing = db.get_refill_task(existing_tracking_id)
            return RefillAccepted(
                message="Duplicate request detected; returning the original in-flight request.",
                tracking_id=existing_tracking_id,
                status=_STATUS_DISPLAY.get(existing["status"], existing["status"]),
                duplicate_of_existing_request=True,
            )

    tracking_id = str(uuid.uuid4())
    db.create_refill_task(
        tracking_id=tracking_id,
        patient_id=patient["patient_id"],
        prescription_id=payload.prescription_id,
        pharmacy_id=payload.pharmacy_id,
        identity_method="jwt",
        status="queued",
    )
    if idempotency_key:
        db.record_idempotency_key(idempotency_key, tracking_id)

    # Step 3: async offloading — prefer Celery, fall back to BackgroundTasks
    if CELERY_AVAILABLE and process_clinical_rules_engine is not None:
        process_clinical_rules_engine.delay(
            tracking_id, patient["patient_id"], payload.prescription_id, payload.pharmacy_id
        )
    else:
        background_tasks.add_task(
            run_synchronously_as_fallback,
            tracking_id, patient["patient_id"], payload.prescription_id, payload.pharmacy_id,
        )

    return RefillAccepted(
        message="Refill request successfully queued for clinical processing.",
        tracking_id=tracking_id,
        status="Queued",
    )


@router.get("/refills/status/{tracking_id}", response_model=RefillStatusResponse)
async def get_refill_status(tracking_id: str, ctx: dict = Depends(_require_scope_dep("prescriptions:read"))):
    """Live tracking status for a specific refill request — the portal
    polls this (or listens on the WebSocket below) to update its UI."""
    task = db.get_refill_task(tracking_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"No refill request found with tracking_id={tracking_id}")
    if task["patient_id"] != ctx["patient"]["patient_id"]:
        raise HTTPException(status_code=403, detail="This tracking ID does not belong to the authenticated patient.")

    return RefillStatusResponse(
        tracking_id=task["tracking_id"],
        status=_STATUS_DISPLAY.get(task["status"], task["status"]),
        result=task.get("result_payload"),
        created_at=task["created_at"],
        updated_at=task["updated_at"],
    )


# ---------------------------------------------------------------------------
# Step 4b: WebSocket for live browser updates (same-process fallback mode —
# see api/websocket_manager.py for the Celery cross-process caveat)
# ---------------------------------------------------------------------------

@router.websocket("/ws/refills/{patient_id}")
async def refill_status_websocket(websocket: WebSocket, patient_id: int):
    await ws_manager.connect(patient_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive; portal doesn't need to send anything
    except WebSocketDisconnect:
        ws_manager.disconnect(patient_id, websocket)


# Apply rate limiting to the mutating endpoint if slowapi is available.
if SLOWAPI_AVAILABLE and limiter is not None:
    submit_refill = limiter.limit("10/minute")(submit_refill)
