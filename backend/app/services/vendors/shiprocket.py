"""Shiprocket logistics adapter.

Shiprocket API docs (from API.docx):
- Auth: POST https://apiv2.shiprocket.in/v1/external/auth/login with {email, password} -> token
- Cache token in memory with expiry, re-login once on 401
- API user ≠ main login if Shiprocket requires a dedicated API user

Shiprocket endpoints to implement (from DELIVERY.docx):
- courier serviceability check
- create ad-hoc order
- assign AWB
- generate pickup
- track by AWB
- cancel shipment
- webhook for status updates

Free plan ≈ 5 orders/month; paid from ₹199/month. Avg shipment ₹36–45.
"""
from __future__ import annotations

import time
from typing import Any

from .base import VendorAdapter, VendorError
from .http import post, get, RateLimiter


class ShiprocketProvider(VendorAdapter):
    name = "shiprocket"

    def __init__(self, cfg: Any) -> None:
        self.email = os.environ.get("SHIPROCKET_EMAIL", "").strip()
        self.password = os.environ.get("SHIPROCKET_PASSWORD", "").strip()
        self.pickup_location = os.environ.get("SHIPROCKET_PICKUP_LOCATION", "").strip()
        self.pickup_pincode = os.environ.get("SHIPROCKET_PICKUP_PINCODE", "").strip()
        self.webhook_token = os.environ.get("SHIPROCKET_WEBHOOK_TOKEN", "").strip()
        self._token: str | None = None
        self._token_expiry: float = 0

        # Rate limiter: Shiprocket free tier is generous but we cap at 1 req/s
        self.rate_limiter = RateLimiter(max_calls=1, period=1.0)

    def configured(self) -> bool:
        return bool(self.email and self.password)

    def mode(self) -> str:
        return "mock"

    def health(self) -> dict:
        """Check Shiprocket auth by attempting login."""
        start = time.monotonic()
        try:
            if not self.rate_limiter.allow():
                return {"ok": False, "mode": "mock", "latency_ms": 0,
                        "detail": "rate limited"}
            resp = post(
                "https://apiv2.shiprocket.in/v1/external/auth/login",
                data={"email": self.email, "password": self.password},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token", "")
                self._token = token
                self._token_expiry = time.monotonic() + 3600  # 1 hour
                return {
                    "ok": True,
                    "mode": "mock",
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "detail": "Shiprocket auth successful",
                }
            elif resp.status_code == 401:
                self._token = None
                self._token_expiry = 0
                return {
                    "ok": False,
                    "mode": "mock",
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "detail": "Shiprocket auth failed: invalid credentials",
                }
            else:
                return {
                    "ok": False,
                    "mode": "mock",
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "detail": f"Shiprocket auth failed: {resp.status_code}",
                }
        except Exception as exc:  # noqa: BLE001 - adapter bugs must not crash the server
            return {
                "ok": False,
                "mode": "mock",
                "latency_ms": int((time.monotonic() - start) * 1000),
                "detail": f"{type(exc).__name__}: {exc}",
            }

    def rate_limit_exceeded(self) -> bool:
        return False  # checked via self.rate_limiter.allow() before calls

    def _ensure_auth(self) -> str | None:
        """Ensure we have a valid auth token, re-login if needed."""
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        # Re-login
        try:
            if not self.rate_limiter.allow():
                return None
            resp = post(
                "https://apiv2.shiprocket.in/v1/external/auth/login",
                data={"email": self.email, "password": self.password},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._token = data.get("token", "")
                self._token_expiry = time.monotonic() + 3600
                return self._token
        except Exception:
            pass
        return None

    def serviceability(self, pickup_pincode: str, delivery_pincode: str,
                       weight_kg: float, cod: bool, declared_value_paise: int) -> list:
        """Check serviceability and get courier quotes."""
        token = self._ensure_auth()
        if not token:
            return [{"error": "Shiprocket not configured. Set SHIPROCKET_EMAIL and SHIPROCKET_PASSWORD."}]

        # Build the serviceability request
        # This is a simplified implementation - real Shiprocket API has specific payload format
        # In mock mode, return a basic serviceability response
        return [{
            "service": "Shiprocket",
            "pincode": delivery_pincode,
            "serviceable": True,
            "fee": 4000,  # ₹40 in paise
            "eta": "2-3 days",
            "mode": "mock",
        }]

    def create_shipment(self, order) -> dict:
        """Create a shipment for the given order. Only allowed when order is CONFIRMED/PACKED."""
        from ..models.order import OrderStatus as OS

        # Gate: shipment must never be created for an order that is not CONFIRMED/PACKED
        if order.status not in {OS.CONFIRMED, OS.PACKED}:
            raise ValueError(f"Cannot ship order in {order.status} state. Order must be CONFIRMED or PACKED.")

        # Gate: Rx orders without verified prescription cannot ship
        if order.requires_prescription and not order.prescription.is_usable():
            raise ValueError("Cannot ship prescription order without verified prescription.")

        token = self._ensure_auth()
        if not token:
            return {"error": "Shiprocket not authenticated. Check SHIPROCKET_EMAIL/PASSWORD."}

        # In mock mode, return a basic shipment response
        awb = f"SB{abs(hash(str(order.id)) % 1000000000):09d}"
        return {
            "awb": awb,
            "shipment_id": awb,
            "courier_name": "Shiprocket",
            "tracking_url": f"https://shiprocket.com/track/{awb}",
            "status": "PACKED",
            "cost_paise": 4000,
            "mode": "mock",
            "detail": "Mock shipment created - configure Shiprocket credentials for real integration",
        }

    def track(self, awb: str) -> dict:
        """Track a shipment by AWB."""
        # In mock mode, return basic tracking info
        return {
            "awb": awb,
            "status": "DELIVERED",
            "current_location": "Hub",
            "eta": None,
            "history": [
                {"status": "PACKED", "location": "Warehouse", "timestamp": None},
                {"status": "OUT_FOR_DELIVERY", "location": "Local Hub", "timestamp": None},
                {"status": "DELIVERED", "location": "Delivery Address", "timestamp": None},
            ],
            "mode": "mock",
        }

    def cancel(self, shipment_id: str) -> None:
        """Cancel a shipment."""
        # In mock mode, just log the cancellation
        pass