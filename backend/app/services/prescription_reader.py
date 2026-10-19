"""Read the medicines off a prescription image with Gemini.

This module only transcribes. It never decides what a patient should take:
the model is told to copy what the prescriber wrote and to mark anything it
cannot read with confidence, and the caller matches those lines against the
catalogue and shows the patient what was read so they can correct it.

Handwriting is the risk. "Losar" and "Lasix" differ by two strokes and treat
different conditions, so a low-confidence line is surfaced as uncertain rather
than silently resolved to the nearest brand.

The image is sent with `store=False` and is not written to disk here.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
DOCUMENT_TYPES = {"application/pdf"}
ACCEPTED_TYPES = IMAGE_TYPES | DOCUMENT_TYPES

CONFIDENCE = ("high", "medium", "low")

SYSTEM_INSTRUCTION = """\
You transcribe Indian medical prescriptions for a pharmacy.

Copy each prescribed medicine exactly as the prescriber wrote it. Do not
correct, substitute, recommend or add medicines, and do not infer a medicine
from a diagnosis. If a word is ambiguous, give your best reading and set
confidence to "low"; never pick a more common drug name because it looks
similar.

For each medicine give: the name as written (brand or generic), the strength
if written (e.g. "500 mg", "625", "10mg/5ml"), the form if written (tablet,
capsule, syrup, injection, drops, cream, inhaler...), the dosage instruction
as written (e.g. "1-0-1", "BD", "SOS", "after food"), and the duration if
written ("5 days"). Leave a field empty when it is not on the prescription.

Set is_prescription to false if the image is not a medical prescription.
Report problems such as blur, cropping or an unreadable section in warnings.
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_prescription": {"type": "boolean"},
        "medicines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "strength": {"type": "string"},
                    "form": {"type": "string"},
                    "dosage": {"type": "string"},
                    "duration": {"type": "string"},
                    "confidence": {"type": "string", "enum": list(CONFIDENCE)},
                },
                "required": ["name", "strength", "form", "dosage",
                             "duration", "confidence"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["is_prescription", "medicines", "warnings"],
}


class PrescriptionReadError(RuntimeError):
    """The prescription could not be read. `code` is safe to return to clients."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ReadLine:
    name: str
    strength: str = ""
    form: str = ""
    dosage: str = ""
    duration: str = ""
    confidence: str = "low"


@dataclass
class ReadResult:
    is_prescription: bool
    lines: list[ReadLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read_prescription(data: bytes, mime_type: str, *, api_key: str,
                      model: str, thinking_level: str = "low",
                      timeout: float = 90.0) -> ReadResult:
    if mime_type not in ACCEPTED_TYPES:
        raise PrescriptionReadError(
            "unsupported_file_type",
            "Upload a photo (JPG, PNG, WEBP, HEIC) or a PDF of the prescription.")

    from google import genai  # imported lazily: optional at runtime

    part_type = "document" if mime_type in DOCUMENT_TYPES else "image"
    client = genai.Client(api_key=api_key)
    try:
        interaction = client.interactions.create(
            model=model,
            system_instruction=SYSTEM_INSTRUCTION,
            input=[
                {"type": "text",
                 "text": "Transcribe the medicines on this prescription."},
                {"type": part_type,
                 "data": base64.b64encode(data).decode("ascii"),
                 "mime_type": mime_type},
            ],
            # The MIME type goes inside response_format only: also sending
            # response_mime_type makes the API reject the request (400).
            response_format={"type": "text", "mime_type": "application/json",
                             "schema": RESPONSE_SCHEMA},
            # "low" read a typed test prescription as accurately as the
            # default and in 11 s instead of 73 s.
            generation_config={"thinking_level": thinking_level},
            store=False,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - the SDK's error classes are private
        log.warning("gemini request failed: %s", exc.__class__.__name__)
        raise PrescriptionReadError(
            "reader_unavailable",
            "The prescription reader is unavailable right now. "
            "Try again, or search for the medicines by name.") from exc

    if interaction.status != "completed" or not interaction.output_text:
        log.warning("gemini interaction ended with status %s", interaction.status)
        raise PrescriptionReadError(
            "unreadable", "We could not read this prescription. "
            "Try a sharper, well-lit photo of the whole page.")

    return parse_response(interaction.output_text)


def parse_response(text: str) -> ReadResult:
    """Validate the model's JSON. Anything malformed is dropped, not guessed."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise PrescriptionReadError(
            "unreadable", "We could not read this prescription.") from exc
    if not isinstance(payload, dict):
        raise PrescriptionReadError("unreadable", "We could not read this prescription.")

    lines: list[ReadLine] = []
    for item in payload.get("medicines") or []:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"), 120)
        if not name:
            continue
        confidence = str(item.get("confidence") or "").lower()
        lines.append(ReadLine(
            name=name,
            strength=_clean(item.get("strength"), 60),
            form=_clean(item.get("form"), 40),
            dosage=_clean(item.get("dosage"), 120),
            duration=_clean(item.get("duration"), 60),
            confidence=confidence if confidence in CONFIDENCE else "low",
        ))

    warnings = [_clean(w, 200) for w in payload.get("warnings") or []
                if isinstance(w, str) and w.strip()]
    return ReadResult(
        is_prescription=bool(payload.get("is_prescription", True)),
        lines=lines[:30],
        warnings=warnings[:10],
    )


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]
