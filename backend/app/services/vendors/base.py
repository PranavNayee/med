"""Vendor adapter base class — every integration follows the same interface."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


class VendorError(RuntimeError):
    """Raised when a vendor call fails (network, auth, rate limit, etc)."""


class VendorAdapter(ABC):
    """One adapter per third-party service."""

    name: str

    @abstractmethod
    def configured(self) -> bool:
        """Return True when the adapter has the minimal secrets it needs."""

    @abstractmethod
    def mode(self) -> str:
        """Return one of: "mock", "sandbox", "live"."""

    @abstractmethod
    def health(self) -> dict:
        """One cheap, real-where-possible call.

        Returns a dict with at minimum:
        {
            "ok": bool,          # True if the call succeeded
            "mode": str,         # "mock" | "sandbox" | "live"
            "latency_ms": float,
            "detail": str,       # human-readable status / error
        }
        Never returns secrets in `detail`.
        """

    @abstractmethod
    def rate_limit_exceeded(self) -> bool:
        """Check if we've hit the vendor's rate limit."""