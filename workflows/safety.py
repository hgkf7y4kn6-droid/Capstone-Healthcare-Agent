"""
Deterministic safety screen — runs on every patient message BEFORE any
agent or model sees it.

Why deterministic: a small local model can miss or soften an emergency.
These rules are explicit, testable and reviewable by a clinician, and the
response they produce is fixed wording rather than generated text. An LLM
never decides whether something is an emergency (same principle as the
"deterministic first" intelligence layer in Pool PM).

Levels:
  emergency — tell the person to call 911 (or 988 / Poison Control) now and
              stop: no booking, no symptom chat.
  urgent    — same-day care advised; the assistant still helps afterwards.
  None      — nothing matched.

This is a screen, not a diagnosis. It favors false positives.
"""

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

EMERGENCY = "emergency"
URGENT = "urgent"


@dataclass
class SafetyRule:
    id: str
    level: str
    patterns: list
    title: str
    guidance: str
    contacts: list = field(default_factory=list)


_CALL_911 = {"label": "Call 911", "href": "tel:911"}
_CALL_988 = {"label": "Call or text 988", "href": "tel:988"}
_POISON = {"label": "Poison Control 1-800-222-1222", "href": "tel:18002221222"}

# Order matters: the first matching rule wins, so the most specific and
# most serious rules come first.
RULES: list[SafetyRule] = [
    SafetyRule(
        "self_harm", EMERGENCY,
        [r"\b(kill|hurt|harm)(ing)? (myself|my ?self)\b", r"\bsuicid", r"\bend (my|it all)\b.*\blife\b",
         r"\bwant to die\b", r"\bdon'?t want to (live|be alive)\b", r"\bend it all\b"],
        "You don't have to go through this alone",
        "Please reach out right now. You can call or text 988 to talk with someone at the "
        "Suicide & Crisis Lifeline any time, day or night. If you might act on these thoughts "
        "or are in danger, call 911.",
        [_CALL_988, _CALL_911],
    ),
    SafetyRule(
        "overdose_poisoning", EMERGENCY,
        [r"\boverdos", r"\btook (too many|a lot of|the whole)\b", r"\bpoison(ed|ing)?\b",
         r"\bswallowed\b.*\b(bleach|cleaner|battery|pills)\b"],
        "This could be a poisoning or overdose",
        "Call Poison Control at 1-800-222-1222 now. If the person is hard to wake, having "
        "trouble breathing or having a seizure, call 911.",
        [_POISON, _CALL_911],
    ),
    SafetyRule(
        "chest_pain", EMERGENCY,
        [r"\bchest (pain|pressure|tightness|hurts)\b", r"\bpain in (my|the) chest\b",
         r"\bheart attack\b", r"\bcrushing\b.*\bchest\b"],
        "Chest pain needs emergency care",
        "Call 911 now and don't drive yourself. Follow the dispatcher's instructions while you wait.",
        [_CALL_911],
    ),
    SafetyRule(
        "stroke", EMERGENCY,
        [r"\bface (is )?droop", r"\bslurred speech\b", r"\b(sudden )?(weakness|numbness) (in|on) one side\b",
         r"\bcan'?t (move|feel) (my|one) (arm|leg|side)\b", r"\bstroke\b", r"\bworst headache\b"],
        "These can be signs of a stroke",
        "Call 911 now and note the time the symptoms started. Every minute matters for stroke treatment.",
        [_CALL_911],
    ),
    SafetyRule(
        "breathing", EMERGENCY,
        [r"\bcan'?t breathe\b", r"\b(trouble|difficulty|struggling to) breath", r"\bgasping\b",
         r"\blips (are |turning )?(blue|gray|grey)\b", r"\bchoking\b"],
        "Trouble breathing is an emergency",
        "Call 911 now. Sit upright and use a rescue inhaler if one was prescribed for you.",
        [_CALL_911],
    ),
    SafetyRule(
        "anaphylaxis", EMERGENCY,
        [r"\b(throat|tongue|lips?) (is |are )?(swelling|swollen|closing)\b", r"\banaphyla",
         r"\ballergic reaction\b.*\b(breath|swell|throat)\b"],
        "This may be a severe allergic reaction",
        "Use an epinephrine auto-injector if you have one, then call 911.",
        [_CALL_911],
    ),
    SafetyRule(
        "bleeding_unconscious_seizure", EMERGENCY,
        [r"\b(won'?t|can'?t) stop bleeding\b", r"\bbleeding (heavily|a lot|badly)\b", r"\bunconscious\b",
         r"\bpassed out\b.*\b(won'?t|not) wak", r"\bunresponsive\b", r"\bseizure\b", r"\bconvuls"],
        "This needs emergency care",
        "Call 911 now. Stay with the person until help arrives.",
        [_CALL_911],
    ),
    SafetyRule(
        "infant_fever", URGENT,
        [r"\b(newborn|infant|baby|(\d|one|two) ?(week|month)s? old)\b.*\bfever\b",
         r"\bfever\b.*\b(newborn|infant|baby)\b"],
        "A fever in a baby needs a clinician today",
        "For babies under 3 months, any fever of 100.4°F (38°C) or higher means calling your "
        "pediatrician now or going to the emergency department.",
        [_CALL_911],
    ),
    SafetyRule(
        "pregnancy", URGENT,
        [r"\bpregnan\w*\b.*\b(bleeding|severe pain|cramp|not moving|reduced movement)\b",
         r"\b(bleeding|severe pain)\b.*\bpregnan"],
        "Please contact your OB or labor and delivery today",
        "Bleeding, severe pain or reduced movement during pregnancy should be checked right away. "
        "If the bleeding is heavy or you feel faint, call 911.",
        [_CALL_911],
    ),
    SafetyRule(
        "high_fever", URGENT,
        [r"\bfever\b.*\b(10[3-9]|11\d)\b", r"\b(10[3-9]|11\d)(\.\d)? ?(°|degrees|f)\b",
         r"\bstiff neck\b.*\bfever\b", r"\bfever\b.*\bstiff neck\b"],
        "A high fever should be seen today",
        "Call the clinic for a same-day visit or go to urgent care. With a stiff neck, confusion "
        "or a spreading rash, go to the emergency department.",
        [],
    ),
]

_COMPILED = [(rule, [re.compile(p, re.IGNORECASE) for p in rule.patterns]) for rule in RULES]


def screen_message(text: str) -> Optional[dict]:
    """Return the first matching rule as a dict (with `level`, `title`,
    `guidance`, `contacts`, `rule_id`), or None."""
    if not text:
        return None
    for rule, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            out = asdict(rule)
            out.pop("patterns")
            out["rule_id"] = out.pop("id")
            return out
    return None


def is_emergency(text: str) -> bool:
    hit = screen_message(text)
    return bool(hit and hit["level"] == EMERGENCY)


DISCLAIMER = (
    "AegisTriage gives general information and helps with scheduling and refills. "
    "It isn't a doctor and can't diagnose. In an emergency, call 911."
)
