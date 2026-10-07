"""
Feedback & Support Ticket Triage crew.

Implements the capstone's multi-agent design:
  1. Classifier agent    -> categorizes the message (positive/negative/
                             query/prescription_refill)
  2. Feedback Handler agent -> triggered for positive/negative feedback
  3. Query Handler agent    -> triggered for status queries
  4. Prescription Refill Agent -> triggered for refill requests; a
                             multi-turn confirmation flow (see
                             workflows/prescription_refill_agent.py)

Unlike agents/crew.py's sequential Planner->Executor flow (where every task
always runs), this workflow is conditionally routed: only the classifier
always runs, and then exactly ONE of the downstream agents runs based on
the classification result. This matches the spec's "Route to the
appropriate downstream agent based on classification" and avoids wasting a
slow local-CPU inference call on an agent whose output would be discarded.

Session-aware routing: if a ticket has an OPEN prescription-refill
confirmation session (awaiting_confirmation or awaiting_correction), a new
incoming message tied to that ticket is routed straight into the
confirmation flow, bypassing the classifier entirely -- a reply like "yes,
that's right" has no classifiable topic of its own, it only makes sense as
an answer to the pending question.

Negative-feedback -> refill chaining: per the spec's own Example 2, a
complaint that mentions a refill (classified as negative_feedback, since
tone takes precedence over topic) automatically chains into starting a
refill confirmation session after the ticket is created, matching
"Negative Feedback Handler Response -> Prescription Refill Agent."

Falls back to the heuristic classifier/deterministic formatters
(workflows/feedback_support.py, workflows/prescription_refill_agent.py) if
the LLM stack isn't available, so this workflow still runs (with reduced
personalization) in a lighter-weight environment.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Optional

from agents.prompts import (
    CLASSIFIER_ROLE, CLASSIFIER_GOAL, CLASSIFIER_BACKSTORY,
    CLASSIFIER_TASK_DESCRIPTION, CLASSIFIER_EXPECTED_OUTPUT,
    FEEDBACK_HANDLER_ROLE, FEEDBACK_HANDLER_GOAL, FEEDBACK_HANDLER_BACKSTORY,
    FEEDBACK_HANDLER_TASK_DESCRIPTION, FEEDBACK_HANDLER_EXPECTED_OUTPUT,
    QUERY_HANDLER_ROLE, QUERY_HANDLER_GOAL, QUERY_HANDLER_BACKSTORY,
    QUERY_HANDLER_TASK_DESCRIPTION, QUERY_HANDLER_EXPECTED_OUTPUT,
    REFILL_AGENT_ROLE, REFILL_AGENT_GOAL, REFILL_AGENT_BACKSTORY,
    REFILL_AGENT_START_TASK_DESCRIPTION, REFILL_AGENT_START_EXPECTED_OUTPUT,
    REFILL_AGENT_CONFIRMATION_TASK_DESCRIPTION, REFILL_AGENT_CONFIRMATION_EXPECTED_OUTPUT,
    REFILL_AGENT_CORRECTION_TASK_DESCRIPTION, REFILL_AGENT_CORRECTION_EXPECTED_OUTPUT,
)
from monitoring.logging_utils import log_event
from monitoring.monitor import track
from data import patient_db as db
from workflows import feedback_support as fs
from workflows import prescription_refill_agent as ra

_REFILL_TRIGGER_KEYWORDS = ("refill", "prescription", "medication")


def classify_message(user_message: str) -> dict:
    """Runs just the Classifier agent and returns its raw category label
    plus which classification path was used (llm vs heuristic-fallback)."""
    with track("feedback_crew.classify") as status:
        try:
            from agents.llm import get_crewai_llm
            from crewai import Agent, Task, Crew, Process

            llm = get_crewai_llm(temperature=0.0)
            classifier_agent = Agent(
                role=CLASSIFIER_ROLE, goal=CLASSIFIER_GOAL, backstory=CLASSIFIER_BACKSTORY,
                llm=llm, tools=[], allow_delegation=False, verbose=False, max_iter=2,
            )
            classifier_task = Task(
                description=CLASSIFIER_TASK_DESCRIPTION.format(user_message=user_message),
                expected_output=CLASSIFIER_EXPECTED_OUTPUT,
                agent=classifier_agent,
            )
            crew = Crew(agents=[classifier_agent], tasks=[classifier_task],
                        process=Process.sequential, verbose=False)
            result = crew.kickoff()
            raw = (result.raw if hasattr(result, "raw") else str(result)).strip().lower()

            category = next((c for c in fs.VALID_CATEGORIES if c in raw), None)
            if category is None:
                # Model didn't return a clean label -- fall back rather than guess wrong.
                category = fs.classify_message_heuristic(user_message)
                status["success"] = False
                return {"category": category, "method": "heuristic_fallback", "raw_llm_output": raw}

            return {"category": category, "method": "llm", "raw_llm_output": raw}

        except ImportError:
            # crewai/transformers not installed -- degrade to the heuristic
            # classifier rather than failing the whole workflow.
            status["success"] = False
            category = fs.classify_message_heuristic(user_message)
            return {"category": category, "method": "heuristic_fallback", "raw_llm_output": None}


def _run_downstream_agent(category: str, user_message: str, patient_name: Optional[str]) -> str:
    """Runs the single downstream agent matching the classification. Falls
    back to calling the deterministic workflow handler directly (bypassing
    the LLM) if crewai/transformers aren't available -- the response format
    matches exactly either way since both paths ultimately call the same
    workflows/feedback_support.py functions."""
    try:
        from agents.llm import get_crewai_llm
        from crewai import Agent, Task, Crew, Process
        from tools.support_ticket_tool import FEEDBACK_TOOLS

        llm = get_crewai_llm()

        if category in (fs.POSITIVE_FEEDBACK, fs.NEGATIVE_FEEDBACK):
            agent = Agent(
                role=FEEDBACK_HANDLER_ROLE, goal=FEEDBACK_HANDLER_GOAL,
                backstory=FEEDBACK_HANDLER_BACKSTORY, llm=llm, tools=FEEDBACK_TOOLS,
                allow_delegation=False, verbose=False, max_iter=4, max_execution_time=90,
            )
            task = Task(
                description=FEEDBACK_HANDLER_TASK_DESCRIPTION.format(
                    category=category, patient_name=patient_name or "", user_message=user_message),
                expected_output=FEEDBACK_HANDLER_EXPECTED_OUTPUT,
                agent=agent,
            )
        else:  # query
            agent = Agent(
                role=QUERY_HANDLER_ROLE, goal=QUERY_HANDLER_GOAL,
                backstory=QUERY_HANDLER_BACKSTORY, llm=llm, tools=FEEDBACK_TOOLS,
                allow_delegation=False, verbose=False, max_iter=4, max_execution_time=90,
            )
            task = Task(
                description=QUERY_HANDLER_TASK_DESCRIPTION.format(user_message=user_message),
                expected_output=QUERY_HANDLER_EXPECTED_OUTPUT,
                agent=agent,
            )

        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        result = crew.kickoff()
        return result.raw if hasattr(result, "raw") else str(result)

    except ImportError:
        # Deterministic fallback -- identical message format, no LLM needed.
        return fs.route_and_handle(category, user_message, patient_name)["message"]


def _run_refill_agent_task(description: str, expected_output: str) -> str:
    """Shared helper: builds a single-agent, single-task crew for one stage
    of the Prescription Refill Agent's confirmation flow."""
    from agents.llm import get_crewai_llm
    from crewai import Agent, Task, Crew, Process
    from tools.prescription_refill_agent_tool import PRESCRIPTION_REFILL_AGENT_TOOLS

    llm = get_crewai_llm()
    agent = Agent(
        role=REFILL_AGENT_ROLE, goal=REFILL_AGENT_GOAL, backstory=REFILL_AGENT_BACKSTORY,
        llm=llm, tools=PRESCRIPTION_REFILL_AGENT_TOOLS, allow_delegation=False,
        verbose=False, max_iter=4, max_execution_time=90,
    )
    task = Task(description=description, expected_output=expected_output, agent=agent)
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff()
    return result.raw if hasattr(result, "raw") else str(result)


