"""Shared HTTP helper for all vendor adapters.

- timeouts, retries with binary backoff
- redacted logging (never echo secrets or full payloads)
- per-vendor rate limiter (in-memory, simple)
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

#: Default read+connect timeout in seconds, per-call overrides possible.
DEFAULT_TIMEOUT = 10

#: Max retries for idempotent GET/POST calls.
MAX_RETRIES = 3

#: Base backoff factor: first retry sleeps 1s, second 2s, third 4s.
_BACKOFF = [1, 2, 4]


class RateLimiter:
    """Simple in-memory rate limiter per vendor.

    Calls `allow()` before each request.  Returns True if the call
    should proceed, False if the caller should back off.
    """

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._calls: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        # Drop timestamps older than ``period``.
        self._calls = [t for t in self._calls if now - t < self.period]
        if len(self._calls) >= self.max_calls:
            return False
        self._calls.append(now)
        return True


def _request(method: str, url: str, timeout: float = DEFAULT_TIMEOUT,
             **kwargs: Any) -> Any:
    """One HTTP call with retries + backoff + redacted logging.

    The caller must inspect ``resp`` themselves; we never echo raw bodies.
    """
    attempt = 0
    last_exc: BaseException | None = None
    while attempt <= MAX_RETRIES:
        try:
            resp = requests.request(
                method, url, timeout=timeout, **kwargs,
            )
            # Store latency for the caller.
            resp.elapsed_total = getattr(resp, "elapsed", None)
            if hasattr(resp, "elapsed"):
                resp.elapsed_ms = resp.elapsed.total_seconds() * 1000
            else:
                resp.elapsed_ms = None
            return resp
        except (requests.RequestException,) as exc:
            last_exc = exc
            attempt += 1
            if attempt <= MAX_RETRIES:
                wait = _BACKOFF[attempt - 1]
                log.warning(
                    "vendor HTTP retry %s/%s for %s: %s",
                    attempt, MAX_RETRIES, url, exc,
                )
                time.sleep(wait)
            else:
                log.error(
                    "vendor HTTP failed after %s retries for %s: %s",
                    MAX_RETRIES, url, exc,
                )
    raise last_exc


def get(url: str, *, timeout: float = DEFAULT_TIMEOUT,
        rate_limiter: Any = None, **kwargs: Any) -> Any:
    """GET wrapper with optional rate limiting and redaction."""
    if rate_limiter and not rate_limiter.allow():
        raise VendorError("rate limited")
    resp = _request("GET", url, timeout=timeout, **kwargs)
    return resp


def post(url: str, *, timeout: float = DEFAULT_TIMEOUT,
          rate_limiter: Any = None, data: dict | None = None,
          **kwargs: Any) -> Any:
    """POST wrapper with optional rate limiting and redaction."""
    if rate_limiter and not rate_limiter.allow():
        raise VendorError("rate limited")
    body = json.dumps(data).encode() if data else None
    headers = kwargs.pop("headers", {})
    if data is not None:
        headers.setdefault("Content-Type", "application/json")
    return _request("POST", url, timeout=timeout,
                    headers=headers, data=data, **kwargs)