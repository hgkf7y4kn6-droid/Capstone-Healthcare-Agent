"""
Lightweight EHR (Electronic Health Record) store backed by SQLite.

Handles:
  - Patient demographics
  - Structured history entries (diagnosis, treatment, alerts)
  - Unstructured notes (free-text attendant entries)

This stands in for a real hospital EHR system. Cal.com (the real API we
integrate with) only knows about bookings/availability — it has no concept
of clinical history — so patient records are modeled here instead.
"""

import sqlite3
import json
import datetime as dt
from contextlib import contextmanager
from typing import Optional

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PATIENT_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name       TEXT NOT NULL,
    age             INTEGER,
    sex             TEXT,
    relation_to_user TEXT,          -- e.g. 'self', 'father', 'mother'
    external_id     TEXT UNIQUE,     -- e.g. 'P001' from an imported source system
    contact         TEXT,
    email           TEXT,
    blood_type      TEXT,
    primary_doctor  TEXT,
    next_appointment TEXT,
    dob             TEXT,            -- ISO date, used for identity verification
    address         TEXT,            -- home address, additional identity factor
    ssn_last4       TEXT,            -- last 4 digits of SSN (or equivalent national ID), additional identity factor
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history_entries (
    entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL,
    entry_type      TEXT NOT NULL,   -- 'diagnosis' | 'treatment' | 'alert' | 'note'
    content         TEXT NOT NULL,   -- structured (JSON string) or free text
    is_structured   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
);

CREATE TABLE IF NOT EXISTS appointments (
    appointment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id       INTEGER NOT NULL,
    doctor_name      TEXT NOT NULL,
    specialty        TEXT,
    calcom_booking_uid TEXT,          -- reference to the real Cal.com booking
    start_time       TEXT,
    end_time         TEXT,
    status           TEXT DEFAULT 'confirmed',
    created_at       TEXT NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
);

CREATE TABLE IF NOT EXISTS prescriptions (
    prescription_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id             INTEGER NOT NULL,
    medication_name        TEXT NOT NULL,
    is_controlled_substance INTEGER NOT NULL DEFAULT 0,
    controlled_schedule    TEXT,             -- e.g. 'CII', 'CIV'
    remaining_refills      INTEGER NOT NULL DEFAULT 0,
    requires_lab_check     INTEGER NOT NULL DEFAULT 0,
    lab_check_type         TEXT,             -- e.g. 'potassium', 'CRP'
    lab_safe_range         TEXT,             -- e.g. '3.5-5.0', '<10', '>60'
    prescribing_doctor     TEXT,
    status                 TEXT DEFAULT 'active',
    created_at             TEXT NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
);

CREATE TABLE IF NOT EXISTS erx_queue (
    queue_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id    INTEGER NOT NULL,
    patient_id         INTEGER NOT NULL,
    pharmacy_id        TEXT,
    confirmation_id    TEXT,
    status             TEXT DEFAULT 'queued',
    created_at         TEXT NOT NULL,
    FOREIGN KEY (prescription_id) REFERENCES prescriptions(prescription_id),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
);

CREATE TABLE IF NOT EXISTS provider_review_queue (
    review_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id    INTEGER NOT NULL,
    patient_id         INTEGER NOT NULL,
    reason             TEXT NOT NULL,
    context_payload    TEXT NOT NULL,        -- JSON: patient profile, active meds, last visit, failed rule
    status             TEXT DEFAULT 'pending', -- pending | approved | denied
    created_at         TEXT NOT NULL,
    FOREIGN KEY (prescription_id) REFERENCES prescriptions(prescription_id),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
);

