"""Doctors, diagnostics, records and the AI assistant."""
from __future__ import annotations

from collections import defaultdict

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import select

from ..extensions import db
from ..models import (
    Doctor,
    EmergencyContact,
    HealthRecord,
    LabTest,
    MedicationReminder,
    ReminderLog,
    Specialty,
    VitalReading,
)
from ..security import audit, auth_required
from ..serializers import doctor_card, health_record, lab_test
from ..services.ai_triage import TriageLevel, screen

bp = Blueprint("clinical", __name__, url_prefix="/api")


# ---------------------------------------------------------------- doctors

@bp.get("/specialties")
def specialties():
    rows = db.session.execute(select(Specialty)).scalars().all()
    counts = dict(db.session.execute(
        select(Doctor.specialty_id, db.func.count(Doctor.id))
        .where(Doctor.is_active.is_(True)).group_by(Doctor.specialty_id)
    ).all())
    return jsonify({"items": [
        {"id": s.code, "name": s.name, "icon": s.icon, "color": s.accent,
         "count": counts.get(s.id, 0)}
        for s in rows
    ]})


@bp.get("/doctors")
def doctors():
    """Only verified, active doctors are ever listed. An unverified row in the
    table is invisible to patients rather than shown as 'pending'."""
    spec = (request.args.get("specialty") or "").strip()
    stmt = select(Doctor).where(
        Doctor.is_active.is_(True),
        Doctor.registration_verified_at.isnot(None),
    )
    if spec and spec != "all":
        stmt = stmt.join(Specialty).where(Specialty.code == spec)

    rows = db.session.execute(stmt.order_by(Doctor.rating.desc())).scalars().all()
    return jsonify({"items": [doctor_card(d) for d in rows]})


@bp.get("/doctors/<int:doctor_id>")
def doctor_detail(doctor_id: int):
    doctor = db.session.get(Doctor, doctor_id)
    if doctor is None or not doctor.is_bookable:
        return jsonify({"error": "not_found"}), 404
    return jsonify(doctor_card(doctor))


@bp.post("/consultations")
@auth_required
def book_consultation():
    cfg = current_app.config["NIROG"]
    if not cfg.enable_teleconsult:
        return jsonify({
            "error": "TELECONSULT_DISABLED",
            "message": "Teleconsultation is not enabled on this deployment. It "
                       "requires signed agreements with registered practitioners "
                       "and compliance with the Telemedicine Practice Guidelines.",
        }), 503
    return jsonify({"error": "not_implemented"}), 501


# ---------------------------------------------------------------- labs

@bp.get("/lab-tests")
def lab_tests():
    packages = request.args.get("packages", type=int)
    stmt = select(LabTest).where(LabTest.is_active.is_(True))
    if packages is not None:
        stmt = stmt.where(LabTest.is_package.is_(bool(packages)))
    rows = db.session.execute(stmt.order_by(LabTest.price_paise.asc())).scalars().all()
    return jsonify({"items": [lab_test(t) for t in rows]})


# ---------------------------------------------------------------- records

@bp.get("/members/<int:member_id>/records")
@auth_required
def member_records(member_id: int):
    if not _owns_member(member_id):
        return jsonify({"error": "forbidden"}), 403

    rows = db.session.execute(
        select(HealthRecord)
        .where(HealthRecord.member_id == member_id)
        .order_by(HealthRecord.record_date.desc())
    ).scalars().all()

    audit("HEALTH_RECORDS_VIEWED", subject_type="member", subject_id=member_id)
    db.session.commit()

    counts: dict[str, int] = defaultdict(int)
    for record in rows:
        counts[record.record_type.lower()] += 1

    return jsonify({
        "items": [health_record(r) for r in rows],
        "counts": {"total": len(rows), **counts},
    })


@bp.get("/members/<int:member_id>/vitals")
@auth_required
def member_vitals(member_id: int):
    if not _owns_member(member_id):
        return jsonify({"error": "forbidden"}), 403

    rows = db.session.execute(
        select(VitalReading)
        .where(VitalReading.member_id == member_id)
        .order_by(VitalReading.measured_on.asc())
    ).scalars().all()

    series: dict[str, dict] = {}
    for reading in rows:
        entry = series.setdefault(reading.metric.lower(), {
            "label": reading.metric, "unit": reading.unit, "data": [],
        })
        entry["data"].append({
            "d": reading.measured_on.strftime("%b"),
            "v": float(reading.value),
        })

    return jsonify({"series": series})


