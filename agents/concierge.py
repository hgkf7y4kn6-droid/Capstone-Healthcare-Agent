"""
The deterministic concierge — AegisTriage's patient assistant when CrewAI or
the local model isn't available (or when ASSISTANT_ENGINE=concierge, the
recommended setting for hosted deployments).

It understands the same jobs the CrewAI executor does — booking, records,
medications/refills and condition information — using keyword intent
detection over verified records. It never invents clinical facts: records
come from the EHR, condition information comes from MedlinePlus, and
anything it can't do it hands to the clinic team.

Returns the same shape as agents.crew.run_assistant (final_response, plan,
tool_trace) plus `cards` and `actions` the web app renders.
"""

import os
import re
import sys
import time
from typing import Optional
from urllib.parse import quote_plus

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CLINIC_NAME, CLINIC_PHONE
from data import patient_db as db
from monitoring.logging_utils import log_event
from monitoring.monitor import track

# Specialty vocabulary: what people say -> the specialty a scheduler books.
SPECIALTIES = {
    "Nephrology": ["kidney", "renal", "nephrolog", "dialysis", "ckd"],
    "Cardiology": ["heart", "cardio", "blood pressure", "hypertension", "palpitation", "cholesterol"],
    "Pulmonology": ["lung", "pulmon", "asthma", "copd", "breathing", "cough"],
    "Dermatology": ["skin", "dermat", "rash", "eczema", "psoriasis", "acne", "mole"],
    "Gastroenterology": ["stomach", "gastro", "digest", "reflux", "gerd", "bowel", "colon"],
    "Neurology": ["neuro", "migraine", "headache", "seizure history", "nerve", "memory"],
    "Rheumatology": ["rheumat", "arthritis", "joint pain", "lupus", "gout"],
    "Psychiatry": ["psychiatr", "anxiety", "depression", "mental health", "therap"],
    "Endocrinology": ["diabet", "thyroid", "endocrin", "hormone", "insulin"],
    "Orthopedics": ["bone", "orthop", "fracture", "knee", "shoulder", "back pain"],
    "Pediatrics": ["pediatr", "my son", "my daughter", "my child", "my baby", "kid"],
    "Primary Care": ["checkup", "check-up", "physical", "annual", "primary care", "family doctor", "gp"],
}

_BOOK = re.compile(r"\b(book|schedul|appointment|see (a|the|my) (doctor|specialist|provider)|make an? (visit|appt)|visit with)\w*", re.I)
_RECORDS = re.compile(r"\b(my|his|her|their|the patient'?s?)\s+(records?|history|chart|diagnos\w*|labs?|lab results?|allerg\w*|conditions?)\b|\bmedical (records?|history)\b", re.I)
_MEDS = re.compile(r"\b(refill|prescription|medication|medicine|meds|pharmacy|renew)\w*", re.I)
# Strong signals always mean "explain a condition"; weak ones ("what is …")
# only count when nothing else in the message matched.
_INFO_STRONG = re.compile(
    r"\b(tell me about|explain|summari[sz]e|treatment(s)? (for|of|methods|options)|symptoms? of|causes? of|"
    r"how (do|is|are) .* treated|latest treatment|learn about|information on)\b", re.I)
_INFO_WEAK = re.compile(r"\b(what is|what are|what'?s)\b", re.I)
_GREETING = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|help|what can you do)\b", re.I)

_CONDITION_STOPWORDS = {
    "the", "a", "an", "my", "his", "her", "their", "for", "of", "about", "me", "latest", "treatment",
    "treatments", "methods", "method", "can", "you", "also", "please", "and", "is", "are", "what",
    "symptoms", "symptom", "causes", "cause", "tell", "explain", "summarize", "summarise", "on",
    "information", "learn", "how", "treated", "it", "this", "that", "options", "new", "current",
}


