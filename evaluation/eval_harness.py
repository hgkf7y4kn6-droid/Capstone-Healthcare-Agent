"""
Model evaluation module (Part 2, step 6).

Since this project uses a local model rather than an OpenAI-only LangChain
chain, we implement a QAEvalChain-equivalent: an LLM-as-judge grader that
scores the agent's generated summaries/responses for accuracy and relevance
against either a reference answer (when available) or general groundedness/
quality criteria (when not). Results are logged to EVAL_LOG_PATH and
aggregated for the Streamlit metrics view.

NOTE: the "judge" here is the same small local model doing everything else
in this project (see agents/llm.py) — a free/local model is a meaningfully
weaker grader than a frontier hosted model would be. Treat these scores as
a rough automated signal, not a substitute for human review, more so here
than you would with a hosted-API judge.
"""

import os
import sys
import json
import datetime as dt

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EVAL_LOG_PATH
from agents.llm import generate

JUDGE_PROMPT = """You are grading the output of a healthcare assistant AI.

Question/Request: {question}
Agent's Response: {response}
{reference_block}

Grade the response on two 1-5 integer scales:
- accuracy: is it factually correct and not hallucinated (relative to the reference if given, otherwise relative to established medical knowledge)?
- relevance: does it actually address the request?

Also give a one-word verdict: CORRECT, PARTIAL, or INCORRECT.

Respond ONLY with strict JSON: {{"accuracy": <1-5>, "relevance": <1-5>, "verdict": "CORRECT|PARTIAL|INCORRECT", "rationale": "<one sentence>"}}
"""


def grade_response(question: str, response: str, reference: str = None, module: str = "general") -> dict:
    reference_block = f"Reference/expected answer: {reference}" if reference else ""
    prompt = JUDGE_PROMPT.format(question=question, response=response, reference_block=reference_block)
    text = generate([
        {"role": "system", "content": "You are a strict, careful grader."},
        {"role": "user", "content": prompt},
    ], temperature=0.0)
    try:
        parsed = json.loads(text.strip().strip("`").replace("json\n", "", 1))
    except (json.JSONDecodeError, ValueError):
        parsed = {"accuracy": None, "relevance": None, "verdict": "UNKNOWN", "rationale": text[:200]}

    record = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "module": module,
        "question": question,
        "response": response[:500],
        "reference": reference,
        **parsed,
    }
    with open(EVAL_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def log_tool_outcome(module: str, success: bool):
    """Lightweight success/failure log for non-LLM-graded events (e.g. did a
    booking API call succeed), used for per-module success-rate metrics."""
    record = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "module": module,
        "type": "tool_outcome",
        "success": success,
    }
    with open(EVAL_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_eval_records(limit: int = 500) -> list[dict]:
    if not os.path.exists(EVAL_LOG_PATH):
        return []
    with open(EVAL_LOG_PATH, "r") as f:
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


def summarize_metrics() -> dict:
    records = load_eval_records()
    graded = [r for r in records if r.get("verdict") in ("CORRECT", "PARTIAL", "INCORRECT")]
    tool_outcomes = [r for r in records if r.get("type") == "tool_outcome"]

    by_module = {}
    for r in graded:
        m = r.get("module", "general")
        by_module.setdefault(m, {"count": 0, "correct": 0, "avg_accuracy": [], "avg_relevance": []})
        by_module[m]["count"] += 1
        if r["verdict"] == "CORRECT":
            by_module[m]["correct"] += 1
        if r.get("accuracy") is not None:
            by_module[m]["avg_accuracy"].append(r["accuracy"])
        if r.get("relevance") is not None:
            by_module[m]["avg_relevance"].append(r["relevance"])

    summary = {}
    for m, d in by_module.items():
        summary[m] = {
            "count": d["count"],
            "correct_rate": round(d["correct"] / d["count"], 2) if d["count"] else 0,
            "avg_accuracy": round(sum(d["avg_accuracy"]) / len(d["avg_accuracy"]), 2) if d["avg_accuracy"] else None,
            "avg_relevance": round(sum(d["avg_relevance"]) / len(d["avg_relevance"]), 2) if d["avg_relevance"] else None,
        }

    tool_by_module = {}
    for r in tool_outcomes:
        m = r.get("module", "general")
        tool_by_module.setdefault(m, {"count": 0, "success": 0})
        tool_by_module[m]["count"] += 1
        if r.get("success"):
            tool_by_module[m]["success"] += 1
    tool_summary = {
        m: {"count": d["count"], "success_rate": round(d["success"] / d["count"], 2) if d["count"] else 0}
        for m, d in tool_by_module.items()
    }

    return {"llm_graded": summary, "tool_success": tool_summary}