@bp.get("/members/<int:member_id>/reminders")
@auth_required
def member_reminders(member_id: int):
    if not _owns_member(member_id):
        return jsonify({"error": "forbidden"}), 403

    from datetime import date
    rows = db.session.execute(
        select(MedicationReminder).where(
            MedicationReminder.member_id == member_id,
            MedicationReminder.is_active.is_(True),
        ).order_by(MedicationReminder.time_of_day.asc())
    ).scalars().all()

    today = date.today()
    taken_ids = {
        log.reminder_id for log in db.session.execute(
            select(ReminderLog).where(ReminderLog.due_on == today,
                                      ReminderLog.taken_at.isnot(None))
        ).scalars().all()
    }

    return jsonify({"items": [
        {"id": r.id, "t": r.label, "d": r.dose_instruction,
         "time": r.time_of_day, "taken": r.id in taken_ids}
        for r in rows
    ]})


def _owns_member(member_id: int) -> bool:
    from ..models import FamilyMember
    member = db.session.get(FamilyMember, member_id)
    return member is not None and member.user_id == g.user_id


# ---------------------------------------------------------------- AI assistant

@bp.post("/ai/triage")
def ai_triage():
    """Symptom guidance.

    Deterministic screening runs first and can end the exchange on its own.
    The model is only consulted for messages that clear screening, and its
    output is validated before it is returned.
    """
    cfg = current_app.config["NIROG"]
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "message_required"}), 400

    result = screen(message)

    if result.level in {TriageLevel.CRISIS, TriageLevel.EMERGENCY}:
        # Escalations are logged without the message body — the fact of the
        # escalation is operationally necessary; the content is not.
        audit(f"AI_TRIAGE_{result.level.value}", actor_type="SYSTEM",
              detail=f'{{"rule":"{result.matched_rule}"}}')
        db.session.commit()
        return jsonify({
            "level": result.level.value,
            "message": result.message,
            "actions": result.actions,
            "model_used": False,
            "disclaimer": result.disclaimer,
        })

    if not cfg.enable_ai_triage or cfg.ai_provider == "none":
        return jsonify({
            "level": TriageLevel.ROUTINE.value,
            "message": "I can't answer symptom questions on this deployment yet. "
                       "A short consultation with a doctor is the reliable next step.",
            "actions": [{"type": "consult", "label": "Consult a doctor",
                         "specialty": "gp", "primary": True}],
            "model_used": False,
            "disclaimer": result.disclaimer,
        })

    # Provider call goes here. Deliberately not wired to a live model in this
    # build — see docs/AI_SAFETY.md for the required review before enabling.
    return jsonify({
        "error": "AI_PROVIDER_NOT_CONFIGURED",
        "message": "Set AI_PROVIDER and complete the clinical review in "
                   "docs/AI_SAFETY.md before enabling model responses.",
    }), 501


# ---------------------------------------------------------------- emergency

@bp.get("/emergency")
@auth_required
def emergency():
    """Emergency screen data.

    This endpoint does NOT dispatch an ambulance. Promising dispatch the
    system cannot deliver is dangerous, so the app surfaces 108 and the
    user's own contacts, and says plainly what it does and does not do.
    """
    contacts = db.session.execute(
        select(EmergencyContact).where(EmergencyContact.user_id == g.user_id)
        .order_by(EmergencyContact.priority.asc())
    ).scalars().all()

    return jsonify({
        "primary_helpline": {"label": "Ambulance · 108", "number": "108"},
        "capabilities": {
            "shares_live_location_with_contacts": True,
            "books_ambulance": False,
            "notifies_hospital": False,
        },
        "notice": "Nirog alerts your saved contacts and shows nearby hospitals. "
                  "It does not book an ambulance for you — call 108.",
        "contacts": [
            {"name": c.name, "relation": c.relation,
             "mobile": c.mobile[-4:].rjust(len(c.mobile), "*")}
            for c in contacts
        ],
    })