def _detect_specialty(text: str) -> Optional[str]:
    lowered = text.lower()
    for specialty, words in SPECIALTIES.items():
        if any(w in lowered for w in words):
            return specialty
    return None


def _condition_from_text(text: str, patient: Optional[dict]) -> Optional[str]:
    """Best-effort topic for an information request: a named condition in
    the message, otherwise the patient's own primary diagnosis."""
    lowered = text.lower()
    m = re.search(r"\b(?:has|have|with|diagnosed with)\s+([a-z][a-z \-']{3,60}?)(?:[.,;!?]|\band\b|$)", lowered)
    if m:
        return m.group(1).strip()
    m = re.search(
        r"(?:about|on|for|of|is|are|explain|summari[sz]e)\s+((?:the\s+)?[a-z][a-z \-']{3,60}?)(?:\?|[.,;!]|$)",
        lowered)
    if m:
        words = [w for w in re.findall(r"[a-z\-']+", m.group(1)) if w not in _CONDITION_STOPWORDS]
        if words:
            return " ".join(words)
    if patient:
        for entry in db.get_history(patient["patient_id"]):
            content = entry.get("content")
            if entry["entry_type"] == "diagnosis" and isinstance(content, dict) and content.get("condition"):
                return content["condition"]
    return None


def _medlineplus_lookup(topic: str) -> list[dict]:
    try:
        from integrations.medlineplus import search_medlineplus
        return search_medlineplus(topic, max_results=2, timeout=6)
    except Exception:
        return []


_PLURALS = {"diagnosis": "Diagnoses", "treatment": "Treatments", "alert": "Alerts", "lab_result": "Lab results",
            "visit": "Visits", "clinical_note": "Clinical notes", "note": "Notes", "emergency_contact": "Emergency contacts"}


def _fmt_value(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in value.items() if v not in (None, ""))
    return str(value)


def _entry_label(entry: dict) -> Optional[str]:
    content = entry["content"]
    if isinstance(content, dict):
        for key in ("condition", "treatment", "medication", "alert", "allergy", "test", "reason", "summary", "name"):
            if content.get(key):
                label = _fmt_value(content[key])
                extra = content.get("severity") if key == "allergy" else content.get("date") or content.get("as_of")
                if key == "allergy":
                    label = f"Allergy: {label}" + (f" ({extra})" if extra and extra != "unspecified" else "")
                elif extra:
                    label += f" ({extra})"
                return label
        if isinstance(content.get("results"), dict):
            return _fmt_value(content["results"]) + (f" (as of {content['as_of']})" if content.get("as_of") else "")
        return _fmt_value(content)
    text = re.sub(r"\s+", " ", str(content)).strip()
    if len(text) > 160:  # raw scanned-document text: show it on the record page, not in chat
        return None
    return text


# What a patient sees about their own record. Staff notes and clinical notes
# are internal (Pool PM rule: clients never receive internal fields) and stay
# in the staff console.
PATIENT_VISIBLE_TYPES = ("diagnosis", "treatment", "alert", "lab_result", "visit")


def _summarize_history(patient: dict) -> tuple[str, dict]:
    history = [e for e in db.get_history(patient["patient_id"]) if e["entry_type"] in PATIENT_VISIBLE_TYPES]
    groups: dict[str, list[str]] = {}
    for e in history:
        label = _entry_label(e)
        if not label:
            continue
        if len(label) > 140:
            label = label[:137].rstrip() + "…"
        groups.setdefault(e["entry_type"], []).append(label)

    lines = []
    for key in PATIENT_VISIBLE_TYPES:
        if key in groups:
            title = _PLURALS.get(key, key.replace("_", " ").capitalize())
            items = list(dict.fromkeys(groups[key]))[:4]
            lines.append(f"**{title}:** " + "; ".join(items))
    if not lines:
        return f"I don't see any history on file for {patient['full_name']} yet.", {}
    card = {"type": "record_summary", "patient": patient["full_name"],
            "groups": {_PLURALS.get(k, k): list(dict.fromkeys(v))[:4] for k, v in groups.items()}}
    return (f"Here's what's on file for **{patient['full_name']}**:\n\n" + "\n".join(f"- {l}" for l in lines)
            + "\n\nYour care team can explain anything here in more detail.", card)


