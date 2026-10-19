"""Home sample-collection bookings.

    GET  /api/lab-slots                   bookable slots (IST), with availability
    POST /api/lab-bookings                {test_id, address_id, member_id?, slot}
    GET  /api/lab-bookings                the user's bookings
    POST /api/lab-bookings/<ref>/cancel   up to CANCEL_CUTOFF before the slot

Slots are generated here rather than stored: morning collection windows for
the next few days, each with a fixed capacity. A booking is BOOKED until a
partner lab confirms it; nothing here claims a phlebotomist is assigned.
"""
from __future__ import annotations

import secrets
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select

from ..extensions import db
from ..models import Address, FamilyMember, LabBooking, LabTest
from ..security import audit, auth_required
from ..serializers import lab_test, rupees

bp = Blueprint("labs", __name__, url_prefix="/api")

IST = ZoneInfo("Asia/Kolkata")
SLOT_HOURS = (7, 8, 9, 10, 11, 17, 18)   # collection windows start (local time)
DAYS_AHEAD = 3
SLOT_CAPACITY = 6
LEAD_TIME = timedelta(hours=3)            # earliest bookable slot from now
CANCEL_CUTOFF = timedelta(hours=2)
ACTIVE = ("BOOKED", "CONFIRMED")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _slot_starts() -> list[datetime]:
    """Upcoming slot starts, as naive UTC (how the DB stores them)."""
    now_local = datetime.now(IST)
    earliest = now_local + LEAD_TIME
    out = []
    for day in range(DAYS_AHEAD + 1):
        date = (now_local + timedelta(days=day)).date()
        for hour in SLOT_HOURS:
            start = datetime.combine(date, time(hour), tzinfo=IST)
            if start >= earliest:
                out.append(start.astimezone(timezone.utc).replace(tzinfo=None))
    return out


def _booked_counts(starts: list[datetime]) -> dict[datetime, int]:
    if not starts:
        return {}
    rows = db.session.execute(
        select(LabBooking.scheduled_for, func.count(LabBooking.id))
        .where(LabBooking.scheduled_for.in_(starts), LabBooking.status.in_(ACTIVE))
        .group_by(LabBooking.scheduled_for)
    ).all()
    return {start: n for start, n in rows}


def _iso(dt_utc: datetime) -> str:
    return dt_utc.replace(tzinfo=timezone.utc).isoformat()


def _label(dt_utc: datetime) -> str:
    local = dt_utc.replace(tzinfo=timezone.utc).astimezone(IST)
    end = local + timedelta(hours=1)
    return f"{local:%a %d %b}, {local:%-I}–{end:%-I %p}"


@bp.get("/lab-slots")
def lab_slots():
    starts = _slot_starts()
    booked = _booked_counts(starts)
    return jsonify({"items": [
        {"slot": _iso(s), "label": _label(s),
         "available": booked.get(s, 0) < SLOT_CAPACITY}
        for s in starts
    ]})


def _booking_payload(b: LabBooking) -> dict:
    upcoming = b.scheduled_for and b.scheduled_for - _now_utc() > CANCEL_CUTOFF
    return {
        "reference": b.reference,
        "test": lab_test(b.lab_test) if b.lab_test else None,
        "slot": _iso(b.scheduled_for) if b.scheduled_for else None,
        "slot_label": _label(b.scheduled_for) if b.scheduled_for else None,
        "status": b.status,
        "amount": rupees(b.amount_paise),
        "patient": b.patient_name,
        "address": b.address_text,
        "payment_method": b.payment_method,
        "can_cancel": b.status in ACTIVE and bool(upcoming),
        "created_at": _iso(b.created_at),
    }


@bp.post("/lab-bookings")
@auth_required
def create_booking():
    data = request.get_json(silent=True) or {}
    test = db.session.execute(
        select(LabTest).where(LabTest.code == str(data.get("test_id") or ""),
                              LabTest.is_active.is_(True))
    ).scalar_one_or_none()
    if test is None:
        return jsonify({"error": "not_found", "message": "That test isn't available."}), 404

    try:
        address = db.session.execute(select(Address).where(
            Address.id == int(data.get("address_id")), Address.user_id == g.user_id,
            Address.is_archived.is_(False))).scalar_one_or_none()
    except (TypeError, ValueError):
        address = None
    if address is None:
        return jsonify({"error": "address_required",
                        "message": "Choose the address for sample collection."}), 422

    member = None
    raw_member = str(data.get("member_id") or "").lstrip("f")
    if raw_member.isdigit():
        member = db.session.execute(select(FamilyMember).where(
            FamilyMember.id == int(raw_member), FamilyMember.user_id == g.user_id)
        ).scalar_one_or_none()
    if member is None:
        member = db.session.execute(select(FamilyMember).where(
            FamilyMember.user_id == g.user_id, FamilyMember.is_self.is_(True))
        ).scalars().first()

    try:
        slot = datetime.fromisoformat(str(data.get("slot"))).astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        slot = None
    if slot not in _slot_starts():
        return jsonify({"error": "slot_unavailable",
                        "message": "That slot is no longer available. Pick another."}), 409
    if _booked_counts([slot]).get(slot, 0) >= SLOT_CAPACITY:
        return jsonify({"error": "slot_full",
                        "message": "That slot just filled up. Pick another."}), 409

    booking = LabBooking(
        reference="LAB" + secrets.token_hex(4).upper(),
        user_id=g.user_id, member_id=member.id if member else None,
        lab_test_id=test.id, address_id=address.id, scheduled_for=slot,
        status="BOOKED", amount_paise=test.price_paise,
        patient_name=member.full_name if member else None,
        address_text=address.one_line(),
    )
    db.session.add(booking)
    db.session.flush()
    audit("LAB_BOOKED", subject_type="lab_booking", subject_id=booking.reference)
    db.session.commit()
    return jsonify(_booking_payload(booking)), 201


@bp.get("/lab-bookings")
@auth_required
def list_bookings():
    rows = db.session.execute(
        select(LabBooking).where(LabBooking.user_id == g.user_id)
        .order_by(LabBooking.scheduled_for.desc()).limit(50)
    ).scalars().all()
    return jsonify({"items": [_booking_payload(b) for b in rows]})


@bp.post("/lab-bookings/<reference>/cancel")
@auth_required
def cancel_booking(reference: str):
    booking = db.session.execute(select(LabBooking).where(
        LabBooking.reference == reference, LabBooking.user_id == g.user_id)
    ).scalar_one_or_none()
    if booking is None:
        return jsonify({"error": "not_found"}), 404
    if not _booking_payload(booking)["can_cancel"]:
        return jsonify({"error": "not_cancellable",
                        "message": "This booking can't be cancelled any more."}), 409
    booking.status = "CANCELLED"
    audit("LAB_CANCELLED", subject_type="lab_booking", subject_id=reference)
    db.session.commit()
    return jsonify(_booking_payload(booking))
