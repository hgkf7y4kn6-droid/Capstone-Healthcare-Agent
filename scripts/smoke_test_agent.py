"""
Live agent smoke test.

This is deliberately NOT part of tests/ (which runs in CI with zero extra
dependencies). This script exercises the *actual* agent pipeline end to
end — it needs the full requirements.txt installed (transformers, torch,
crewai, etc.) and will download the local LLM's weights on
first run if they aren't cached yet. Run it once after `pip install -r
requirements.txt` to confirm the agent genuinely works on your machine,
not just that the code compiles.

Usage:
    python -m scripts.smoke_test_agent           # run all scenarios
    python -m scripts.smoke_test_agent --quick    # run just the first 2 (faster)

What "pass" means here: the agent completed without crashing, produced a
non-empty response, and called at least one of the tools you'd expect for
that request. It does NOT require Cal.com/Tavily API keys to be configured
— a scenario that correctly attempts to call book_appointment but gets a
"CALCOM_API_KEY is not set" error back is still a PASS for the agent
architecture; that's a separate, expected configuration gap, not a bug in
the agent. Scenarios are designed to primarily exercise the free-by-default
tools (EHR + MedlinePlus search) so a fresh clone with just the LLM
configured can pass everything.
"""

import argparse
import sys
import time
import traceback

SCENARIOS = [
    {
        "name": "EHR lookup",
        "query": "Find the patient named John Smith and tell me what conditions are on file for him.",
        "expect_any_tool": ["find_patient_record", "get_patient_history"],
    },
    {
        "name": "Medical information search (RAG)",
        "query": "What is asthma and how is it typically treated?",
        "expect_any_tool": ["search_disease_information"],
    },
    {
        "name": "Prescription lookup",
        "query": "What prescriptions does John Smith have on file?",
        "expect_any_tool": ["find_patient_record", "list_patient_prescriptions"],
    },
    {
        "name": "Multi-step: history + medical info (original capstone sample scenario)",
        "query": (
            "My 70-year-old father John Smith has chronic kidney disease. Can you "
            "look up his medical history and also summarize the latest treatment "
            "methods for CKD?"
        ),
        "expect_any_tool": ["find_patient_record", "get_patient_history", "search_disease_information"],
    },
    {
        "name": "Prescription refill (identity verification + rule engine)",
        "query": (
            "I'd like to refill my Metformin prescription. My name is John Smith "
            "and my date of birth is 1979-03-15."
        ),
        "expect_any_tool": ["find_patient_record", "list_patient_prescriptions", "request_prescription_refill"],
    },
]


def _ensure_data_seeded():
    from data import patient_db as db
    db.init_db(seed=False)
    if not db.list_patients():
        print("No patients found — running data ingestion first...\n")
        from data import ingest_patient_data as ingest
        ingest.main()
        print()


def run_scenario(scenario: dict) -> dict:
    from agents.crew import run_assistant

    print(f"--- {scenario['name']} ---")
    print(f"Query: {scenario['query']}")
    start = time.perf_counter()
    try:
        result = run_assistant(scenario["query"])
    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"CRASHED after {elapsed:.1f}s: {e}")
        traceback.print_exc()
        return {"name": scenario["name"], "passed": False, "elapsed": elapsed, "error": str(e)}

    elapsed = time.perf_counter() - start
    final_response = result.get("final_response") or ""
    tools_called = {t["tool"] for t in result.get("tool_trace", [])}
    expected_hit = bool(tools_called & set(scenario["expect_any_tool"]))

    print(f"Elapsed: {elapsed:.1f}s")
    print(f"Tools called: {sorted(tools_called) or '(none)'}")
    print(f"Response: {final_response[:300]}{'...' if len(final_response) > 300 else ''}")

    passed = bool(final_response) and expected_hit
    if passed:
        print("PASS\n")
    else:
        reasons = []
        if not final_response:
            reasons.append("empty final response")
        if not expected_hit:
            reasons.append(f"none of expected tools {scenario['expect_any_tool']} were called")
        print(f"FAIL ({'; '.join(reasons)})\n")

    return {"name": scenario["name"], "passed": passed, "elapsed": elapsed, "tools_called": sorted(tools_called)}


def run_feedback_scenario() -> dict:
    """Separate check for the feedback/support-ticket triage crew
    (agents/feedback_crew.py) -- a different entry point than
    run_assistant(), so it isn't in SCENARIOS above."""
    from agents.feedback_crew import run_feedback_workflow

    name = "Feedback triage (Classifier -> Feedback Handler, from the spec's own example)"
    query = "Thanks for sorting out my medication refill issue."
    print(f"--- {name} ---")
    print(f"Query: {query}")
    start = time.perf_counter()
    try:
        result = run_feedback_workflow(query)
    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"CRASHED after {elapsed:.1f}s: {e}")
        traceback.print_exc()
        return {"name": name, "passed": False, "elapsed": elapsed, "error": str(e)}

    elapsed = time.perf_counter() - start
    print(f"Elapsed: {elapsed:.1f}s")
    print(f"Classified as: {result.get('category')} (via {result.get('classification_method')})")
    print(f"Response: {result.get('message')}")

    passed = result.get("category") == "positive_feedback" and "thank you" in (result.get("message") or "").lower()
    print("PASS\n" if passed else "FAIL (wrong category or unexpected response format)\n")
    return {"name": name, "passed": passed, "elapsed": elapsed}


def main():
    parser = argparse.ArgumentParser(description="Smoke-test the live agent end to end.")
    parser.add_argument("--quick", action="store_true", help="Run only the first 2 scenarios (faster)")
    args = parser.parse_args()

    print("=" * 70)
    print("Agentic Healthcare Assistant -- live agent smoke test")
    print("=" * 70)

    # Import here (not lazily inside run_scenario) so a missing dependency
    # is caught once, up front, with a clear message -- rather than crashing
    # with a raw traceback partway through the first scenario.
    try:
        from agents.crew import run_assistant  # noqa: F401  (import-check only)
    except ImportError as e:
        print(f"\nCould not import the agent pipeline: {e}")
        print("Run `pip install -r requirements.txt` first.\n")
        sys.exit(1)

    try:
        from agents.llm import is_model_downloaded
        from config import LOCAL_LLM_MODEL
        if not is_model_downloaded():
            print(f"\nNote: {LOCAL_LLM_MODEL} isn't cached yet -- the first call below will "
                  f"download it (may take a few minutes depending on your connection).\n")
    except ImportError as e:
        print(f"\nCould not import the agent stack: {e}")
        print("Run `pip install -r requirements.txt` first.\n")
        sys.exit(1)

    _ensure_data_seeded()

    scenarios = SCENARIOS[:2] if args.quick else SCENARIOS
    results = []
    for scenario in scenarios:
        results.append(run_scenario(scenario))

    if not args.quick:
        results.append(run_feedback_scenario())

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['name']} ({r['elapsed']:.1f}s)")

    passed_count = sum(1 for r in results if r["passed"])
    print(f"\n{passed_count}/{len(results)} scenarios passed.")

    if passed_count < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
