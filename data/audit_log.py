"""
Append-only, hash-chained audit trail.

Every row stores the hash of the row before it, so editing or removing any
entry breaks every hash after it and `verify_chain()` reports where. SQLite
triggers refuse UPDATE and DELETE, so the application can't rewrite history
by accident; someone with file access can, which the chain then exposes.
Ship the head hash somewhere outside this database (a log drain, a ticket)
on a schedule for protection against that.

Rows hold ids, actions and outcomes only — never message text or chart
content — so the trail itself is not a second copy of the chart.
"""

import datetime as dt
import hashlib
import json
import os
import sys
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import patient_db as db

GENESIS = "0" * 64
_FIELDS = ("seq", "ts", "actor", "actor_role", "action", "resource", "patient_id", "outcome", "detail")
_DETAIL_TYPES = (str, int, float, bool, type(None))


def _digest(prev_hash: str, row: dict) -> str:
    body = json.dumps([row[f] for f in _FIELDS], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(f"{prev_hash}|{body}".encode()).hexdigest()


def record(actor: str, actor_role: str, action: str, *, resource: Optional[str] = None,
           patient_id: Optional[int] = None, outcome: str = "ok", detail: Optional[dict] = None) -> int:
    """Append one entry and return its sequence number. Raises if it can't be
    written: callers on the PHI path should fail the request rather than serve
    data that wasn't logged."""
    clean = {k: (v if isinstance(v, _DETAIL_TYPES) else str(type(v).__name__))
             for k, v in (detail or {}).items()}
    for k, v in clean.items():
        if isinstance(v, str) and len(v) > 80:
            clean[k] = v[:80]
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")  # one writer at a time, so the chain can't fork
        last = conn.execute("SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        row = {
            "seq": (last["seq"] + 1) if last else 1,
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "actor": actor[:80], "actor_role": actor_role, "action": action[:120],
            "resource": resource[:120] if resource else None, "patient_id": patient_id,
            "outcome": outcome, "detail": json.dumps(clean, sort_keys=True) if clean else None,
        }
        prev = last["hash"] if last else GENESIS
        row["prev_hash"], row["hash"] = prev, _digest(prev, row)
        conn.execute(
            "INSERT INTO audit_log (seq, ts, actor, actor_role, action, resource, patient_id, outcome, detail, "
            "prev_hash, hash) VALUES (:seq, :ts, :actor, :actor_role, :action, :resource, :patient_id, :outcome, "
            ":detail, :prev_hash, :hash)", row)
        return row["seq"]


def verify_chain() -> dict:
    """Walk the whole chain. {ok, entries, head, broken_at} — broken_at is the
    first sequence number whose link or hash doesn't check out."""
    prev, count, expected_seq = GENESIS, 0, 1
    with db.get_conn() as conn:
        for r in conn.execute("SELECT * FROM audit_log ORDER BY seq"):
            row = dict(r)
            if row["seq"] != expected_seq or row["prev_hash"] != prev or row["hash"] != _digest(prev, row):
                return {"ok": False, "entries": count, "head": prev, "broken_at": row["seq"]}
            prev, count, expected_seq = row["hash"], count + 1, expected_seq + 1
    return {"ok": True, "entries": count, "head": prev, "broken_at": None}


def list_entries(limit: int = 100, patient_id: Optional[int] = None, before_seq: Optional[int] = None) -> list[dict]:
    clauses, params = [], []
    if patient_id is not None:
        clauses.append("patient_id = ?"); params.append(patient_id)
    if before_seq is not None:
        clauses.append("seq < ?"); params.append(before_seq)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.get_conn() as conn:
        rows = conn.execute(f"SELECT * FROM audit_log {where} ORDER BY seq DESC LIMIT ?", (*params, min(limit, 500))).fetchall()
    return [dict(r) for r in rows]


_UNLOGGED = {"/api/auth/staff/me"}  # session check on every page load, no data returned


def record_request(request, actor: str, role: str, patient_id: Optional[int] = None) -> Optional[int]:
    """Log an authenticated API call *before* its handler runs, so a read can't
    happen unlogged. Works on any request-like object (scope route + path params)."""
    path = request.url.path
    if path in _UNLOGGED:
        return None
    route = request.scope.get("route")
    template = getattr(route, "path", path)
    params = dict(request.path_params or {})
    if patient_id is None and "patient_id" in params:
        try:
            patient_id = int(params["patient_id"])
        except (TypeError, ValueError):
            pass
    resource = ",".join(f"{k}={v}" for k, v in sorted(params.items())) or None
    return record(actor, role, f"{request.method} {template}", resource=resource, patient_id=patient_id,
                  outcome="attempt")