def run_concierge(user_query: str, patient_id: Optional[int] = None) -> dict:
    started = time.perf_counter()
    with track("assistant.concierge", meta={"query_length": len(user_query)}) as status:
        patient = db.get_patient(patient_id) if patient_id else None
        sub_goals, sections, cards, trace = [], [], [], []
        actions: list[dict] = []

        wants_booking = bool(_BOOK.search(user_query))
        wants_records = bool(_RECORDS.search(user_query))
        wants_meds = bool(_MEDS.search(user_query))
        wants_info = bool(_INFO_STRONG.search(user_query)) or (
            bool(_INFO_WEAK.search(user_query)) and not (wants_booking or wants_records or wants_meds))

        if _GREETING.search(user_query) and not (wants_booking or wants_records or wants_meds or wants_info):
            first = patient["full_name"].split()[0] if patient else "there"
            text = (f"Hi {first}! I can request an appointment, go over what's in your record, check your "
                    f"medications and refills, or explain a condition using MedlinePlus. What would help today?")
            return {"final_response": text, "plan": {"sub_goals": []}, "tool_trace": [], "cards": [],
                    "actions": _default_actions(patient), "engine": "concierge"}

        step = 0
        if wants_booking:
            step += 1
            specialty = _detect_specialty(user_query) or "Primary Care"
            sub_goals.append({"step": step, "goal": f"Request a {specialty} appointment", "tool_category": "calendar"})
            if patient:
                appt_id = db.record_appointment(
                    patient["patient_id"], doctor_name="Next available", specialty=specialty,
                    calcom_booking_uid=None, start_time="", status="requested",
                )
                trace.append({"tool": "request_appointment", "status": "success",
                              "args": {"patient_id": patient["patient_id"], "specialty": specialty},
                              "result": f"appointment_id={appt_id}"})
                log_event("appointment_requested", {"patient_id": patient["patient_id"], "specialty": specialty,
                                                    "appointment_id": appt_id})
                sections.append(
                    f"I've sent a **{specialty}** appointment request for {patient['full_name']} to the "
                    f"{CLINIC_NAME} front desk. They'll confirm a time, and you'll see it in your portal "
                    f"once it's booked.")
                cards.append({"type": "appointment_request", "appointment_id": appt_id, "specialty": specialty,
                              "patient": patient["full_name"], "status": "requested"})
            else:
                sections.append(
                    f"I can request a **{specialty}** appointment. Choose who it's for at the top of the chat "
                    f"so the front desk knows which record to use.")
                actions.append({"label": "Choose a patient", "intent": "select_patient"})

        if wants_records:
            step += 1
            sub_goals.append({"step": step, "goal": "Summarize the patient's record", "tool_category": "ehr"})
            if patient:
                summary, card = _summarize_history(patient)
                trace.append({"tool": "get_patient_history", "status": "success",
                              "args": {"patient_id": patient["patient_id"]}, "result": f"{len(card.get('groups', {}))} groups"})
                sections.append(summary)
                if card:
                    cards.append(card)
            else:
                sections.append("To go over a record I need to know whose it is — choose a patient first.")

        if wants_meds:
            step += 1
            sub_goals.append({"step": step, "goal": "Review medications and refill options", "tool_category": "prescriptions"})
            if patient:
                meds = db.list_prescriptions(patient["patient_id"])
                trace.append({"tool": "list_patient_prescriptions", "status": "success",
                              "args": {"patient_id": patient["patient_id"]}, "result": f"{len(meds)} prescriptions"})
                if meds:
                    lines = []
                    for rx in meds:
                        note = []
                        if rx["remaining_refills"] <= 0:
                            note.append("no refills left — needs your provider")
                        else:
                            note.append(f"{rx['remaining_refills']} refill{'s' if rx['remaining_refills'] != 1 else ''} left")
                        if rx["is_controlled_substance"]:
                            note.append("controlled — reviewed by a provider")
                        if rx["requires_lab_check"]:
                            note.append(f"needs a recent {rx['lab_check_type']} result")
                        lines.append(f"- **{rx['medication_name']}** — {', '.join(note)}")
                    sections.append("Medications on file:\n\n" + "\n".join(lines) +
                                    "\n\nTap **Request a refill** and I'll verify identity and run the safety checks.")
                    cards.append({"type": "prescriptions", "items": [
                        {"prescription_id": rx["prescription_id"], "name": rx["medication_name"],
                         "refills": rx["remaining_refills"], "controlled": bool(rx["is_controlled_substance"]),
                         "lab": rx["lab_check_type"] if rx["requires_lab_check"] else None} for rx in meds]})
                    actions.append({"label": "Request a refill", "intent": "refill"})
                else:
                    sections.append(f"There are no prescriptions on file for {patient['full_name']}.")
            else:
                sections.append("Choose a patient and I'll list their medications and refill options.")

        if wants_info or not sub_goals:
            topic = _condition_from_text(user_query, patient) if wants_info else None
            if topic:
                step += 1
                sub_goals.append({"step": step, "goal": f"Explain {topic} from trusted sources", "tool_category": "medical_search"})
                results = _medlineplus_lookup(topic)
                trace.append({"tool": "search_disease_information", "status": "success" if results else "error",
                              "args": {"query": topic}, "result": f"{len(results)} MedlinePlus topics"})
                if results:
                    top = results[0]
                    snippet = top["snippet"] or "MedlinePlus has a plain-language overview."
                    sections.append(f"**About {top['title']}** (MedlinePlus): {snippet}")
                    cards.append({"type": "sources", "items": [{"title": r["title"], "url": r["url"],
                                                                "source": r["source"]} for r in results]})
                else:
                    url = f"https://medlineplus.gov/search/?query={quote_plus(topic)}"
                    sections.append(
                        f"I couldn't reach MedlinePlus just now. Their overview of **{topic}** covers symptoms, "
                        f"treatment options and questions to ask your provider.")
                    cards.append({"type": "sources", "items": [{"title": f"MedlinePlus: {topic}", "url": url,
                                                                "source": "MedlinePlus"}]})
                sections.append("Treatment choices depend on the person, so bring questions to the care team — "
                                "I can request a visit if that helps.")
                if not wants_booking:
                    actions.append({"label": "Request a visit", "intent": "book"})
            elif not sub_goals:
                sections.append(
                    "I can help you **request an appointment**, **review a record**, **check medications and "
                    f"refills**, or **explain a condition**. For anything else, call {CLINIC_NAME} at {CLINIC_PHONE}.")
                actions.extend(_default_actions(patient))
                status["success"] = False

        text = "\n\n".join(sections)
        log_event("plan", {"user_query": user_query, "plan": {"sub_goals": sub_goals}, "engine": "concierge"})
        log_event("response", {"user_query": user_query, "final_response": text, "engine": "concierge",
                               "latency_ms": round((time.perf_counter() - started) * 1000, 1)})
        seen, deduped = set(), []
        for a in actions:
            if a["intent"] not in seen:
                seen.add(a["intent"])
                deduped.append(a)
        return {"final_response": text, "plan": {"sub_goals": sub_goals}, "tool_trace": trace,
                "cards": cards, "actions": deduped, "engine": "concierge"}


def _default_actions(patient: Optional[dict]) -> list[dict]:
    return [
        {"label": "Request an appointment", "intent": "book"},
        {"label": "Review my record", "intent": "records"},
        {"label": "My medications", "intent": "meds"},
    ]
