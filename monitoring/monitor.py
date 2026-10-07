"""
Monitoring layer (Part 2, "speed" requirement).

Tracks operational metrics — latency, throughput, error rate — for every
layer of the agent pipeline: the planner, the tool-calling executor, and
each individual tool (Cal.com, EHR, medical search). This is deliberately
separate from evaluation/eval_harness.py: monitoring answers "how fast and
how reliably is the system running right now", while evaluation answers
"how good/correct are its answers". The Streamlit "Monitoring" tab reads
from here; the "Evaluation" tab reads from eval_harness.

Usage:
    from monitoring.monitor import track

    with track("calcom.book_appointment") as status:
        try:
            ...
        except SomeError:
            status["success"] = False
"""

import os
import sys
import json
import time
import datetime as dt
from contextlib import contextmanager
from statistics import mean, median

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MONITORING_LOG_PATH


def log_metric(module: str, latency_ms: float, success: bool, meta: dict = None):
    record = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "module": module,
        "latency_ms": round(latency_ms, 2),
        "success": bool(success),
        **(meta or {}),
    }
    with open(MONITORING_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


@contextmanager
def track(module: str, meta: dict = None):
    """Context manager: times the wrapped block and logs a monitoring event.
    Yields a mutable `status` dict — set status["success"] = False inside
    the block if the operation logically failed without raising (e.g. an
    API call that returned an error string instead of throwing)."""
    start = time.perf_counter()
    status = {"success": True}
    try:
        yield status
    except Exception:
        status["success"] = False
        raise
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        log_metric(module, elapsed_ms, status["success"], meta)


def load_metrics(limit: int = 2000) -> list[dict]:
    if not os.path.exists(MONITORING_LOG_PATH):
        return []
    with open(MONITORING_LOG_PATH, "r") as f:
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


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize_latency(limit: int = 2000) -> dict:
    """Per-module latency + success-rate summary: count, avg/median/p95 ms,
    error rate. Used by the Streamlit Monitoring tab."""
    records = load_metrics(limit)
    by_module = {}
    for r in records:
        by_module.setdefault(r["module"], []).append(r)

    summary = {}
    for module, recs in by_module.items():
        latencies = [r["latency_ms"] for r in recs]
        successes = sum(1 for r in recs if r.get("success"))
        summary[module] = {
            "count": len(recs),
            "avg_ms": round(mean(latencies), 1) if latencies else 0,
            "median_ms": round(median(latencies), 1) if latencies else 0,
            "p95_ms": round(_percentile(latencies, 0.95), 1),
            "success_rate": round(successes / len(recs), 2) if recs else 0,
        }
    return summary


def throughput_over_time(limit: int = 2000, bucket_minutes: int = 5) -> list[dict]:
    """Requests per time bucket, for a simple throughput chart."""
    records = load_metrics(limit)
    buckets = {}
    for r in records:
        try:
            ts = dt.datetime.fromisoformat(r["timestamp"])
        except (KeyError, ValueError):
            continue
        bucket_key = ts.replace(
            minute=(ts.minute // bucket_minutes) * bucket_minutes, second=0, microsecond=0
        ).isoformat()
        buckets[bucket_key] = buckets.get(bucket_key, 0) + 1
    return [{"time": k, "requests": v} for k, v in sorted(buckets.items())]