def start_prescription_refill(ticket_number: str, patient_name: str, medication_name: str) -> dict:
    """Begins the confirmation flow for a new refill ticket: asks the
    customer to confirm the claimed patient/medication before proceeding."""
    with track("feedback_crew.refill_start") as run_status:
        try:
            message = _run_refill_agent_task(
                REFILL_AGENT_START_TASK_DESCRIPTION.format(
                    ticket_number=ticket_number, patient_name=patient_name, medication_name=medication_name),
                REFILL_AGENT_START_EXPECTED_OUTPUT,
            )
        except ImportError:
            run_status["success"] = False
            ra.start_confirmation(ticket_number, patient_name, medication_name)
            message = ra.format_confirmation_question(ticket_number, patient_name, medication_name)

        log_event("refill_agent_response", {"ticket_number": ticket_number, "stage": "awaiting_confirmation",
                                             "response": message})
        return {"ticket_number": ticket_number, "stage": "awaiting_confirmation", "message": message}


def continue_prescription_refill(ticket_number: str, user_response: str,
                                  corrected_patient_name: Optional[str] = None,
                                  corrected_medication_name: Optional[str] = None) -> dict:
    """Continues an in-progress confirmation session for `ticket_number`,
    dispatching based on the session's current stage. This is the
    session-aware entry point used once a ticket already has an open
    refill_confirmations row -- see run_feedback_workflow()."""
    with track("feedback_crew.refill_continue") as run_status:
        session = db.get_latest_refill_confirmation(ticket_number)
        if not session:
            run_status["success"] = False
            return {"ticket_number": ticket_number, "stage": "error",
                     "message": f"No open refill confirmation found for ticket #{ticket_number}."}

        stage = session["stage"]

        if stage == "awaiting_confirmation":
            try:
                message = _run_refill_agent_task(
                    REFILL_AGENT_CONFIRMATION_TASK_DESCRIPTION.format(
                        ticket_number=ticket_number, user_response=user_response),
                    REFILL_AGENT_CONFIRMATION_EXPECTED_OUTPUT,
                )
                # Re-fetch to report the stage the LLM path actually left us in.
                new_stage = (db.get_latest_refill_confirmation(ticket_number) or {}).get("stage", stage)
            except ImportError:
                run_status["success"] = False
                is_positive = ra.classify_confirmation_response_heuristic(user_response)
                if is_positive:
                    result = ra.confirm_positive(ticket_number)
                    if result["outcome"] == "eligible":
                        message = ra.format_positive_confirmation_ack()
                    else:
                        message = ra.format_positive_confirmation_ack() + " " + ra.format_escalation_message(
                            ticket_number, result["appointment_date"], result["appointment_time"])
                    new_stage = "resolved"
                else:
                    ra.confirm_negative(ticket_number)
                    message = ra.format_negative_confirmation_ack()
                    new_stage = "awaiting_correction"

        elif stage == "awaiting_correction":
            if not corrected_patient_name or not corrected_medication_name:
                run_status["success"] = False
                return {"ticket_number": ticket_number, "stage": "awaiting_correction",
                        "message": "Please provide the corrected Ticket #, Patient name, and Medication name."}
            try:
                message = _run_refill_agent_task(
                    REFILL_AGENT_CORRECTION_TASK_DESCRIPTION.format(
                        ticket_number=ticket_number, user_response=user_response),
                    REFILL_AGENT_CORRECTION_EXPECTED_OUTPUT,
                )
                new_stage = (db.get_latest_refill_confirmation(ticket_number) or {}).get("stage", stage)
            except ImportError:
                run_status["success"] = False
                result = ra.submit_correction(ticket_number, corrected_patient_name, corrected_medication_name)
                if result["outcome"] == "verified_eligible":
                    message = ra.format_verified_ack()
                else:
                    message = ra.format_escalation_message(
                        ticket_number, result["appointment_date"], result["appointment_time"])
                new_stage = "resolved"

        else:
            run_status["success"] = False
            return {"ticket_number": ticket_number, "stage": stage,
                    "message": f"This ticket's confirmation is already {stage}."}

        log_event("refill_agent_response", {"ticket_number": ticket_number, "stage": new_stage, "response": message})
        return {"ticket_number": ticket_number, "stage": new_stage, "message": message}


