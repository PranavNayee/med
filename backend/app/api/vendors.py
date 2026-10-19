"""Vendor status endpoints.

GET /api/vendors                          → list of {name, configured, mode, enabled_features, last_health}
GET /api/vendors/<name>/health            → {ok, mode, latency_ms, detail}  (admin only)
"""

from __future__ import annotations
import hmac

import time
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select

from ..config import Config, _flag
from ..extensions import db
from ..models import AuditLog, Order, OrderStatus
from ..services.vendors import get, register
from ..services.vendors.http import get as http_get, RateLimiter
from ..security import audit

bp = Blueprint("vendors", __name__, url_prefix="/api/vendors")

# ---------------------------------------------------------------------------
# In-memory per-vendor state (last health check time, rate limiters, etc.)
# ---------------------------------------------------------------------------
_vendor_health: dict[str, dict] = {}
_vendor_rate_limiters: dict[str, RateLimiter] = {}

# ---------------------------------------------------------------------------
# Helper: run one health check for a vendor
# ---------------------------------------------------------------------------
def _check_vendor_health(name: str) -> dict:
    """Execute one health-check call for *name*; return the response dict."""
    try:
        adapter = get(name)
    except KeyError:
        return {"ok": False, "mode": "unknown", "latency_ms": 0,
                "detail": f"Vendor '{name}' is not registered"}

    start = time.monotonic()
    try:
        result = adapter.health()
    except Exception as exc:  # noqa: BLE001 - adapter bugs must not crash the server
        result = {"ok": False, "mode": adapter.mode(), "latency_ms": 0,
                  "detail": f"{type(exc).__name__}: {exc}"}
    latency_ms = int((time.monotonic() - start) * 1000)
    result["latency_ms"] = latency_ms
    result["checked_at"] = datetime.utcnow().isoformat() + "Z"
    _vendor_health[name] = result
    return result


# ---------------------------------------------------------------------------
# GET /api/vendors  (list all)
# ---------------------------------------------------------------------------
@bp.get("")
def list_vendors():
    cfg = current_app.config["NIROG"]
    rows = []
    for name in sorted(_vendor_health.keys()):
        health = _vendor_health[name]
        rows.append({
            "name": name,
            "configured": health.get("ok", False),
            "mode": health.get("mode", "unknown"),
            "enabled_features": [],  # per-vendor feature flags
            "last_health": health,
        })
    # Also include any registered but unchecked vendors
    for name in sorted(getattr(cfg, "vendor_names", []), key=str.lower):
        if name not in _vendor_health:
            rows.append({
                "name": name,
                "configured": False,
                "mode": "mock",
                "enabled_features": [],
                "last_health": None,
            })
    return jsonify({"items": rows})


