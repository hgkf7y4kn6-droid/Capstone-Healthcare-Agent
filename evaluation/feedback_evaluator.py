"""
Feedback/support-ticket triage evaluator (Part 2, step 5: classification
accuracy + routing success rate).

Runs the test cases in evaluation/feedback_eval_dataset.py through the real
run_feedback_workflow() pipeline (agents/feedback_crew.py) -- using the LLM
classifier when available, the heuristic fallback otherwise -- and reports:
  - classification accuracy: did the predicted category match expected?
  - routing success: did the downstream handler produce the right kind of
    response for that category (a ticket number for negative feedback, a
    non-empty thank-you for positive, a status line for a resolvable query)?
"""

import os
import sys
import re
import time
import datetime as dt

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evaluation.feedback_eval_dataset import FEEDBACK_TEST_CASES
from workflows.feedback_support import POSITIVE_FEEDBACK, NEGATIVE_FEEDBACK, QUERY, PRESCRIPTION_REFILL
from monitoring.monitor import track


def _routing_succeeded(category: str, message: str, requires_ticket_number) -> bool:
    """Checks the response contains the right FACTS for that category --
    not exact template wording. The agent is instructed to compose a
    genuine, individualized reply (see agents/prompts.py), so this
    deliberately tolerates paraphrasing and only requires the
    non-negotiable facts (a ticket number, the correct status word,
    empathetic tone) to actually be present."""
    lowered = message.lower()
    if category == POSITIVE_FEEDBACK:
        gratitude_words = ("thank", "grateful", "appreciate", "glad", "delighted", "happy to")
        return any(w in lowered for w in gratitude_words)
    if category == NEGATIVE_FEEDBACK:
        has_ticket_number = bool(re.search(r"\b\d{6}\b", message))
        empathy_words = ("sorry", "apolog", "understand", "inconvenience")
        return has_ticket_number and any(w in lowered for w in empathy_words)
    if category == QUERY:
        if requires_ticket_number:
            has_number = requires_ticket_number in message
            # status is a specific factual value (Unresolved/In Progress/Resolved) --
            # tolerate paraphrased surrounding text, but the actual status word
            # must appear verbatim somewhere in the reply.
            has_status_word = any(s.lower() in lowered for s in ("unresolved", "in progress", "resolved"))
            return has_number and has_status_word
        # A query with no ticket number in the message should get a
        # clarification asking for one, not a fabricated status.
        return "ticket number" in message.lower()
    if category == PRESCRIPTION_REFILL:
        # The eval harness doesn't supply a patient_name, so the workflow
        # should ask for the patient's name/medication rather than
        # fabricating a ticket -- see run_feedback_workflow()'s
        # PRESCRIPTION_REFILL branch.
        return "patient" in lowered and ("medication" in lowered or "refill" in lowered)
    return False


def run_feedback_eval_suite(limit: int = None) -> list[dict]:
    from agents.feedback_crew import run_feedback_workflow
    from data import patient_db as db

    cases = FEEDBACK_TEST_CASES[:limit] if limit else FEEDBACK_TEST_CASES
    results = []

    for case in cases:
        # Queries need a real ticket to look up -- seed one with the exact
        # number the test case expects, so the query path can actually
        # succeed rather than always hitting "ticket not found".
        ticket_number = case.get("requires_ticket_number")
        if ticket_number:
            with db.get_conn() as conn:
                existing = conn.execute(
                    "SELECT 1 FROM support_tickets WHERE ticket_number = ?", (ticket_number,)
                ).fetchone()
                if not existing:
                    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
                    conn.execute(
                        "INSERT INTO support_tickets (ticket_number, patient_name, complaint_text, "
                        "status, created_at, updated_at) VALUES (?, NULL, 'seeded for eval', "
                        "'Unresolved', ?, ?)",
                        (ticket_number, now_iso, now_iso),
                    )

        start = time.perf_counter()
        error = None
        try:
            with track("feedback_eval.case", meta={"case_id": case["id"]}):
                result = run_feedback_workflow(case["message"])
        except Exception as e:
            result = None
            error = str(e)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

        if error:
            results.append({
                "id": case["id"], "expected_category": case["expected_category"],
                "predicted_category": None, "classification_correct": False,
                "routing_succeeded": False, "message": f"[error: {error}]",
                "latency_ms": elapsed_ms, "method": None,
            })
            continue

        classification_correct = result["category"] == case["expected_category"]
        routing_succeeded = _routing_succeeded(
            result["category"], result["message"], case.get("requires_ticket_number")
        )

        results.append({
            "id": case["id"],
            "message": case["message"],
            "expected_category": case["expected_category"],
            "predicted_category": result["category"],
            "classification_correct": classification_correct,
            "routing_succeeded": routing_succeeded,
            "response": result["message"],
            "method": result["classification_method"],
            "latency_ms": elapsed_ms,
        })

    return results


def summarize_feedback_eval(results: list[dict]) -> dict:
    if not results:
        return {"count": 0}
    n = len(results)
    classification_correct = sum(1 for r in results if r["classification_correct"])
    routing_succeeded = sum(1 for r in results if r["routing_succeeded"])
    latencies = [r["latency_ms"] for r in results]
    return {
        "count": n,
        "classification_accuracy": round(classification_correct / n, 2),
        "routing_success_rate": round(routing_succeeded / n, 2),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1),
    }


if __name__ == "__main__":
    print(f"Running {len(FEEDBACK_TEST_CASES)} feedback classification test cases...\n")
    results = run_feedback_eval_suite()
    for r in results:
        c_mark = "OK" if r["classification_correct"] else "MISS"
        r_mark = "OK" if r["routing_succeeded"] else "MISS"
        print(f"  [{c_mark}/{r_mark}] {r['id']} -- expected={r['expected_category']} "
              f"predicted={r['predicted_category']} ({r['latency_ms']}ms)")
    print("\nSummary:", summarize_feedback_eval(results))