def run_feedback_workflow(user_message: str, patient_name: Optional[str] = None,
                           active_ticket_number: Optional[str] = None,
                           corrected_patient_name: Optional[str] = None,
                           corrected_medication_name: Optional[str] = None) -> dict:
    """Main entry point: classify the message, then route to exactly one
    downstream handler. Returns the classification, which path handled it,
    and the final response message.

    If active_ticket_number is given and has an open refill-confirmation
    session, this bypasses the classifier entirely and routes straight into
    the confirmation flow (see module docstring) -- a bare "yes" or "no"
    reply has no classifiable topic of its own.
    """
    with track("feedback_crew.full_workflow") as run_status:
        # Session-aware routing: an open confirmation takes priority over classification.
        if active_ticket_number:
            existing_session = db.get_latest_refill_confirmation(active_ticket_number)
            if existing_session and existing_session["stage"] in ("awaiting_confirmation", "awaiting_correction"):
                result = continue_prescription_refill(
                    active_ticket_number, user_message,
                    corrected_patient_name=corrected_patient_name,
                    corrected_medication_name=corrected_medication_name,
                )
                return {
                    "category": fs.PRESCRIPTION_REFILL, "classification_method": "session_continuation",
                    "message": result["message"], "ticket_number": active_ticket_number, "stage": result["stage"],
                }

        classification = classify_message(user_message)
        category = classification["category"]

        log_event("feedback_classification", {
            "user_message": user_message, "category": category,
            "method": classification["method"],
        })

        if category == fs.PRESCRIPTION_REFILL:
            # Fresh refill request with no ticket yet -- open one and start confirmation.
            if not patient_name:
                return {
                    "category": category, "classification_method": classification["method"],
                    "message": "I'd be glad to help with a refill -- could you tell me the patient's "
                               "name and which medication needs refilling?",
                }
            ticket_number = db.create_support_ticket(user_message, patient_name=patient_name)
            medication_name = corrected_medication_name or "the requested medication"
            result = start_prescription_refill(ticket_number, patient_name, medication_name)
            return {
                "category": category, "classification_method": classification["method"],
                "message": result["message"], "ticket_number": ticket_number, "stage": result["stage"],
            }

        try:
            response_message = _run_downstream_agent(category, user_message, patient_name)
        except Exception:
            run_status["success"] = False
            raise

        result = {
            "category": category,
            "classification_method": classification["method"],
            "message": response_message,
        }

        # Spec Example 2: a refill-related complaint chains into the refill
        # agent automatically after the ticket is created.
        if (category == fs.NEGATIVE_FEEDBACK
                and any(kw in user_message.lower() for kw in _REFILL_TRIGGER_KEYWORDS)):
            tickets = db.list_support_tickets()
            latest_ticket = tickets[0]["ticket_number"] if tickets else None
            if latest_ticket and patient_name:
                chained = start_prescription_refill(latest_ticket, patient_name, "the requested medication")
                result["chained_refill_agent"] = {
                    "ticket_number": latest_ticket, "message": chained["message"], "stage": chained["stage"],
                }

        log_event("feedback_response", {
            "user_message": user_message, "category": category, "response": response_message,
        })

        return result
