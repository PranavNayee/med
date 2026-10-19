"""Razorpay: create orders and verify signatures.

Flow (Razorpay Standard Checkout):
  1. server creates a Razorpay order for the amount   -> create_order()
  2. the browser opens checkout.js with that order id
  3. checkout returns razorpay_payment_id / _order_id / _signature; the server
     checks HMAC-SHA256(order_id + "|" + payment_id, key_secret)
                                                        -> verify_payment()
  4. Razorpay also calls our webhook; the raw body is signed with the
     webhook secret                                     -> verify_webhook()

The browser is never trusted to say a payment succeeded: only a valid
signature marks an order paid.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request

API = "https://api.razorpay.com/v1"


class PaymentError(RuntimeError):
    pass


def create_order(*, key_id: str, key_secret: str, amount_paise: int,
                 receipt: str, timeout: float = 20.0) -> dict:
    body = json.dumps({"amount": int(amount_paise), "currency": "INR",
                       "receipt": receipt[:40]}).encode()
    auth = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    req = urllib.request.Request(f"{API}/orders", data=body, method="POST", headers={
        "Authorization": f"Basic {auth}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        raise PaymentError(f"Razorpay order failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PaymentError(f"Razorpay unreachable: {exc}") from exc


def _sign(message: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_payment(*, key_secret: str, razorpay_order_id: str,
                   razorpay_payment_id: str, razorpay_signature: str) -> bool:
    expected = _sign(f"{razorpay_order_id}|{razorpay_payment_id}".encode(), key_secret)
    return hmac.compare_digest(expected, razorpay_signature or "")


def verify_webhook(*, webhook_secret: str, raw_body: bytes, signature: str) -> bool:
    return hmac.compare_digest(_sign(raw_body, webhook_secret), signature or "")
