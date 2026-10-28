"""
JSONL logging for agent traces (planning breakdowns + tool usage) so the
Streamlit "Memory and Logs" tab can display them, and so evaluation can
compute per-module success rates.

Lives in monitoring/ (not agents/) because workflows/ and tools/ need to
log events too, and having them import from agents/ purely for this
logging utility created a backwards package dependency (business-logic
layers depending on the orchestration layer) -- monitoring/ is already
the designated home for cross-cutting observability code (see
monitoring/monitor.py), so this belongs alongside it.
"""

import json
import datetime as dt
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import AGENT_LOG_PATH


# Free text a patient or a model wrote (and tool arguments/results built from
# chart data). Logs keep its length, never its content.
TEXT_KEYS = {"user_query", "user_message", "final_response", "response", "message", "complaint",
             "query", "args", "result", "raw_llm_output", "content", "notes"}


def scrub(payload: dict) -> dict:
    return {k: ({"chars": len(json.dumps(v, default=str))} if k in TEXT_KEYS and v is not None else v)
            for k, v in payload.items()}


def log_event(event_type: str, payload: dict):
    """event_type: 'plan' | 'tool_call' | 'response' | 'error'. Message text is
    never written: see TEXT_KEYS."""
    record = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "event_type": event_type,
        **scrub(payload),
    }
    with open(AGENT_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_events(limit: int = 200) -> list[dict]:
    if not os.path.exists(AGENT_LOG_PATH):
        return []
    with open(AGENT_LOG_PATH, "r") as f:
        lines = f.readlines()[-limit:]
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
