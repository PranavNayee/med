"""Doctors, consultations, diagnostics and the health-record timeline."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class Specialty(db.Model):
    __tablename__ = "specialties"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    icon = db.Column(db.String(40))
    accent = db.Column(db.String(20))


class Doctor(db.Model):
    """Registration number is mandatory. Telemedicine Practice Guidelines
    require a Registered Medical Practitioner, so an unverified doctor row
    must never become bookable."""

    __tablename__ = "doctors"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(160), nullable=False)
    specialty_id = db.Column(db.Integer, db.ForeignKey("specialties.id"), nullable=False)
    qualification = db.Column(db.String(200), nullable=False)

    nmc_registration_number = db.Column(db.String(80), nullable=False, unique=True)
    registration_council = db.Column(db.String(120))
    registration_verified_at = db.Column(db.DateTime)

    experience_years = db.Column(db.Integer)
    languages = db.Column(db.String(240))   # JSON array
    affiliation = db.Column(db.String(200))
    about = db.Column(db.Text)

    consultation_fee_paise = db.Column(db.Integer, nullable=False)
    rating = db.Column(db.Numeric(2, 1))
    review_count = db.Column(db.Integer, default=0)
    is_online = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=False, nullable=False)

    specialty = db.relationship("Specialty")

    @property
    def is_bookable(self) -> bool:
        return bool(self.is_active and self.registration_verified_at)


class DoctorSlot(db.Model):
    __tablename__ = "doctor_slots"

    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id"), nullable=False)
    starts_at = db.Column(db.DateTime, nullable=False, index=True)
    duration_minutes = db.Column(db.Integer, default=15, nullable=False)
    is_booked = db.Column(db.Boolean, default=False, nullable=False)

    doctor = db.relationship("Doctor")


class Consultation(db.Model):
    __tablename__ = "consultations"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(24), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("family_members.id"))
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id"), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey("doctor_slots.id"))

    mode = db.Column(db.String(10), nullable=False)   # VIDEO / AUDIO / CHAT
    status = db.Column(db.String(20), default="SCHEDULED", nullable=False)
    chief_complaint = db.Column(db.Text)
    fee_paise = db.Column(db.Integer, nullable=False)

    started_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)
    follow_up_until = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    doctor = db.relationship("Doctor")


class LabTest(db.Model):
    __tablename__ = "lab_tests"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(200), nullable=False)
    parameter_count = db.Column(db.Integer, default=1, nullable=False)
    sample_type = db.Column(db.String(60))
    fasting_required = db.Column(db.Boolean, default=False, nullable=False)
    turnaround_hours = db.Column(db.Integer)
    price_paise = db.Column(db.Integer, nullable=False)
    mrp_paise = db.Column(db.Integer, nullable=False)
    is_package = db.Column(db.Boolean, default=False, nullable=False)
    audience = db.Column(db.String(120))
    tag = db.Column(db.String(60))
    nabl_lab_name = db.Column(db.String(160))
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class LabBooking(db.Model):
    __tablename__ = "lab_bookings"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(24), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("family_members.id"))
    lab_test_id = db.Column(db.Integer, db.ForeignKey("lab_tests.id"), nullable=False)
    address_id = db.Column(db.Integer, db.ForeignKey("addresses.id"))

    scheduled_for = db.Column(db.DateTime)
    status = db.Column(db.String(30), default="CONFIRMED", nullable=False)
    phlebotomist_name = db.Column(db.String(160))
    amount_paise = db.Column(db.Integer, nullable=False)
    # Snapshots, so the booking reads correctly even if the address or
    # profile changes later.
    patient_name = db.Column(db.String(160))
    address_text = db.Column(db.Text)
    payment_method = db.Column(db.String(20), default="PAY_AT_COLLECTION", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    lab_test = db.relationship("LabTest")


class HealthRecord(db.Model):
    """The timeline. Files live in object storage; only the key is stored."""

    __tablename__ = "health_records"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("family_members.id"),
                          nullable=False, index=True)

    record_type = db.Column(db.String(30), nullable=False)  # CONSULT/LAB/RX/RADIOLOGY/VACCINE
    title = db.Column(db.String(240), nullable=False)
    issued_by = db.Column(db.String(200))
    record_date = db.Column(db.Date, nullable=False, index=True)

    file_key = db.Column(db.String(500))
    file_mime = db.Column(db.String(80))
    source = db.Column(db.String(40), default="UPLOAD", nullable=False)  # UPLOAD/NIROG/ABDM

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class VitalReading(db.Model):
    """Powers the trend charts. One row per measurement, never an aggregate."""

    __tablename__ = "vital_readings"

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("family_members.id"),
                          nullable=False, index=True)
    metric = db.Column(db.String(40), nullable=False, index=True)  # HBA1C/BP_SYS/WEIGHT
    value = db.Column(db.Numeric(10, 3), nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    measured_on = db.Column(db.Date, nullable=False, index=True)
    source = db.Column(db.String(40), default="MANUAL", nullable=False)

    __table_args__ = (db.Index("ix_vitals_series", "member_id", "metric", "measured_on"),)


class MedicationReminder(db.Model):
    __tablename__ = "medication_reminders"

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("family_members.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"))
    label = db.Column(db.String(200), nullable=False)
    dose_instruction = db.Column(db.String(200))
    time_of_day = db.Column(db.String(10), nullable=False)   # "08:00"
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class ReminderLog(db.Model):
    __tablename__ = "reminder_logs"

    id = db.Column(db.Integer, primary_key=True)
    reminder_id = db.Column(db.Integer, db.ForeignKey("medication_reminders.id"),
                            nullable=False)
    due_on = db.Column(db.Date, nullable=False)
    taken_at = db.Column(db.DateTime)

    __table_args__ = (db.UniqueConstraint("reminder_id", "due_on", name="uq_reminder_day"),)


class EmergencyContact(db.Model):
    __tablename__ = "emergency_contacts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    relation = db.Column(db.String(60))
    mobile = db.Column(db.String(15), nullable=False)
    priority = db.Column(db.Integer, default=1, nullable=False)