CREATE TABLE IF NOT EXISTS refill_tasks (
    tracking_id        TEXT PRIMARY KEY,      -- UUID, returned to the portal immediately
    patient_id         INTEGER NOT NULL,
    prescription_id    INTEGER NOT NULL,
    pharmacy_id        TEXT,
    identity_method    TEXT NOT NULL,          -- 'jwt' (portal handoff) | 'dob' (direct/conversational)
    status              TEXT DEFAULT 'queued',  -- queued|processing|sent_to_pharmacy|pending_provider_review|unauthorized|error
    result_payload      TEXT,                   -- JSON result once processed
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (prescription_id) REFERENCES prescriptions(prescription_id),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key    TEXT PRIMARY KEY,
    tracking_id        TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_number    TEXT PRIMARY KEY,       -- unique 6-digit string, e.g. '784521'
    patient_name     TEXT,
    complaint_text   TEXT NOT NULL,
    status           TEXT DEFAULT 'Unresolved',  -- Unresolved | In Progress | Resolved
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refill_confirmations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_number           TEXT NOT NULL,
    claimed_patient_name    TEXT,
    claimed_medication_name TEXT,
    stage                   TEXT DEFAULT 'awaiting_confirmation',
        -- awaiting_confirmation | awaiting_correction | verified | escalated
    resolved_patient_id     INTEGER,        -- set once we've matched a real patient record
    resolved_prescription_id INTEGER,       -- set once we've matched a real prescription
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    FOREIGN KEY (ticket_number) REFERENCES support_tickets(ticket_number)
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(PATIENT_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # The API serves concurrent requests on worker threads; wait briefly on a
    # locked database instead of failing the request.
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    """Add any newer columns to existing (older-schema) tables."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(patients)")}
    new_columns = {
        "external_id": "TEXT",
        "contact": "TEXT",
        "email": "TEXT",
        "blood_type": "TEXT",
        "primary_doctor": "TEXT",
        "next_appointment": "TEXT",
        "dob": "TEXT",
        "address": "TEXT",
        "ssn_last4": "TEXT",
    }
    for col, coltype in new_columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE patients ADD COLUMN {col} {coltype}")

    rx_existing = {row["name"] for row in conn.execute("PRAGMA table_info(prescriptions)")}
    rx_new_columns = {
        # ISO date the prescription was written -- needed for the Schedule
        # III/IV "not filled more than 6 months after the written date" rule.
        "written_date": "TEXT",
    }
    for col, coltype in rx_new_columns.items():
        if col not in rx_existing:
            conn.execute(f"ALTER TABLE prescriptions ADD COLUMN {col} {coltype}")


def init_db(seed: bool = True):
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        if seed:
            cur = conn.execute("SELECT COUNT(*) AS c FROM patients")
            if cur.fetchone()["c"] == 0:
                _seed_demo_data(conn)


def _seed_demo_data(conn):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO patients (full_name, age, sex, relation_to_user, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Ramesh Kumar", 70, "M", "father", now),
    )
    patient_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    conn.execute(
        "INSERT INTO history_entries (patient_id, entry_type, content, is_structured, created_at) "
        "VALUES (?, 'diagnosis', ?, 1, ?)",
        (patient_id, json.dumps({"condition": "Chronic Kidney Disease", "stage": "Stage 3", "diagnosed": "2023-04-12"}), now),
    )
    conn.execute(
        "INSERT INTO history_entries (patient_id, entry_type, content, is_structured, created_at) "
        "VALUES (?, 'treatment', ?, 1, ?)",
        (patient_id, json.dumps({"treatment": "ACE inhibitor (Lisinopril)", "started": "2023-04-20"}), now),
    )
    conn.execute(
        "INSERT INTO history_entries (patient_id, entry_type, content, is_structured, created_at) "
        "VALUES (?, 'alert', ?, 1, ?)",
        (patient_id, json.dumps({"alert": "Avoid NSAIDs — risk of further renal impairment"}), now),
    )
    conn.execute(
        "INSERT INTO history_entries (patient_id, entry_type, content, is_structured, created_at) "
        "VALUES (?, 'note', ?, 0, ?)",
        (patient_id, "Patient reports mild fatigue and occasional ankle swelling over the last two weeks.", now),
    )


# ---------- CRUD helpers used by the tools layer ----------

def add_patient(full_name: str, age: int, sex: str, relation_to_user: str = "self") -> int:
    with get_conn() as conn:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO patients (full_name, age, sex, relation_to_user, created_at) VALUES (?, ?, ?, ?, ?)",
            (full_name, age, sex, relation_to_user, now),
        )
        return cur.lastrowid


def upsert_patient(full_name: str, external_id: Optional[str] = None, age: int = None,
                    sex: str = None, relation_to_user: str = "self", contact: str = None,
                    email: str = None, blood_type: str = None, primary_doctor: str = None,
                    next_appointment: str = None, dob: str = None, address: str = None,
                    ssn_last4: str = None) -> int:
    """Insert a patient, or update it in place if external_id already exists.
    Used by the data-import pipeline so re-running an import is idempotent."""
    with get_conn() as conn:
        row = None
        if external_id:
            row = conn.execute(
                "SELECT patient_id FROM patients WHERE external_id = ?", (external_id,)
            ).fetchone()
        if row:
            patient_id = row["patient_id"]
            conn.execute(
                "UPDATE patients SET full_name=?, age=?, sex=?, relation_to_user=?, contact=?, "
                "email=?, blood_type=?, primary_doctor=?, next_appointment=?, dob=COALESCE(?, dob), "
                "address=COALESCE(?, address), ssn_last4=COALESCE(?, ssn_last4) "
                "WHERE patient_id=?",
                (full_name, age, sex, relation_to_user, contact, email, blood_type,
                 primary_doctor, next_appointment, dob, address, ssn_last4, patient_id),
            )
            return patient_id
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO patients (full_name, age, sex, relation_to_user, external_id, contact, "
            "email, blood_type, primary_doctor, next_appointment, dob, address, ssn_last4, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (full_name, age, sex, relation_to_user, external_id, contact, email,
             blood_type, primary_doctor, next_appointment, dob, address, ssn_last4, now),
        )
        return cur.lastrowid


def set_external_id(patient_id: int, external_id: str) -> None:
    """Assign an external id to an existing patient that has none."""
    with get_conn() as conn:
        conn.execute("UPDATE patients SET external_id = ? WHERE patient_id = ? AND external_id IS NULL",
                     (external_id, patient_id))


def get_patient_by_external_id(external_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE external_id = ?", (external_id,)
        ).fetchone()
        return dict(row) if row else None


def find_patient(full_name: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE full_name LIKE ? ORDER BY patient_id DESC LIMIT 1",
            (f"%{full_name}%",),
        ).fetchone()
        return dict(row) if row else None


def list_patients() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM patients ORDER BY patient_id").fetchall()
        return [dict(r) for r in rows]


def add_history_entry(patient_id: int, entry_type: str, content, is_structured: bool = False,
                       created_at: Optional[str] = None, skip_if_duplicate: bool = False) -> Optional[int]:
    """created_at lets imported records keep their real historical timestamp
    instead of the import time. skip_if_duplicate makes repeat imports
    idempotent (matches on patient_id + entry_type + content)."""
    with get_conn() as conn:
        payload = json.dumps(content) if is_structured else str(content)
        if skip_if_duplicate:
            existing = conn.execute(
                "SELECT entry_id FROM history_entries WHERE patient_id=? AND entry_type=? AND content=?",
                (patient_id, entry_type, payload),
            ).fetchone()
            if existing:
                return None
        ts = created_at or dt.datetime.now(dt.timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO history_entries (patient_id, entry_type, content, is_structured, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (patient_id, entry_type, payload, int(is_structured), ts),
        )
        return cur.lastrowid


def get_history(patient_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM history_entries WHERE patient_id = ? ORDER BY created_at",
            (patient_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d["is_structured"]:
                try:
                    d["content"] = json.loads(d["content"])
                except json.JSONDecodeError:
                    pass
            out.append(d)
        return out


def record_appointment(patient_id: int, doctor_name: str, specialty: str,
                        calcom_booking_uid: Optional[str], start_time: str, end_time: str = "",
                        status: str = "confirmed", skip_if_duplicate: bool = False) -> Optional[int]:
    """calcom_booking_uid is None for appointments imported from external
    records that weren't booked through Cal.com by this assistant.
    skip_if_duplicate makes repeat imports idempotent (matches on
    patient_id + doctor_name + start_time)."""
    with get_conn() as conn:
        if skip_if_duplicate:
            existing = conn.execute(
                "SELECT appointment_id FROM appointments WHERE patient_id=? AND doctor_name=? AND start_time=?",
                (patient_id, doctor_name, start_time),
            ).fetchone()
            if existing:
                return None
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO appointments (patient_id, doctor_name, specialty, calcom_booking_uid, "
            "start_time, end_time, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (patient_id, doctor_name, specialty, calcom_booking_uid, start_time, end_time, status, now),
        )
        return cur.lastrowid


def list_appointments(patient_id: Optional[int] = None) -> list[dict]:
    with get_conn() as conn:
        if patient_id:
            rows = conn.execute(
                "SELECT a.*, p.full_name FROM appointments a JOIN patients p ON a.patient_id = p.patient_id "
                "WHERE a.patient_id = ? ORDER BY a.start_time", (patient_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT a.*, p.full_name FROM appointments a JOIN patients p ON a.patient_id = p.patient_id "
                "ORDER BY a.start_time"
            ).fetchall()
        return [dict(r) for r in rows]


def get_appointment(appointment_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT a.*, p.full_name FROM appointments a JOIN patients p ON a.patient_id = p.patient_id "
            "WHERE a.appointment_id = ?", (appointment_id,)
        ).fetchone()
        return dict(row) if row else None


def update_appointment(appointment_id: int, status: Optional[str] = None, start_time: Optional[str] = None,
                       doctor_name: Optional[str] = None) -> bool:
    """Staff confirm/decline/reschedule an appointment (requests made by the
    patient assistant arrive with status 'requested')."""
    fields, values = [], []
    for col, val in (("status", status), ("start_time", start_time), ("doctor_name", doctor_name)):
        if val is not None:
            fields.append(f"{col} = ?")
            values.append(val)
    if not fields:
        return False
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE appointments SET {', '.join(fields)} WHERE appointment_id = ?",
                           (*values, appointment_id))
        return cur.rowcount > 0


def get_patient(patient_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
        return dict(row) if row else None


# ---------- Identity verification (HIPAA-style, multi-factor) ----------

def _normalize_address(addr: str) -> str:
    return " ".join(addr.strip().lower().split())


def verify_patient_identity(patient_id: int, date_of_birth: str, address: Optional[str] = None,
                             ssn_last4: Optional[str] = None) -> bool:
    """Mock identity verification: confirms the supplied DOB (YYYY-MM-DD)
    matches the record on file. DOB is always required and checked; home
    address and last-4-of-SSN are optional additional factors — if the
    caller supplies them, they must ALSO match for verification to pass,
    giving stronger identity assurance for higher-risk operations without
    breaking callers that only collect DOB. This mirrors a real
    patient-portal knowledge-based verification flow but is NOT a
    substitute for a real HIPAA-compliant identity provider in production."""
    return verify_patient_identity_detailed(patient_id, date_of_birth, address, ssn_last4)["verified"]


def verify_patient_identity_detailed(patient_id: int, date_of_birth: str, address: Optional[str] = None,
                                      ssn_last4: Optional[str] = None) -> dict:
    """Same check as verify_patient_identity, but returns which specific
    factors were checked and which matched (e.g. 'DOB matched, address did
    not') instead of a bare bool — used for audit logging so a failure
    reason is recorded, not just a pass/fail."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT dob, address, ssn_last4 FROM patients WHERE patient_id = ?", (patient_id,)
        ).fetchone()

    factors = {}
    if not row:
        factors["dob"] = False
        if address is not None:
            factors["address"] = False
        if ssn_last4 is not None:
            factors["ssn_last4"] = False
        return {"verified": False, "factors": factors}

    factors["dob"] = bool(row["dob"]) and row["dob"].strip() == (date_of_birth or "").strip()

    if address is not None:
        on_file = _normalize_address(row["address"]) if row["address"] else ""
        factors["address"] = bool(on_file) and on_file == _normalize_address(address)

    if ssn_last4 is not None:
        on_file = (row["ssn_last4"] or "").strip()
        factors["ssn_last4"] = bool(on_file) and on_file == ssn_last4.strip()

    return {"verified": all(factors.values()), "factors": factors}


# ---------- Prescriptions ----------

def add_prescription(patient_id: int, medication_name: str, is_controlled_substance: bool = False,
                      controlled_schedule: Optional[str] = None, remaining_refills: int = 0,
                      requires_lab_check: bool = False, lab_check_type: Optional[str] = None,
                      lab_safe_range: Optional[str] = None, prescribing_doctor: str = "",
                      status: str = "active", written_date: Optional[str] = None,
                      skip_if_duplicate: bool = False) -> Optional[int]:
    with get_conn() as conn:
        if skip_if_duplicate:
            existing = conn.execute(
                "SELECT prescription_id FROM prescriptions WHERE patient_id=? AND medication_name=?",
                (patient_id, medication_name),
            ).fetchone()
            if existing:
                return None
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO prescriptions (patient_id, medication_name, is_controlled_substance, "
            "controlled_schedule, remaining_refills, requires_lab_check, lab_check_type, "
            "lab_safe_range, prescribing_doctor, status, written_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (patient_id, medication_name, int(is_controlled_substance), controlled_schedule,
             remaining_refills, int(requires_lab_check), lab_check_type, lab_safe_range,
             prescribing_doctor, status, written_date, now),
        )
        return cur.lastrowid


def get_prescription(prescription_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM prescriptions WHERE prescription_id = ?", (prescription_id,)
        ).fetchone()
        return dict(row) if row else None


def list_prescriptions(patient_id: Optional[int] = None) -> list[dict]:
    with get_conn() as conn:
        if patient_id:
            rows = conn.execute(
                "SELECT rx.*, p.full_name FROM prescriptions rx JOIN patients p ON rx.patient_id = p.patient_id "
                "WHERE rx.patient_id = ? ORDER BY rx.created_at", (patient_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT rx.*, p.full_name FROM prescriptions rx JOIN patients p ON rx.patient_id = p.patient_id "
                "ORDER BY rx.created_at"
            ).fetchall()
        return [dict(r) for r in rows]


def decrement_refill(prescription_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE prescriptions SET remaining_refills = MAX(remaining_refills - 1, 0) "
            "WHERE prescription_id = ?", (prescription_id,)
        )


def get_active_medications(patient_id: int) -> list[dict]:
    """Used for medication reconciliation in the context pre-assembly payload."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM prescriptions WHERE patient_id = ? AND status = 'active' ORDER BY medication_name",
            (patient_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_visit_date(patient_id: int) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT created_at FROM history_entries WHERE patient_id = ? AND entry_type = 'visit' "
            "ORDER BY created_at DESC LIMIT 1", (patient_id,)
        ).fetchone()
        return row["created_at"] if row else None


def get_latest_structured_lab_value(patient_id: int, lab_key: str) -> Optional[dict]:
    """Searches structured lab_result history entries for a key matching
    lab_key (case-insensitive substring), returning the most recent match
    with its raw string value and the entry's date."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT content, created_at FROM history_entries "
            "WHERE patient_id = ? AND entry_type = 'lab_result' AND is_structured = 1 "
            "ORDER BY created_at DESC", (patient_id,),
        ).fetchall()
        for row in rows:
            try:
                content = json.loads(row["content"])
            except json.JSONDecodeError:
                continue
            results = content.get("results", content) if isinstance(content, dict) else {}
            for k, v in results.items():
                if lab_key.lower() in k.lower():
                    return {"key": k, "raw_value": v, "as_of": content.get("as_of", row["created_at"])}
        return None


# ---------- eRx queue (golden path) ----------

def add_erx_queue_entry(prescription_id: int, patient_id: int, pharmacy_id: str,
                         confirmation_id: str, status: str = "queued") -> int:
    with get_conn() as conn:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO erx_queue (prescription_id, patient_id, pharmacy_id, confirmation_id, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (prescription_id, patient_id, pharmacy_id, confirmation_id, status, now),
        )
        return cur.lastrowid


def list_erx_queue(patient_id: Optional[int] = None) -> list[dict]:
    with get_conn() as conn:
        if patient_id:
            rows = conn.execute(
                "SELECT q.*, p.full_name, rx.medication_name FROM erx_queue q "
                "JOIN patients p ON q.patient_id = p.patient_id "
                "JOIN prescriptions rx ON q.prescription_id = rx.prescription_id "
                "WHERE q.patient_id = ? ORDER BY q.created_at DESC", (patient_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT q.*, p.full_name, rx.medication_name FROM erx_queue q "
                "JOIN patients p ON q.patient_id = p.patient_id "
                "JOIN prescriptions rx ON q.prescription_id = rx.prescription_id "
                "ORDER BY q.created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


# ---------- Provider review queue (flagged/failed path) ----------

def add_provider_review(prescription_id: int, patient_id: int, reason: str,
                         context_payload: dict, status: str = "pending") -> int:
    with get_conn() as conn:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO provider_review_queue (prescription_id, patient_id, reason, "
            "context_payload, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (prescription_id, patient_id, reason, json.dumps(context_payload), status, now),
        )
        return cur.lastrowid


def list_provider_review(patient_id: Optional[int] = None, status: Optional[str] = None) -> list[dict]:
    with get_conn() as conn:
        query = (
            "SELECT r.*, p.full_name, rx.medication_name FROM provider_review_queue r "
            "JOIN patients p ON r.patient_id = p.patient_id "
            "JOIN prescriptions rx ON r.prescription_id = rx.prescription_id WHERE 1=1"
        )
        params = []
        if patient_id:
            query += " AND r.patient_id = ?"
            params.append(patient_id)
        if status:
            query += " AND r.status = ?"
            params.append(status)
        query += " ORDER BY r.created_at DESC"
        rows = conn.execute(query, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["context_payload"] = json.loads(d["context_payload"])
            except json.JSONDecodeError:
                pass
            out.append(d)
        return out


def update_review_status(review_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE provider_review_queue SET status = ? WHERE review_id = ?", (status, review_id))


# ---------- Refill task tracking (portal async integration) ----------

def create_refill_task(tracking_id: str, patient_id: int, prescription_id: int, pharmacy_id: str,
                        identity_method: str, status: str = "queued") -> None:
    with get_conn() as conn:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO refill_tasks (tracking_id, patient_id, prescription_id, pharmacy_id, "
            "identity_method, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tracking_id, patient_id, prescription_id, pharmacy_id, identity_method, status, now, now),
        )


def update_refill_task_status(tracking_id: str, status: str, result_payload: Optional[dict] = None) -> None:
    with get_conn() as conn:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        if result_payload is not None:
            conn.execute(
                "UPDATE refill_tasks SET status = ?, result_payload = ?, updated_at = ? WHERE tracking_id = ?",
                (status, json.dumps(result_payload), now, tracking_id),
            )
        else:
            conn.execute(
                "UPDATE refill_tasks SET status = ?, updated_at = ? WHERE tracking_id = ?",
                (status, now, tracking_id),
            )


def get_refill_task(tracking_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM refill_tasks WHERE tracking_id = ?", (tracking_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("result_payload"):
            try:
                d["result_payload"] = json.loads(d["result_payload"])
            except json.JSONDecodeError:
                pass
        return d





# ---------- Idempotency keys ----------

def get_tracking_id_for_idempotency_key(key: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT tracking_id FROM idempotency_keys WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return row["tracking_id"] if row else None


def record_idempotency_key(key: str, tracking_id: str) -> None:
    with get_conn() as conn:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys (idempotency_key, tracking_id, created_at) VALUES (?, ?, ?)",
            (key, tracking_id, now),
        )


# ---------- Support tickets (feedback/query triage workflow) ----------

def _generate_unique_ticket_number(conn) -> str:
    """6-digit ticket number, regenerated on the rare collision. Not
    cryptographically secure — these are display/tracking IDs, not secrets."""
    import random
    for _ in range(20):
        candidate = f"{random.randint(0, 999999):06d}"
        existing = conn.execute(
            "SELECT 1 FROM support_tickets WHERE ticket_number = ?", (candidate,)
        ).fetchone()
        if not existing:
            return candidate
    raise RuntimeError("Could not generate a unique 6-digit ticket number after 20 attempts.")


def create_support_ticket(complaint_text: str, patient_name: Optional[str] = None) -> str:
    """Creates a new ticket with status 'Unresolved' and returns its
    6-digit ticket number."""
    with get_conn() as conn:
        ticket_number = _generate_unique_ticket_number(conn)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO support_tickets (ticket_number, patient_name, complaint_text, status, "
            "created_at, updated_at) VALUES (?, ?, ?, 'Unresolved', ?, ?)",
            (ticket_number, patient_name, complaint_text, now, now),
        )
        return ticket_number


def get_support_ticket(ticket_number: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM support_tickets WHERE ticket_number = ?", (ticket_number.strip(),)
        ).fetchone()
        return dict(row) if row else None


def update_ticket_status(ticket_number: str, status: str) -> bool:
    """Returns True if a ticket was actually updated, False if the ticket
    number doesn't exist."""
    with get_conn() as conn:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cur = conn.execute(
            "UPDATE support_tickets SET status = ?, updated_at = ? WHERE ticket_number = ?",
            (status, now, ticket_number.strip()),
        )
        return cur.rowcount > 0


def list_support_tickets(status: Optional[str] = None) -> list[dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM support_tickets WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM support_tickets ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


# ---------- Refill confirmation sessions (multi-turn prescription-refill agent) ----------

def create_refill_confirmation(ticket_number: str, claimed_patient_name: Optional[str],
                                claimed_medication_name: Optional[str]) -> int:
    with get_conn() as conn:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO refill_confirmations (ticket_number, claimed_patient_name, "
            "claimed_medication_name, stage, created_at, updated_at) "
            "VALUES (?, ?, ?, 'awaiting_confirmation', ?, ?)",
            (ticket_number, claimed_patient_name, claimed_medication_name, now, now),
        )
        return cur.lastrowid


def get_latest_refill_confirmation(ticket_number: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM refill_confirmations WHERE ticket_number = ? ORDER BY id DESC LIMIT 1",
            (ticket_number,),
        ).fetchone()
        return dict(row) if row else None


def list_refill_confirmations(stage: Optional[str] = None) -> list[dict]:
    """Lists the most recent confirmation session per ticket (not every
    historical row), optionally filtered by stage. Used by the Streamlit
    Support Tickets view to show active/open confirmation sessions."""
    with get_conn() as conn:
        query = (
            "SELECT rc.* FROM refill_confirmations rc "
            "INNER JOIN (SELECT ticket_number, MAX(id) AS max_id FROM refill_confirmations "
            "GROUP BY ticket_number) latest "
            "ON rc.ticket_number = latest.ticket_number AND rc.id = latest.max_id"
        )
        params = []
        if stage:
            query += " WHERE rc.stage = ?"
            params.append(stage)
        query += " ORDER BY rc.updated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_refill_confirmation(confirmation_id: int, stage: Optional[str] = None,
                                claimed_patient_name: Optional[str] = None,
                                claimed_medication_name: Optional[str] = None,
                                resolved_patient_id: Optional[int] = None,
                                resolved_prescription_id: Optional[int] = None) -> None:
    """Only overwrites fields explicitly provided (non-None) plus stage."""
    with get_conn() as conn:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        fields, values = [], []
        if stage is not None:
            fields.append("stage = ?"); values.append(stage)
        if claimed_patient_name is not None:
            fields.append("claimed_patient_name = ?"); values.append(claimed_patient_name)
        if claimed_medication_name is not None:
            fields.append("claimed_medication_name = ?"); values.append(claimed_medication_name)
        if resolved_patient_id is not None:
            fields.append("resolved_patient_id = ?"); values.append(resolved_patient_id)
        if resolved_prescription_id is not None:
            fields.append("resolved_prescription_id = ?"); values.append(resolved_prescription_id)
        fields.append("updated_at = ?"); values.append(now)
        values.append(confirmation_id)
        conn.execute(f"UPDATE refill_confirmations SET {', '.join(fields)} WHERE id = ?", values)


def find_prescription_by_patient_and_medication(patient_name: str, medication_name: str) -> Optional[dict]:
    """Fuzzy-ish match: patient full_name and prescription medication_name
    both matched case-insensitively as substrings. Returns the prescription
    row (with patient_id) if found, else None. Used to verify a claimed
    ticket-to-patient-to-medication mapping against real records."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT rx.*, p.full_name FROM prescriptions rx JOIN patients p ON rx.patient_id = p.patient_id "
            "WHERE p.full_name LIKE ? AND rx.medication_name LIKE ?",
            (f"%{patient_name.strip()}%", f"%{medication_name.strip()}%"),
        ).fetchall()
        return dict(rows[0]) if rows else None


if __name__ == "__main__":
    init_db(seed=True)
    print("Initialized patient DB at", PATIENT_DB_PATH)
    print(list_patients())
