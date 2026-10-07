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


def log_event(event_type: str, payload: dict):
    """event_type: 'plan' | 'tool_call' | 'response' | 'error'"""
    record = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "event_type": event_type,
        **payload,
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
