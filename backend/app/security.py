"""Authentication and request-scoped identity."""
from __future__ import annotations

import functools
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from flask import current_app, g, jsonify, request

from .extensions import db
from .models import AuditLog


def hash_password(password: str) -> str:
    """PBKDF2. Swap for argon2/bcrypt in production — kept dependency-free here."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"pbkdf2_sha256$260000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def issue_token(user_id: int) -> str:
    cfg = current_app.config["NIROG"]
    payload = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=cfg.jwt_ttl_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, cfg.secret_key, algorithm="HS256")


def decode_token(token: str) -> int | None:
    cfg = current_app.config["NIROG"]
    try:
        payload = jwt.decode(token, cfg.secret_key, algorithms=["HS256"])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def auth_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "authentication_required"}), 401
        user_id = decode_token(header[7:])
        if user_id is None:
            return jsonify({"error": "invalid_token"}), 401
        g.user_id = user_id
        return view(*args, **kwargs)

    return wrapper


def audit(event: str, *, subject_type: str = "", subject_id: str = "",
          detail: str = "", actor_type: str = "USER") -> None:
    """Write an audit row. Never called with raw credentials in `detail`."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    entry = AuditLog(
        correlation_id=getattr(g, "correlation_id", uuid.uuid4().hex),
        actor_type=actor_type,
        actor_id=getattr(g, "user_id", None),
        event=event,
        subject_type=subject_type or None,
        subject_id=str(subject_id) or None,
        detail=detail or None,
        ip_hash=hashlib.sha256(ip.encode()).hexdigest()[:64] if ip else None,
    )
    db.session.add(entry)
