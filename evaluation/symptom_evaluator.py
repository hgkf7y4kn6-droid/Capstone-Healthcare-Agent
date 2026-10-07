"""
Symptom-analysis QA evaluator (Part 2, "accuracy of symptom analysis"
requirement).

For each test case in evaluation/symptom_eval_dataset.py, this:
  1. Times how long the medical-search RAG pipeline takes to respond
     (speed) — reuses the existing search_disease_information tool + a
     synthesis call, mirroring what the agent does in a real conversation.
  2. Checks whether the expected condition (or an accepted keyword variant)
     appears in the synthesized answer (fast, automated correctness check).
  3. Additionally asks the LLM-as-judge (eval_harness.grade_response) for a
     nuanced accuracy/relevance score, since keyword matching alone can
     miss correct-but-differently-worded answers or catch false positives.

Results are logged to both the monitoring log (module="symptom_analysis",
latency) and the eval log (module="symptom_analysis", correctness), so both
the Monitoring and Evaluation tabs in Streamlit reflect them.
"""

import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evaluation.symptom_eval_dataset import SYMPTOM_TEST_CASES
from evaluation.eval_harness import grade_response
from monitoring.monitor import track
from tools.medical_search_tool import search_disease_information
from agents.prompts import RAG_SYNTHESIS_PROMPT
from agents.llm import generate


def _synthesize_answer(symptom_description: str) -> str:
    """Mirrors the agent's real symptom-analysis path: search trusted
    sources, then have the local model synthesize a grounded answer."""
    search_results = search_disease_information.run(query=symptom_description)

    prompt = RAG_SYNTHESIS_PROMPT.format(topic=symptom_description, snippets=search_results)
    return generate([
        {"role": "system", "content": "You are a careful clinical information assistant."},
        {"role": "user", "content": prompt},
    ], temperature=0.0)


def _keyword_match(answer: str, acceptable_keywords: list[str]) -> bool:
    answer_lower = answer.lower()
    return any(kw.lower() in answer_lower for kw in acceptable_keywords)


def run_symptom_analysis_suite(use_llm_judge: bool = True, limit: int = None) -> list[dict]:
    """Runs the full (or first `limit`) test suite. Returns a list of
    per-case result dicts and logs speed + accuracy for each to the
    monitoring and evaluation logs."""
    cases = SYMPTOM_TEST_CASES[:limit] if limit else SYMPTOM_TEST_CASES
    results = []

    for case in cases:
        start = time.perf_counter()
        error = None
        answer = ""
        with track("symptom_analysis.full_case", meta={"case_id": case["id"]}) as status:
            try:
                answer = _synthesize_answer(case["symptom_description"])
            except Exception as e:
                status["success"] = False
                error = str(e)

        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

        if error:
            results.append({
                "id": case["id"], "expected_condition": case["expected_condition"],
                "keyword_match": False, "llm_verdict": "ERROR", "latency_ms": elapsed_ms,
                "answer": f"[error: {error}]", "error": error,
            })
            continue

        keyword_correct = _keyword_match(answer, case["acceptable_keywords"])

        llm_grade = None
        if use_llm_judge:
            llm_grade = grade_response(
                question=case["symptom_description"],
                response=answer,
                reference=f"Expected diagnosis: {case['expected_condition']}",
                module="symptom_analysis",
            )

        results.append({
            "id": case["id"],
            "symptom_description": case["symptom_description"],
            "expected_condition": case["expected_condition"],
            "answer": answer,
            "keyword_match": keyword_correct,
            "llm_verdict": llm_grade["verdict"] if llm_grade else None,
            "llm_accuracy": llm_grade.get("accuracy") if llm_grade else None,
            "latency_ms": elapsed_ms,
        })

    return results


def summarize_suite_results(results: list[dict]) -> dict:
    if not results:
        return {"count": 0}
    n = len(results)
    keyword_correct = sum(1 for r in results if r.get("keyword_match"))
    llm_correct = sum(1 for r in results if r.get("llm_verdict") == "CORRECT")
    latencies = [r["latency_ms"] for r in results if "latency_ms" in r]
    return {
        "count": n,
        "keyword_accuracy": round(keyword_correct / n, 2),
        "llm_judge_accuracy": round(llm_correct / n, 2) if any(r.get("llm_verdict") for r in results) else None,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "max_latency_ms": round(max(latencies), 1) if latencies else None,
    }


if __name__ == "__main__":
    print(f"Running {len(SYMPTOM_TEST_CASES)} symptom-analysis test cases...\n")
    results = run_symptom_analysis_suite()
    for r in results:
        mark = "✓" if r.get("keyword_match") else "✗"
        print(f"  [{mark}] {r['id']} — expected '{r['expected_condition']}' "
              f"— {r['latency_ms']}ms — llm_verdict={r.get('llm_verdict')}")
    print("\nSummary:", summarize_suite_results(results))
