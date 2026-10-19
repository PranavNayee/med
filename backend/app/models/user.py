"""Accounts, family profiles and consent.

Health data is sensitive personal data under the DPDP Act 2023, so consent
is a first-class record with a purpose and a timestamp, not a checkbox
buried in a settings blob.
"""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    mobile = db.Column(db.String(15), nullable=False, unique=True, index=True)
    email = db.Column(db.String(200), unique=True)
    full_name = db.Column(db.String(160), nullable=False)
    password_hash = db.Column(db.String(255))

    abha_number = db.Column(db.String(20), unique=True)   # ABDM health ID, optional
    abha_linked_at = db.Column(db.DateTime)

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime)

    members = db.relationship("FamilyMember", back_populates="user",
                              cascade="all, delete-orphan")
    addresses = db.relationship("Address", back_populates="user",
                                cascade="all, delete-orphan")


class FamilyMember(db.Model):
    """Every clinical record hangs off a member, not off the account, so a
    parent ordering for a child never mixes the two histories."""

    __tablename__ = "family_members"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    full_name = db.Column(db.String(160), nullable=False)
    relation = db.Column(db.String(40), nullable=False)   # SELF/SPOUSE/SON/...
    date_of_birth = db.Column(db.Date)
    sex = db.Column(db.String(20))
    is_self = db.Column(db.Boolean, default=False, nullable=False)
    is_minor = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="members")


class Address(db.Model):
    __tablename__ = "addresses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    label = db.Column(db.String(40))
    recipient_name = db.Column(db.String(160))
    phone = db.Column(db.String(15))
    line1 = db.Column(db.String(255), nullable=False)
    line2 = db.Column(db.String(255))
    city = db.Column(db.String(80), nullable=False)
    state = db.Column(db.String(80), nullable=False)
    pincode = db.Column(db.String(10), nullable=False, index=True)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    # An address used by an order is archived, not deleted, so the order keeps
    # its foreign key. Archived addresses are hidden from the address book.
    is_archived = db.Column(db.Boolean, default=False, nullable=False)

    user = db.relationship("User", back_populates="addresses")

    def one_line(self) -> str:
        parts = [self.recipient_name, self.line1, self.line2,
                 f"{self.city}, {self.state} {self.pincode}", self.phone]
        return ", ".join(p for p in parts if p)


class Consent(db.Model):
    """One row per purpose. Withdrawal is recorded, never deleted — the audit
    trail has to show that consent existed at the time data was processed."""

    __tablename__ = "consents"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    purpose = db.Column(db.String(80), nullable=False)
    # e.g. ORDER_FULFILMENT, TELECONSULT_RECORD, LAB_REPORT_STORAGE, MARKETING
    granted = db.Column(db.Boolean, nullable=False)
    granted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    withdrawn_at = db.Column(db.DateTime)
    policy_version = db.Column(db.String(20), nullable=False)

    __table_args__ = (db.Index("ix_consent_lookup", "user_id", "purpose"),)


class AuditLog(db.Model):
    """Append-only. Anything that touches health data or money lands here."""

    __tablename__ = "audit_log"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    correlation_id = db.Column(db.String(36), index=True, nullable=False)
    actor_type = db.Column(db.String(20), nullable=False)   # USER/PHARMACIST/DOCTOR/SYSTEM
    actor_id = db.Column(db.Integer)
    event = db.Column(db.String(60), nullable=False, index=True)
    subject_type = db.Column(db.String(40))
    subject_id = db.Column(db.String(64))
    detail = db.Column(db.Text)      # JSON, never contains raw credentials
    ip_hash = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)
