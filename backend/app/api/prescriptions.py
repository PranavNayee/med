"""Read a prescription and find the same medicines for less.

    POST /api/prescriptions/read    multipart `file`: photo or PDF -> lines + matches
    POST /api/prescriptions/match   JSON {"lines": [{name, strength, form}]} -> matches

`/match` needs no AI: it serves typed entries and lines the patient corrected
after a read. `/read` needs GEMINI_API_KEY and is capped per user per day,
because every call is billed.

The uploaded file is read in memory and discarded. Attaching a prescription
to an order for pharmacist verification is a separate step
(`/orders/<n>/prescription`), so nothing here stores health documents.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, current_app, g, jsonify, request

from ..extensions import db
from ..models import AuditLog
from ..security import audit, auth_required
from ..serializers import product_card, rupees, substitute
from ..services.prescription_match import LineMatch, match_line
from ..services.prescription_reader import (
    ACCEPTED_TYPES,
    PrescriptionReadError,
    read_prescription,
)
from ..services.substitution import SubstituteResult

bp = Blueprint("prescriptions", __name__, url_prefix="/api/prescriptions")

MAX_LINES = 30

NOTE = ("Cheaper options contain the same salts at the same strength and form. "
        "Check each medicine matches your prescription, and ask your doctor or "
        "pharmacist before switching brands.")


@bp.get("/status")
def status():
    cfg = current_app.config["NIROG"]
    return jsonify({"reader_available": bool(cfg.gemini_api_key),
                    "accepted_types": sorted(ACCEPTED_TYPES)})


@bp.post("/read")
@auth_required
def read():
    cfg = current_app.config["NIROG"]
    if not cfg.gemini_api_key:
        return jsonify({"error": "reader_unavailable",
                        "message": "Prescription reading is not switched on yet. "
                                   "You can type the medicines instead."}), 503

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "invalid_input",
                        "message": "Attach a photo or PDF of the prescription."}), 400
    mime_type = (upload.mimetype or "").lower()
    if mime_type not in ACCEPTED_TYPES:
        return jsonify({"error": "unsupported_file_type",
                        "message": "Upload a photo (JPG, PNG, WEBP, HEIC) or a PDF."}), 415

    since = datetime.utcnow() - timedelta(days=1)
    used = db.session.query(AuditLog).filter(
        AuditLog.actor_id == g.user_id,
        AuditLog.event == "PRESCRIPTION_READ",
        AuditLog.created_at >= since,
    ).count()
    if used >= cfg.prescription_reads_per_day:
        return jsonify({"error": "daily_limit_reached",
                        "message": "You've reached today's limit for reading "
                                   "prescriptions. Type the medicines instead, "
                                   "or try again tomorrow."}), 429

    data = upload.read()
    try:
        result = read_prescription(data, mime_type,
                                   api_key=cfg.gemini_api_key,
                                   model=cfg.prescription_reader_model,
                                   thinking_level=cfg.prescription_reader_thinking)
    except PrescriptionReadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 422
    finally:
        del data

    # Counts toward the daily cap; records that a read happened, never what
    # was on the prescription.
    audit("PRESCRIPTION_READ", subject_type="prescription",
          detail=f'{{"lines": {len(result.lines)}}}')
    db.session.commit()

    if not result.is_prescription:
        return jsonify({"error": "not_a_prescription",
                        "message": "This doesn't look like a prescription. "
                                   "Upload a photo of the doctor's prescription."}), 422

    return jsonify({
        "lines": [_line_payload(line.__dict__, match_line(line.name, line.strength, line.form))
                  for line in result.lines],
        "warnings": result.warnings,
        "note": NOTE,
    })


@bp.post("/match")
def match():
    body = request.get_json(silent=True) or {}
    raw_lines = body.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        return jsonify({"error": "invalid_input",
                        "message": "Send at least one medicine."}), 400

    lines = []
    for raw in raw_lines[:MAX_LINES]:
        if not isinstance(raw, dict):
            continue
        read_line = {k: " ".join(str(raw.get(k) or "").split())[:120]
                     for k in ("name", "strength", "form", "dosage", "duration")}
        if not read_line["name"]:
            continue
        read_line["confidence"] = "typed"
        lines.append(_line_payload(read_line, match_line(
            read_line["name"], read_line["strength"], read_line["form"])))

    if not lines:
        return jsonify({"error": "invalid_input",
                        "message": "Send at least one medicine name."}), 400
    return jsonify({"lines": lines, "warnings": [], "note": NOTE})


def _line_payload(read_line: dict, found: LineMatch) -> dict:
    options = []
    for option in found.options:
        if isinstance(option, SubstituteResult):
            options.append(substitute(option))
        else:
            options.append(product_card(option))

    saving = None
    if found.best_saving is not None:
        saving = {"amount": rupees(found.best_saving.saving_paise),
                  "percent": found.best_saving.saving_percent,
                  "product": product_card(found.best_saving.product)}

    return {
        "read": read_line,
        "basis": found.basis,
        "match": product_card(found.product) if found.product else None,
        "needs_review": found.needs_review or read_line.get("confidence") == "low",
        "candidates": [product_card(p) for p in found.candidates],
        "options": options,
        "best_saving": saving,
    }
