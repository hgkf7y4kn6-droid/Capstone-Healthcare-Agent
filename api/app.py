"""
Tendwell AA product API — the one server behind the web app.

    uvicorn api.app:app --reload --port 8000

Three audiences, three trust boundaries:
  /api/public/*  no auth: product config for the landing and sign-in pages
  /api/staff/*   staff session (api/staff_auth.py): the clinic console
  /api/me/*      patient portal JWT (api/portal_auth.py): the patient's own data only

Routes are transport only. Every rule lives in workflows/ (refill gates,
safety screen, support routing) and is shared with the agents, the legacy
webhook API and the Streamlit ops console, so there is one implementation of
each clinical rule. Patient responses never include internal fields (SSN
digits, address, staff notes); staff responses never include SSN digits.

The built web app (web/dist) is served from the same origin, so one Render
service hosts everything.
"""

import os
import sys
import time
import uuid
from collections import Counter
from typing import Literal, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
from version import __version__
from api import staff_auth
from api.portal_auth import PortalAuthError, create_test_token, decode_portal_token, require_scope, resolve_patient
from data import patient_db as db
from data.bootstrap import ensure_ready
from monitoring.logging_utils import log_event, read_events
from workflows.safety import DISCLAIMER

PATIENT_SCOPES = ["prescriptions:read", "refills:write", "assistant:use", "records:read"]

app = FastAPI(title=f"{config.APP_NAME} API", version=__version__,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])

# Legacy integration surface for external portals (JWT, async, idempotent).
try:
    from api.portal_integration import router as portal_router
    from api.rate_limiting import limiter, SLOWAPI_AVAILABLE
    app.include_router(portal_router)
    if SLOWAPI_AVAILABLE:
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
except ImportError:  # optional dependency missing — the product API still runs
    pass


@app.on_event("startup")
def _startup() -> None:
    ensure_ready()


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# --------------------------------------------------------------------------- helpers

def _staff_patient(p: dict) -> dict:
    """What the console may see. SSN digits never leave the server."""
    out = {k: v for k, v in p.items() if k != "ssn_last4"}
    out["identity_factors"] = {"dob": bool(p.get("dob")), "address": bool(p.get("address")),
                               "ssn_last4": bool(p.get("ssn_last4"))}
    out["specialty"] = _specialty_of(p.get("primary_doctor"))
    return out


def _portal_patient(p: dict) -> dict:
    return {"patient_id": p["patient_id"], "full_name": p["full_name"], "first_name": p["full_name"].split()[0],
            "age": p.get("age"), "primary_doctor": p.get("primary_doctor"),
            "next_appointment": p.get("next_appointment")}


def _specialty_of(doctor: Optional[str]) -> Optional[str]:
    if doctor and "(" in doctor and doctor.endswith(")"):
        return doctor[doctor.rindex("(") + 1:-1]
    return None


def _rx_view(rx: dict) -> dict:
    return {
        "prescription_id": rx["prescription_id"], "patient_id": rx["patient_id"],
        "full_name": rx.get("full_name"), "name": rx["medication_name"],
        "refills": rx["remaining_refills"], "controlled": bool(rx["is_controlled_substance"]),
        "schedule": rx.get("controlled_schedule"),
        "lab": rx["lab_check_type"] if rx["requires_lab_check"] else None,
        "lab_range": rx.get("lab_safe_range") if rx["requires_lab_check"] else None,
        "prescriber": rx.get("prescribing_doctor"), "status": rx.get("status"),
    }


def _get_patient_or_404(patient_id: int) -> dict:
    patient = db.get_patient(patient_id)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found.")
    return patient


