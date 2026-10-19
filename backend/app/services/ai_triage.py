"""Symptom guidance ("Nirog AI").

Design rule: the language model is the *last* thing consulted and has the
least authority. Deterministic rules run first and can end the interaction
on their own. The model can only ever produce general information and a
recommended level of care — it cannot diagnose, cannot name a medicine,
and cannot downgrade an escalation the rules have already raised.

Two escalation paths are handled before anything else:

  EMERGENCY  possible medical emergency -> show emergency guidance and
             stop. No chat continuation, no differential, no reassurance.
  CRISIS     possible self-harm or suicidal ideation -> show human support
             options immediately. This path never reaches the model and
             never produces an AI-written reply.

Both lists are intentionally broad. A false positive costs a user one
extra tap; a false negative can cost far more.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class TriageLevel(str, Enum):
    CRISIS = "CRISIS"
    EMERGENCY = "EMERGENCY"
    URGENT = "URGENT"          # see a doctor today
    ROUTINE = "ROUTINE"        # consult when convenient
    SELF_CARE = "SELF_CARE"


#: Possible medical emergency. Matched case-insensitively on word boundaries.
EMERGENCY_PATTERNS = [
    r"chest pain", r"crushing.{0,12}chest", r"pain.{0,15}(left arm|jaw)",
    r"can.?t breathe", r"cannot breathe", r"struggling to breathe",
    r"breathless(ness)? at rest", r"blue lips", r"gasping",
    r"unconscious", r"unresponsive", r"passed out", r"fainted",
    r"seizure", r"fit(s)? (with|and) (jerking|frothing)", r"convulsion",
    r"stroke", r"face droop", r"slurred speech", r"sudden weakness on one side",
    r"heavy bleeding", r"bleeding (that )?won.?t stop", r"vomiting blood",
    r"coughing (up )?blood", r"black tarry stool",
    r"severe abdominal pain", r"rigid abdomen",
    r"stiff neck (with|and) fever", r"worst headache",
    r"poison(ing)?", r"overdose", r"swallowed .{0,20}(tablets|chemical|acid)",
    r"snake ?bite", r"severe burn", r"electric shock", r"drowning",
    r"not moving", r"baby.{0,20}(not feeding|floppy|blue)",
    r"labou?r pain", r"water broke", r"bleeding in pregnancy",
]

#: Possible self-harm or suicidal ideation.
CRISIS_PATTERNS = [
    r"kill myself", r"end my life", r"take my (own )?life", r"want to die",
    r"suicid", r"self.?harm", r"hurt myself", r"cut myself", r"cutting myself",
    r"no reason to live", r"better off dead", r"don.?t want to (live|be here)",
    r"ending it all", r"can.?t go on",
]

_EMERGENCY_RE = [re.compile(p, re.IGNORECASE) for p in EMERGENCY_PATTERNS]
_CRISIS_RE = [re.compile(p, re.IGNORECASE) for p in CRISIS_PATTERNS]


@dataclass
class TriageResult:
    level: TriageLevel
    message: str
    actions: list[dict] = field(default_factory=list)
    allow_model_response: bool = False
    matched_rule: str | None = None
    disclaimer: str = (
        "Nirog AI gives general health information only. It does not diagnose "
        "or prescribe. For diagnosis and treatment, always consult a licensed "
        "doctor."
    )


def _first_match(text: str, patterns: list[re.Pattern]) -> str | None:
    for pattern in patterns:
        if pattern.search(text):
            return pattern.pattern
    return None


def screen(user_text: str) -> TriageResult:
    """Run deterministic screening. Always call this before any model call."""
    text = (user_text or "").strip()

    crisis = _first_match(text, _CRISIS_RE)
    if crisis:
        return TriageResult(
            level=TriageLevel.CRISIS,
            matched_rule=crisis,
            allow_model_response=False,
            message=(
                "It sounds like you're going through something very painful, and "
                "I'm glad you said something. I'm not able to help with this the "
                "way a person can — please talk to someone who can support you "
                "properly, right now."
            ),
            actions=[
                {"type": "helpline", "label": "Tele-MANAS (24×7, free)",
                 "value": "14416", "primary": True},
                {"type": "helpline", "label": "KIRAN mental health helpline",
                 "value": "1800-599-0019"},
                {"type": "consult", "label": "Talk to a psychologist on Nirog",
                 "specialty": "psych"},
                {"type": "contact", "label": "Call someone you trust"},
            ],
        )

    emergency = _first_match(text, _EMERGENCY_RE)
    if emergency:
        return TriageResult(
            level=TriageLevel.EMERGENCY,
            matched_rule=emergency,
            allow_model_response=False,
            message=(
                "What you've described may need emergency medical care. Please "
                "don't wait for an online answer — call an ambulance or get to "
                "the nearest emergency department now."
            ),
            actions=[
                {"type": "call", "label": "Ambulance · 108", "value": "108",
                 "primary": True},
                {"type": "navigate", "label": "Nearest hospitals", "route": "emergency"},
                {"type": "contact", "label": "Alert my emergency contacts"},
            ],
        )

    return TriageResult(
        level=TriageLevel.ROUTINE,
        allow_model_response=True,
        message="",
    )


#: Appended to every model call. The model is told what it may not do in the
#: same terms the code enforces.
SYSTEM_PROMPT = """\
You are a health information assistant inside an Indian consumer health app.

You MUST NOT:
- give a diagnosis, or say what condition someone "has" or "probably has"
- name, recommend, suggest a dose for, or compare any medicine, including
  over-the-counter ones
- interpret a specific lab value, scan or report as normal or abnormal
- tell anyone it is safe to wait, or to avoid seeing a doctor
- claim to be a doctor, or imply your answer replaces one

You MAY:
- explain in plain language what a symptom generally means
- describe what usually happens at a consultation for that symptom
- list warning signs that mean someone should be seen urgently
- suggest which kind of specialist normally handles this

Always end by recommending an appropriate level of care. If anything in the
message suggests an emergency or thoughts of self-harm, respond only by
urging immediate human help — nothing else.

Answer in the user's language. Keep it under 150 words. Be warm and plain.
Never use frightening language, and never offer false reassurance.
"""


def build_model_request(user_text: str, history: list[dict] | None = None) -> dict:
    """Assemble the payload for the model. Screening must have passed first."""
    result = screen(user_text)
    if not result.allow_model_response:
        raise ValueError("build_model_request called on a screened-out message")

    messages = list(history or [])
    messages.append({"role": "user", "content": user_text})
    return {"system": SYSTEM_PROMPT, "messages": messages, "max_tokens": 400}


#: Post-checks on model output. If any fires, the reply is discarded and the
#: user gets a safe fallback instead of a partially-scrubbed answer.
_OUTPUT_BLOCKERS = [
    re.compile(r"\byou (probably |likely |most likely )?have\b", re.I),
    re.compile(r"\b(diagnos(is|ed|e)|you are suffering from)\b", re.I),
    re.compile(r"\btake \d+\s?(mg|ml|tablet|tablets|capsule)", re.I),
    re.compile(r"\b(no need|not necessary) to (see|consult|visit) a doctor\b", re.I),
]

SAFE_FALLBACK = (
    "I'm not able to give a reliable answer on that one. The safest next step "
    "is a short consultation with a doctor, who can ask the follow-up questions "
    "this needs."
)


def validate_model_output(text: str) -> tuple[bool, str]:
    """Return (is_safe, text_to_show)."""
    for blocker in _OUTPUT_BLOCKERS:
        if blocker.search(text or ""):
            return False, SAFE_FALLBACK
    return True, text
