"""
Single entry point for the patient assistant: safety screen first, then the
configured engine.

    safety screen (workflows/safety.py, deterministic)
        └─ emergency → fixed guidance, stop. No model sees the message.
        └─ otherwise → engine:
             crew       CrewAI planner + executor on the local model (agents/crew.py)
             concierge  deterministic intent routing over verified records (agents/concierge.py)
             auto       crew when CrewAI is installed, falling back to concierge on any failure

Every reply carries `safety` (None, or the urgent/emergency rule that
matched) and `engine` so the UI and the audit log show which path answered.
"""

import os
import sys
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ASSISTANT_ENGINE
from monitoring.logging_utils import log_event
from workflows.safety import screen_message, EMERGENCY


def crew_available() -> bool:
    try:
        import crewai  # noqa: F401
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def resolve_engine(requested: Optional[str] = None) -> str:
    engine = (requested or ASSISTANT_ENGINE or "auto").lower()
    if engine == "auto":
        return "crew" if crew_available() else "concierge"
    return engine if engine in ("crew", "concierge") else "concierge"


def answer(user_query: str, patient_id: Optional[int] = None, engine: Optional[str] = None) -> dict:
    user_query = (user_query or "").strip()
    safety = screen_message(user_query)
    if safety:
        log_event("safety_screen", {"rule_id": safety["rule_id"], "level": safety["level"]})

    if safety and safety["level"] == EMERGENCY:
        return {
            "final_response": f"**{safety['title']}.** {safety['guidance']}",
            "plan": {"sub_goals": []}, "tool_trace": [], "cards": [], "actions": [],
            "engine": "safety", "safety": safety,
        }

    chosen = resolve_engine(engine)
    result: dict
    if chosen == "crew":
        try:
            from agents.crew import run_assistant
            result = run_assistant(user_query, patient_id_hint=patient_id)
            result.setdefault("cards", [])
            result.setdefault("actions", [])
            result["engine"] = "crew"
        except Exception as exc:  # degrade, never strand the patient
            log_event("assistant_fallback", {"from": "crew", "error": str(exc)[:300]})
            if (engine or ASSISTANT_ENGINE) == "crew":
                raise
            from agents.concierge import run_concierge
            result = run_concierge(user_query, patient_id)
    else:
        from agents.concierge import run_concierge
        result = run_concierge(user_query, patient_id)

    result["safety"] = safety
    if safety:  # urgent: lead with the guidance, then still help
        result["final_response"] = f"**{safety['title']}.** {safety['guidance']}\n\n" + result["final_response"]
    return result