def require_patient(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    try:
        payload = decode_portal_token(header.split(" ", 1)[1])
        patient = resolve_patient(payload)
    except PortalAuthError as e:
        raise HTTPException(e.status_code, e.message)
    return {"payload": payload, "patient": patient}


def _scope(ctx: dict, scope: str) -> None:
    try:
        require_scope(ctx["payload"], scope)
    except PortalAuthError as e:
        raise HTTPException(e.status_code, e.message)


# --------------------------------------------------------------------------- public

@app.get("/api/public/config")
def public_config():
    from agents.assistant import resolve_engine
    return {
        "app_name": config.APP_NAME, "version": __version__, "env": config.APP_ENV,
        "clinic": {"name": config.CLINIC_NAME, "phone": config.CLINIC_PHONE},
        "demo": {"enabled": config.DEMO_MODE, "access_code": config.STAFF_ACCESS_CODE if config.DEMO_MODE else None},
        "assistant_engine": resolve_engine(),
        "integrations": {"calcom": bool(config.CALCOM_API_KEY), "tavily": bool(config.TAVILY_API_KEY),
                         "portal_webhook": bool(config.PORTAL_WEBHOOK_URL), "sqs": bool(config.AWS_SQS_QUEUE_URL)},
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


# --------------------------------------------------------------------------- staff auth

class SignIn(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=60)


@app.post("/api/auth/staff")
def staff_sign_in(body: SignIn, request: Request):
    client = request.client.host if request.client else "unknown"
    session = staff_auth.sign_in(body.code, body.name, client)
    log_event("staff_sign_in", {"name": session["name"]})
    return session


@app.get("/api/auth/staff/me")
def staff_me(staff: dict = Depends(staff_auth.require_staff)):
    return {"name": staff["sub"], "role": staff["role"], "expires_at": staff["exp"]}


# --------------------------------------------------------------------------- staff: today

@app.get("/api/staff/overview")
def staff_overview(staff: dict = Depends(staff_auth.require_staff)):
    reviews = db.list_provider_review(status="pending")
    appts = db.list_appointments()
    requested = [a for a in appts if a["status"] == "requested"]
    tickets = db.list_support_tickets()
    open_tickets = [t for t in tickets if t["status"] != "Resolved"]
    erx = db.list_erx_queue()
    events = read_events(limit=2000)
    safety = [e for e in events if e.get("event_type") == "safety_screen"]
    patients = db.list_patients()
    auto_rate = round(len(erx) / (len(erx) + len(db.list_provider_review())), 2) if (erx or reviews) else None

    attention = []
    for r in reviews[:6]:
        attention.append({"kind": "review", "id": r["review_id"], "title": f"{r['medication_name']} for {r['full_name']}",
                          "detail": r["reason"], "tone": "warning", "at": r["created_at"]})
    for a in requested[:6]:
        attention.append({"kind": "appointment", "id": a["appointment_id"], "title": f"{a['specialty']} visit for {a['full_name']}",
                          "detail": "Requested through the assistant", "tone": "info", "at": a["created_at"]})
    for t in [t for t in open_tickets if t["status"] == "Unresolved"][:6]:
        attention.append({"kind": "ticket", "id": t["ticket_number"], "title": f"Ticket #{t['ticket_number']}"
                          + (f", {t['patient_name']}" if t.get("patient_name") else ""),
                          "detail": t["complaint_text"][:120], "tone": "destructive", "at": t["created_at"]})
    attention.sort(key=lambda x: x["at"] or "", reverse=True)

    return {
        "counts": {"reviews": len(reviews), "appointment_requests": len(requested), "open_tickets": len(open_tickets),
                   "sent_to_pharmacy": len(erx), "patients": len(patients), "safety_flags": len(safety)},
        "auto_approval_rate": auto_rate,
        "attention": attention[:10],
        "checklist": {
            "calendar_connected": bool(config.CALCOM_API_KEY),
            "portal_connected": bool(config.PORTAL_WEBHOOK_URL),
            "own_access_code": config.STAFF_ACCESS_CODE not in ("", "tendwell-demo"),
            "patients_loaded": len(patients) > 0,
        },
    }


@app.get("/api/staff/activity")
def staff_activity(limit: int = 30, staff: dict = Depends(staff_auth.require_staff)):
    """A plain-language feed. Message text is left out on purpose."""
    labels = {
        "appointment_requested": ("Appointment requested", "info"),
        "safety_screen": ("Safety screen matched", "destructive"),
        "refill_escalated_to_doctor": ("Refill escalated to a provider", "warning"),
        "refill_confirmation_started": ("Refill confirmation started", "info"),
        "feedback_classification": ("Message triaged", "neutral"),
        "pharmacy_notification": ("Sent to pharmacy", "success"),
        "assistant_fallback": ("Assistant switched to concierge", "warning"),
        "staff_sign_in": ("Staff signed in", "neutral"),
        "review_decided": ("Provider decision recorded", "success"),
    }
    out = []
    for e in reversed(read_events(limit=600)):
        t = e.get("event_type")
        if t not in labels:
            continue
        title, tone = labels[t]
        detail = ""
        if t == "safety_screen":
            detail = f"{str(e.get('rule_id', '')).replace('_', ' ')} ({e.get('level', '')})"
        elif t == "feedback_classification":
            detail = str(e.get("category", "")).replace("_", " ")
        elif t == "appointment_requested":
            detail = e.get("specialty", "")
        elif t == "staff_sign_in":
            detail = e.get("name", "")
        elif t == "review_decided":
            detail = e.get("decision", "")
        out.append({"type": t, "title": title, "detail": detail, "tone": tone, "at": e.get("timestamp")})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- staff: patients

class NewPatient(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    age: int = Field(ge=0, le=120)
    sex: Literal["M", "F", "Other"]
    relation_to_user: str = Field(default="self", max_length=40)


class HistoryEntry(BaseModel):
    entry_type: Literal["diagnosis", "treatment", "alert", "note", "visit", "lab_result"]
    content: str = Field(min_length=1, max_length=4000)


@app.get("/api/staff/patients")
def staff_patients(staff: dict = Depends(staff_auth.require_staff)):
    rx_counts = Counter(rx["patient_id"] for rx in db.list_prescriptions())
    pending = Counter(r["patient_id"] for r in db.list_provider_review(status="pending"))
    out = []
    for p in db.list_patients():
        conditions = [e["content"].get("condition") for e in db.get_history(p["patient_id"])
                      if e["entry_type"] == "diagnosis" and isinstance(e["content"], dict) and e["content"].get("condition")]
        row = _staff_patient(p)
        row.update({"conditions": conditions[:3], "prescriptions": rx_counts.get(p["patient_id"], 0),
                    "pending_reviews": pending.get(p["patient_id"], 0)})
        out.append(row)
    return out


@app.post("/api/staff/patients", status_code=201)
def staff_add_patient(body: NewPatient, staff: dict = Depends(staff_auth.require_staff)):
    pid = db.add_patient(body.full_name.strip(), body.age, body.sex, body.relation_to_user)
    return _staff_patient(db.get_patient(pid))


@app.get("/api/staff/patients/{patient_id}")
def staff_patient_detail(patient_id: int, staff: dict = Depends(staff_auth.require_staff)):
    p = _get_patient_or_404(patient_id)
    return {
        "patient": _staff_patient(p),
        "history": db.get_history(patient_id),
        "prescriptions": [_rx_view(rx) for rx in db.list_prescriptions(patient_id)],
        "appointments": db.list_appointments(patient_id),
        "reviews": db.list_provider_review(patient_id=patient_id),
        "erx": db.list_erx_queue(patient_id=patient_id),
    }


@app.post("/api/staff/patients/{patient_id}/history", status_code=201)
def staff_add_history(patient_id: int, body: HistoryEntry, staff: dict = Depends(staff_auth.require_staff)):
    _get_patient_or_404(patient_id)
    entry_id = db.add_history_entry(patient_id, body.entry_type, body.content.strip(), False)
    return {"entry_id": entry_id}


@app.post("/api/staff/patients/{patient_id}/portal-session")
def staff_open_portal(patient_id: int, staff: dict = Depends(staff_auth.require_staff)):
    """Opens the patient experience for a patient — a stand-in for the clinic
    portal's own sign-in. Patients without an external id get one so the
    portal token has a stable subject."""
    p = _get_patient_or_404(patient_id)
    external_id = p.get("external_id")
    if not external_id:
        external_id = f"TW-{patient_id:05d}"
        db.set_external_id(patient_id, external_id)
        if not db.get_patient_by_external_id(external_id):
            raise HTTPException(500, "Couldn't open a portal session for this patient.")
    try:
        token = create_test_token(external_id, scopes=PATIENT_SCOPES, expires_in_seconds=2 * 3600)
    except PortalAuthError as e:
        raise HTTPException(e.status_code, e.message)
    log_event("portal_session_opened", {"patient_id": patient_id, "by": staff["sub"]})
    return {"token": token, "patient": _portal_patient(db.get_patient_by_external_id(external_id))}


# --------------------------------------------------------------------------- staff: schedule

class AppointmentUpdate(BaseModel):
    status: Optional[Literal["requested", "confirmed", "declined", "completed", "cancelled"]] = None
    start_time: Optional[str] = Field(default=None, max_length=40)
    doctor_name: Optional[str] = Field(default=None, max_length=120)


@app.get("/api/staff/appointments")
def staff_appointments(staff: dict = Depends(staff_auth.require_staff)):
    return db.list_appointments()


@app.patch("/api/staff/appointments/{appointment_id}")
def staff_update_appointment(appointment_id: int, body: AppointmentUpdate, staff: dict = Depends(staff_auth.require_staff)):
    if not db.get_appointment(appointment_id):
        raise HTTPException(404, "Appointment not found.")
    if body.status == "confirmed" and not (body.start_time or db.get_appointment(appointment_id)["start_time"]):
        raise HTTPException(422, "Pick a time before confirming.")
    db.update_appointment(appointment_id, status=body.status, start_time=body.start_time, doctor_name=body.doctor_name)
    log_event("appointment_updated", {"appointment_id": appointment_id, "status": body.status})
    return db.get_appointment(appointment_id)


# --------------------------------------------------------------------------- staff: refills

class StaffRefill(BaseModel):
    patient_id: int
    prescription_id: int
    date_of_birth: str = Field(max_length=10)
    pharmacy_id: str = Field(default="CVS-HORSHAM", max_length=60)
    address: Optional[str] = Field(default=None, max_length=200)
    ssn_last4: Optional[str] = Field(default=None, pattern=r"^\d{4}$")


class ReviewDecision(BaseModel):
    decision: Literal["approved", "denied"]
    note: Optional[str] = Field(default=None, max_length=500)


@app.get("/api/staff/refills")
def staff_refills(staff: dict = Depends(staff_auth.require_staff)):
    return {
        "reviews": db.list_provider_review(),
        "erx": db.list_erx_queue(),
        "prescriptions": [_rx_view(rx) for rx in db.list_prescriptions()],
    }


@app.post("/api/staff/refills")
def staff_submit_refill(body: StaffRefill, staff: dict = Depends(staff_auth.require_staff)):
    """Phone or front-desk refill: identity is re-verified (DOB, plus any
    extra factors given) and the full rule engine runs."""
    from workflows.prescription_refill import process_refill_request
    _get_patient_or_404(body.patient_id)
    return process_refill_request(body.patient_id, body.date_of_birth, body.prescription_id, body.pharmacy_id,
                                  address=body.address or None, ssn_last4=body.ssn_last4 or None)


@app.post("/api/staff/reviews/{review_id}/decision")
def staff_decide_review(review_id: int, body: ReviewDecision, staff: dict = Depends(staff_auth.require_staff)):
    review = next((r for r in db.list_provider_review() if r["review_id"] == review_id), None)
    if not review:
        raise HTTPException(404, "Review not found.")
    if review["status"] != "pending":
        raise HTTPException(409, f"This review was already {review['status']}.")
    db.update_review_status(review_id, body.decision)
    result = {"review_id": review_id, "status": body.decision}
    if body.decision == "approved":
        from workflows.prescription_refill import golden_path_queue_injection
        prescription = db.get_prescription(review["prescription_id"])
        result["queue"] = golden_path_queue_injection(prescription, pharmacy_id="PROVIDER-APPROVED")
    log_event("review_decided", {"review_id": review_id, "decision": body.decision, "by": staff["sub"]})
    return result


# --------------------------------------------------------------------------- staff: inbox

class InboxMessage(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    patient_name: Optional[str] = Field(default=None, max_length=120)
    medication_name: Optional[str] = Field(default=None, max_length=120)
    active_ticket: Optional[str] = Field(default=None, pattern=r"^\d{6}$")
    corrected_patient_name: Optional[str] = Field(default=None, max_length=120)


class TicketUpdate(BaseModel):
    status: Literal["Unresolved", "In Progress", "Resolved"]


@app.get("/api/staff/inbox")
def staff_inbox(staff: dict = Depends(staff_auth.require_staff)):
    return {"tickets": db.list_support_tickets(), "confirmations": db.list_refill_confirmations()}


@app.post("/api/staff/inbox/messages")
def staff_inbox_message(body: InboxMessage, staff: dict = Depends(staff_auth.require_staff)):
    """Run an incoming patient message (SMS, email, web form) through triage."""
    from agents.feedback_crew import run_feedback_workflow
    return run_feedback_workflow(
        body.message, patient_name=body.patient_name, active_ticket_number=body.active_ticket,
        corrected_patient_name=body.corrected_patient_name, corrected_medication_name=body.medication_name)


@app.patch("/api/staff/tickets/{ticket_number}")
def staff_update_ticket(ticket_number: str, body: TicketUpdate, staff: dict = Depends(staff_auth.require_staff)):
    if not db.update_ticket_status(ticket_number, body.status):
        raise HTTPException(404, "Ticket not found.")
    return db.get_support_ticket(ticket_number)


# --------------------------------------------------------------------------- staff: insights

@app.get("/api/staff/insights")
def staff_insights(staff: dict = Depends(staff_auth.require_staff)):
    from monitoring.monitor import summarize_latency, throughput_over_time
    from evaluation.eval_harness import summarize_metrics
    events = read_events(limit=5000)
    by_type = Counter(e.get("event_type") for e in events)
    engines = Counter(e.get("engine", "crew") for e in events if e.get("event_type") == "response")
    safety = Counter(e.get("rule_id") for e in events if e.get("event_type") == "safety_screen")
    categories = Counter(e.get("category") for e in events if e.get("event_type") == "feedback_classification")
    reviews = db.list_provider_review()
    erx = db.list_erx_queue()
    return {
        "latency": summarize_latency(),
        "throughput": throughput_over_time(bucket_minutes=60)[-48:],
        "quality": summarize_metrics(),
        "events": dict(by_type),
        "engines": dict(engines),
        "safety": dict(safety),
        "triage": dict(categories),
        "refills": {"auto": len(erx), "review": len(reviews),
                    "reasons": dict(Counter(r["reason"] for r in reviews))},
    }


@app.post("/api/staff/insights/quality-check")
def staff_quality_check(staff: dict = Depends(staff_auth.require_staff)):
    """Runs the labelled triage suite (deterministic, no network) in a child
    process against a throwaway database, so test tickets never land in the
    clinic's real inbox."""
    import json as _json
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="tendwell_eval_") as tmp:
        env = {**os.environ, "PATIENT_DB_PATH": os.path.join(tmp, "eval.db"), "LOGS_DIR": tmp,
               "DEMO_MODE": "false"}
        code = ("import json; from data import patient_db as db; db.init_db(seed=False); "
                "from evaluation.feedback_evaluator import run_feedback_eval_suite, summarize_feedback_eval; "
                "r = run_feedback_eval_suite(); print(json.dumps({'summary': summarize_feedback_eval(r), 'cases': r}, default=str))")
        proc = subprocess.run([sys.executable, "-c", code], cwd=config.BASE_DIR, env=env,
                              capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise HTTPException(500, "The quality check couldn't run.")
    return _json.loads(proc.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- patient portal

class AssistantMessage(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class PatientRefill(BaseModel):
    prescription_id: int
    pharmacy_id: str = Field(default="CVS-HORSHAM", max_length=60)


class PatientSupport(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    active_ticket: Optional[str] = Field(default=None, pattern=r"^\d{6}$")
    medication_name: Optional[str] = Field(default=None, max_length=120)


@app.get("/api/me")
def me(ctx: dict = Depends(require_patient)):
    p = ctx["patient"]
    upcoming = [a for a in db.list_appointments(p["patient_id"]) if a["status"] in ("requested", "confirmed")]
    return {
        "patient": _portal_patient(p),
        "prescriptions": [_rx_view(rx) for rx in db.list_prescriptions(p["patient_id"])],
        "appointments": [{k: a[k] for k in ("appointment_id", "doctor_name", "specialty", "start_time", "status")}
                         for a in db.list_appointments(p["patient_id"])],
        "upcoming": len(upcoming),
        "disclaimer": DISCLAIMER,
        "clinic": {"name": config.CLINIC_NAME, "phone": config.CLINIC_PHONE},
    }


@app.post("/api/me/assistant")
def me_assistant(body: AssistantMessage, ctx: dict = Depends(require_patient)):
    _scope(ctx, "assistant:use")
    from agents.assistant import answer
    started = time.perf_counter()
    result = answer(body.message, ctx["patient"]["patient_id"])
    return {
        "reply": result.get("final_response") or "",
        "cards": result.get("cards", []), "actions": result.get("actions", []),
        "safety": result.get("safety"), "engine": result.get("engine"),
        "plan": result.get("plan", {}),
        "trace": [{"tool": t.get("tool"), "status": t.get("status")} for t in result.get("tool_trace", [])],
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


@app.post("/api/me/refills")
def me_refill(body: PatientRefill, ctx: dict = Depends(require_patient)):
    _scope(ctx, "refills:write")
    from workflows.prescription_refill import process_refill_request_authenticated
    patient = ctx["patient"]
    rx = db.get_prescription(body.prescription_id)
    if not rx or rx["patient_id"] != patient["patient_id"]:
        raise HTTPException(404, "Prescription not found.")
    result = process_refill_request_authenticated(patient["patient_id"], body.prescription_id, body.pharmacy_id)
    friendly = {
        "processed": "Your refill is on its way to the pharmacy.",
        "routed_to_review": "Your provider needs to take a quick look first. We'll let you know when it's approved.",
    }
    return {"status": result["status"], "message": friendly.get(result["status"], result.get("message", "")),
            "confirmation_id": result.get("confirmation_id"), "reason": result.get("reason")}


@app.post("/api/me/support")
def me_support(body: PatientSupport, ctx: dict = Depends(require_patient)):
    from agents.feedback_crew import run_feedback_workflow
    result = run_feedback_workflow(body.message, patient_name=ctx["patient"]["full_name"],
                                   active_ticket_number=body.active_ticket,
                                   corrected_medication_name=body.medication_name)
    return {"message": result.get("message"), "category": result.get("category"),
            "ticket_number": result.get("ticket_number"), "stage": result.get("stage"),
            "chained": result.get("chained_refill_agent")}


# --------------------------------------------------------------------------- errors + web app

@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    ref = uuid.uuid4().hex[:8]
    log_event("api_error", {"path": request.url.path, "error": type(exc).__name__, "ref": ref})
    return JSONResponse(status_code=500, content={"detail": f"Something went wrong on our side (ref {ref})."})


if os.path.isdir(config.WEB_DIST_DIR):
    assets_dir = os.path.join(config.WEB_DIST_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "Not found.")
        candidate = os.path.normpath(os.path.join(config.WEB_DIST_DIR, full_path))
        if full_path and candidate.startswith(config.WEB_DIST_DIR) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(config.WEB_DIST_DIR, "index.html"))