# ---------------------------------------------------------------------------
# GET /api/vendors/<name>/health  (single health check, admin only)
# ---------------------------------------------------------------------------
@bp.get("/<name>/health")
def vendor_health(name: str):
    cfg = current_app.config["NIROG"]
    # Only allow health checks for known vendors
    known = ["drugsetu", "shiprocket", "razorpay", "phonepe", "msg91", "gemini"]
    if name not in known:
        return jsonify({"error": "unknown_vendor", "message": f"Vendor '{name}' is not a recognised integration"}), 404

    # Check admin token
    expected = current_app.config["NIROG"].admin_token
    if not expected:
        return jsonify({"error": "admin_not_configured",
                        "message": "Set ADMIN_TOKEN to use staff endpoints."}), 503
    given = request.headers.get("X-Admin-Token", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        return jsonify({"error": "forbidden"}), 403
        return jsonify({"error": "unknown_vendor", "message": f"Vendor '{name}' is not a recognised integration"}), 404

    # In development, allow health checks without admin token for convenience,
    # but in production the @auth_required guard is enough.
    result = _check_vendor_health(name)

    # Record audit
    audit("VENDOR_HEALTH_CHECK", subject_type="vendor",
          subject_id=name,
          detail=f'{{"mode": "{result.get("mode", "unknown")}", "ok": {result.get("ok", False)}}}')

    return jsonify(result)


# ---------------------------------------------------------------------------
# POST /api/vendors/<name>/check  (force a fresh health check, admin only)
# ---------------------------------------------------------------------------
@bp.post("/<name>/check")
def force_check(name: str):
    """Force a fresh health check, bypassing cache."""
    known = ["drugsetu", "shiprocket", "razorpay", "phonepe", "msg91", "gemini"]
    if name not in known:
        return jsonify({"error": "unknown_vendor", "message": f"Vendor '{name}' is not a recognised integration"}), 404

    # Check admin token
    expected = current_app.config["NIROG"].admin_token
    if not expected:
        return jsonify({"error": "admin_not_configured",
                        "message": "Set ADMIN_TOKEN to use staff endpoints."}), 503
    given = request.headers.get("X-Admin-Token", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        return jsonify({"error": "forbidden"}), 403
        return jsonify({"error": "unknown_vendor", "message": f"Vendor '{name}' is not a recognised integration"}), 404

    result = _check_vendor_health(name)
    return jsonify(result)


# ---------------------------------------------------------------------------
# POST /api/vendors/<name>/reset  (reset cached health for a vendor)
# ---------------------------------------------------------------------------
@bp.post("/<name>/reset")
def reset_check(name: str):
    known = ["drugsetu", "shiprocket", "razorpay", "phonepe", "msg91", "gemini"]
    if name not in known:
        return jsonify({"error": "unknown_vendor", "message": f"Vendor '{name}' is not a recognised integration"}), 404

    # Check admin token
    expected = current_app.config["NIROG"].admin_token
    if not expected:
        return jsonify({"error": "admin_not_configured",
                        "message": "Set ADMIN_TOKEN to use staff endpoints."}), 503
    given = request.headers.get("X-Admin-Token", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        return jsonify({"error": "forbidden"}), 403
        return jsonify({"error": "unknown_vendor", "message": f"Vendor '{name}' is not a recognised integration"}), 404

    if name in _vendor_health:
        del _vendor_health[name]
    return jsonify({"ok": True, "message": "Cached health reset"})


# ---------------------------------------------------------------------------
# Startup: initialise default adapters (mock mode unless env vars say otherwise)
# ---------------------------------------------------------------------------
def _init_vendors(cfg: Config) -> None:
    """Register default adapters based on configuration.

    This runs once when the app starts.  Every adapter is created in
    "mock" mode by default; live/sandbox modes require env vars.
    """
    from .vendors.drugsetu import DrugSetuAdapter
    from .vendors.shiprocket import ShiprocketAdapter
    from .vendors.razorpay import RazorpayAdapter
    from .vendors.phonepe import PhonePeAdapter
    from .vendors.msg91 import Msg91Adapter
    from .vendors.gemini import GeminiAdapter

    # Record the names so the /api/vendors list includes them even before
    # a health check has been run.
    if not hasattr(cfg, "vendor_names") or cfg.vendor_names is None:
        cfg.vendor_names = []

    for name, adapter_class in [
        ("drugsetu", DrugSetuAdapter),
        ("shiprocket", ShiprocketAdapter),
        ("razorpay", RazorpayAdapter),
        ("phonepe", PhonePeAdapter),
        ("msg91", Msg91Adapter),
        ("gemini", GeminiAdapter),
    ]:
        register(name, adapter_class(cfg))
        cfg.vendor_names.append(name)

        # initialise rate limiter if the adapter exposes one
        if hasattr(adapter_class, "rate_limiter"):
            _vendor_rate_limiters[name] = adapter_class.rate_limiter

        # run an initial health check (mock call, no network needed)
        try:
            _check_vendor_health(name)
        except Exception:
            pass  # keep mock mode if the initial check fails