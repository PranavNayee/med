"""Accounts, cart, checkout and prescription verification."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import select

from ..extensions import db
from ..models import (
    Address,
    AuditLog,
    Cart,
    CartItem,
    FamilyMember,
    IllegalTransition,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    Prescription,
    Product,
    User,
)
from ..security import audit, auth_required, hash_password, issue_token, verify_password
from ..serializers import address_card, family_member, order_summary, product_card, rupees
from ..services import payments
from ..services.rx_policy import evaluate_cart, evaluate_item

bp = Blueprint("commerce", __name__, url_prefix="/api")

DELIVERY_FEE_PAISE = 4000
FREE_DELIVERY_THRESHOLD_PAISE = 49900
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_MINUTES = 15
CANCELLABLE = {OrderStatus.CREATED, OrderStatus.AWAITING_PRESCRIPTION,
               OrderStatus.AWAITING_PHARMACIST, OrderStatus.CONFIRMED}
_PINCODE = re.compile(r"^[1-9][0-9]{5}$")


def normalise_mobile(raw) -> str | None:
    """"+91 98250 00000", "098250-00000" -> "9825000000"; None if not valid."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9][0-9]{9}", digits) else None


# ---------------------------------------------------------------- auth

@bp.post("/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    mobile = normalise_mobile(data.get("mobile"))
    name = " ".join((data.get("full_name") or "").split())[:160]
    password = data.get("password") or ""

    if mobile is None:
        return jsonify({"error": "invalid_mobile",
                        "message": "Enter a valid 10-digit Indian mobile number."}), 400
    if not name or len(password) < 8:
        return jsonify({"error": "invalid_input",
                        "message": "Your name and a password of at least 8 characters are required."}), 400

    if db.session.execute(select(User).where(User.mobile == mobile)).scalar_one_or_none():
        return jsonify({"error": "already_registered"}), 409

    user = User(mobile=mobile, full_name=name, password_hash=hash_password(password))
    db.session.add(user)
    db.session.flush()

    # Every account starts with a SELF profile so records always have an owner.
    db.session.add(FamilyMember(user_id=user.id, full_name=name,
                                relation="SELF", is_self=True))
    g.user_id = user.id
    audit("USER_REGISTERED", subject_type="user", subject_id=user.id)
    db.session.commit()

    return jsonify({"token": issue_token(user.id), "user": {"name": user.full_name}}), 201


@bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    mobile = normalise_mobile(data.get("mobile")) or ""

    # Throttle password guessing per mobile number.
    since = datetime.utcnow() - timedelta(minutes=LOGIN_WINDOW_MINUTES)
    failures = db.session.query(AuditLog).filter(
        AuditLog.event == "LOGIN_FAILED", AuditLog.subject_id == mobile,
        AuditLog.created_at >= since,
    ).count()
    if failures >= LOGIN_MAX_FAILURES:
        return jsonify({"error": "too_many_attempts",
                        "message": f"Too many attempts. Try again in {LOGIN_WINDOW_MINUTES} minutes."}), 429

    user = db.session.execute(
        select(User).where(User.mobile == mobile)
    ).scalar_one_or_none()

    # Same response for unknown user and wrong password — no account enumeration.
    if user is None or not verify_password(data.get("password") or "", user.password_hash):
        audit("LOGIN_FAILED", subject_type="user", subject_id=mobile, actor_type="ANON")
        db.session.commit()
        return jsonify({"error": "invalid_credentials",
                        "message": "Mobile number or password is incorrect."}), 401
    if not user.is_active:
        return jsonify({"error": "account_disabled"}), 403

    user.last_login_at = datetime.utcnow()
    g.user_id = user.id
    audit("USER_LOGIN", subject_type="user", subject_id=user.id)
    db.session.commit()
    return jsonify({"token": issue_token(user.id), "user": {"name": user.full_name}})


@bp.get("/me")
@auth_required
def me():
    user = db.session.get(User, g.user_id)
    if user is None or not user.is_active:
        return jsonify({"error": "invalid_token"}), 401
    return jsonify({
        "name": user.full_name,
        "first_name": user.full_name.split()[0] if user.full_name else "",
        "mobile": user.mobile[-4:].rjust(len(user.mobile), "*"),
        "abha_linked": bool(user.abha_number),
        "members": [family_member(m) for m in user.members],
    })


# ---------------------------------------------------------------- cart

def _open_cart(user_id: int) -> Cart:
    cart = db.session.execute(
        select(Cart).where(Cart.user_id == user_id, Cart.is_open.is_(True))
    ).scalars().first()
    if cart is None:
        cart = Cart(user_id=user_id)
        db.session.add(cart)
        db.session.flush()
    return cart


def _cart_payload(cart: Cart) -> dict:
    cfg = current_app.config["NIROG"]
    lines, subtotal, mrp_total = [], 0, 0

    for item in cart.items:
        product = item.product
        decision = evaluate_item(product.drug_schedule, item.quantity,
                                 cfg.max_rx_quantity_per_order)
        line_total = product.selling_price_paise * item.quantity
        subtotal += line_total
        mrp_total += product.mrp_paise * item.quantity
        lines.append({
            **product_card(product),
            "qty": item.quantity,
            "line_total": rupees(line_total),
            "policy": {
                "allowed": decision.allowed,
                "requires_prescription": decision.requires_prescription,
                "reason": decision.reason_code,
                "message": decision.message,
            },
        })

    allowed, requires_rx, _ = evaluate_cart(
        [(i.product.drug_schedule, i.quantity) for i in cart.items],
        cfg.max_rx_quantity_per_order,
    )
    delivery = 0 if subtotal >= FREE_DELIVERY_THRESHOLD_PAISE or subtotal == 0 \
        else DELIVERY_FEE_PAISE

    return {
        "items": lines,
        "subtotal": rupees(subtotal),
        "mrp_total": rupees(mrp_total),
        "you_save": rupees(mrp_total - subtotal),
        "delivery": rupees(delivery),
        "total": rupees(subtotal + delivery),
        "checkout_allowed": allowed and bool(cart.items),
        "requires_prescription": requires_rx,
    }


@bp.get("/cart")
@auth_required
def get_cart():
    return jsonify(_cart_payload(_open_cart(g.user_id)))


@bp.post("/cart/items")
@auth_required
def add_to_cart():
    data = request.get_json(silent=True) or {}
    sku = (data.get("sku") or "").strip()
    qty = max(1, int(data.get("quantity") or 1))

    product = db.session.execute(
        select(Product).where(Product.sku == sku, Product.is_active.is_(True))
    ).scalar_one_or_none()
    if product is None:
        return jsonify({"error": "not_found"}), 404

    cfg = current_app.config["NIROG"]
    decision = evaluate_item(product.drug_schedule, qty, cfg.max_rx_quantity_per_order)
    if not decision.allowed:
        return jsonify({"error": decision.reason_code, "message": decision.message}), 422

    cart = _open_cart(g.user_id)
    existing = next((i for i in cart.items if i.product_id == product.id), None)
    if existing:
        existing.quantity = min(existing.quantity + qty, cfg.max_rx_quantity_per_order
                                if decision.requires_prescription else 20)
    else:
        db.session.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=qty))

    db.session.commit()
    return jsonify(_cart_payload(_open_cart(g.user_id)))


@bp.put("/cart/items/<sku>")
@auth_required
def set_cart_quantity(sku: str):
    """Set a line's quantity; 0 removes it."""
    data = request.get_json(silent=True) or {}
    try:
        qty = int(data.get("quantity"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_input", "message": "quantity must be a number"}), 400

    cart = _open_cart(g.user_id)
    existing = next((i for i in cart.items if i.product.sku == sku), None)
    if qty <= 0:
        if existing:
            db.session.delete(existing)
        db.session.commit()
        return jsonify(_cart_payload(_open_cart(g.user_id)))

    product = existing.product if existing else db.session.execute(
        select(Product).where(Product.sku == sku, Product.is_active.is_(True))
    ).scalar_one_or_none()
    if product is None:
        return jsonify({"error": "not_found"}), 404

    cfg = current_app.config["NIROG"]
    decision = evaluate_item(product.drug_schedule, qty, cfg.max_rx_quantity_per_order)
    if not decision.allowed:
        return jsonify({"error": decision.reason_code, "message": decision.message}), 422
    qty = min(qty, 20)
    if existing:
        existing.quantity = qty
    else:
        db.session.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=qty))
    db.session.commit()
    return jsonify(_cart_payload(_open_cart(g.user_id)))


@bp.delete("/cart/items/<sku>")
@auth_required
def remove_from_cart(sku: str):
    cart = _open_cart(g.user_id)
    for item in list(cart.items):
        if item.product.sku == sku:
            db.session.delete(item)
    db.session.commit()
    return jsonify(_cart_payload(_open_cart(g.user_id)))


# ---------------------------------------------------------------- addresses

def _user_address(address_id) -> Address | None:
    try:
        address_id = int(address_id)
    except (TypeError, ValueError):
        return None
    return db.session.execute(
        select(Address).where(Address.id == address_id, Address.user_id == g.user_id,
                              Address.is_archived.is_(False))
    ).scalar_one_or_none()


@bp.get("/addresses")
@auth_required
def list_addresses():
    rows = db.session.execute(
        select(Address).where(Address.user_id == g.user_id, Address.is_archived.is_(False))
        .order_by(Address.is_default.desc(), Address.id.desc())
    ).scalars().all()
    return jsonify({"items": [address_card(a) for a in rows]})


@bp.post("/addresses")
@auth_required
def add_address():
    data = request.get_json(silent=True) or {}
    clean = {k: " ".join(str(data.get(k) or "").split())
             for k in ("label", "recipient_name", "phone", "line1", "line2",
                       "city", "state", "pincode")}
    errors = {}
    if not clean["recipient_name"]:
        errors["recipient_name"] = "Enter the name of the person receiving the order."
    if not clean["line1"]:
        errors["line1"] = "Enter the house / flat and street."
    if not clean["city"]:
        errors["city"] = "Enter the city."
    if not clean["state"]:
        errors["state"] = "Enter the state."
    if not _PINCODE.match(clean["pincode"]):
        errors["pincode"] = "Enter a valid 6-digit pincode."
    phone = normalise_mobile(clean["phone"])
    if phone is None:
        errors["phone"] = "Enter a valid 10-digit mobile number."
    if errors:
        return jsonify({"error": "invalid_input", "fields": errors,
                        "message": next(iter(errors.values()))}), 400

    has_default = db.session.query(Address).filter(
        Address.user_id == g.user_id, Address.is_default.is_(True),
        Address.is_archived.is_(False)).count() > 0
    address = Address(
        user_id=g.user_id, label=(clean["label"] or "Home")[:40],
        recipient_name=clean["recipient_name"][:160], phone=phone,
        line1=clean["line1"][:255], line2=clean["line2"][:255] or None,
        city=clean["city"][:80], state=clean["state"][:80], pincode=clean["pincode"],
        is_default=bool(data.get("is_default")) or not has_default,
    )
    if address.is_default:
        _clear_default()
    db.session.add(address)
    db.session.commit()
    return jsonify(address_card(address)), 201


@bp.post("/addresses/<int:address_id>/default")
@auth_required
def make_default_address(address_id: int):
    address = _user_address(address_id)
    if address is None:
        return jsonify({"error": "not_found"}), 404
    _clear_default()
    address.is_default = True
    db.session.commit()
    return jsonify(address_card(address))


@bp.delete("/addresses/<int:address_id>")
@auth_required
def delete_address(address_id: int):
    address = _user_address(address_id)
    if address is None:
        return jsonify({"error": "not_found"}), 404
    used = db.session.query(Order).filter(Order.address_id == address.id).count()
    if used:
        address.is_archived = True       # orders still point at it
        address.is_default = False
    else:
        db.session.delete(address)
    db.session.commit()
    return jsonify({"ok": True})


def _clear_default() -> None:
    db.session.query(Address).filter(
        Address.user_id == g.user_id, Address.is_default.is_(True)
    ).update({"is_default": False})


# ---------------------------------------------------------------- orders

@bp.post("/orders")
@auth_required
def create_order():
    """Checkout. Idempotent on the client-supplied key so a retried request
    after a timeout cannot create a duplicate order."""
    data = request.get_json(silent=True) or {}
    key = (data.get("idempotency_key") or "").strip() or secrets.token_hex(16)

    existing = db.session.execute(
        select(Order).where(Order.idempotency_key == key)
    ).scalar_one_or_none()
    if existing:
        return jsonify(order_summary(existing)), 200

    cart = _open_cart(g.user_id)
    if not cart.items:
        return jsonify({"error": "cart_empty", "message": "Your cart is empty."}), 422

    address = _user_address(data.get("address_id"))
    if address is None:
        return jsonify({"error": "address_required",
                        "message": "Choose a delivery address."}), 422
    payment_method = (data.get("payment_method") or "COD").upper()
    allowed_methods = {"COD", "ONLINE"} if cfg_online() else {"COD"}
    if payment_method not in allowed_methods:
        return jsonify({"error": "payment_method_unavailable",
                        "message": "That payment method isn't available yet."}), 422

    cfg = current_app.config["NIROG"]
    allowed, requires_rx, decisions = evaluate_cart(
        [(i.product.drug_schedule, i.quantity) for i in cart.items],
        cfg.max_rx_quantity_per_order,
    )
    if not allowed:
        blocked = next(d for d in decisions if not d.allowed)
        return jsonify({"error": blocked.reason_code, "message": blocked.message}), 422

    if requires_rx and not cfg.enable_rx_dispatch:
        return jsonify({
            "error": "RX_DISPATCH_DISABLED",
            # ENABLE_RX_DISPATCH needs a drug licence and a registered
            # pharmacist wired to verification (docs/COMPLIANCE.md).
            "message": "Prescription (Rx) medicines can't be ordered online yet. "
                       "Remove them from your cart to order the rest.",
        }), 503

    order = Order(
        order_number="NRG" + secrets.token_hex(4).upper(),
        idempotency_key=key,
        user_id=g.user_id,
        member_id=data.get("member_id"),
        address_id=address.id,
        shipping_address=address.one_line(),
        payment_method=payment_method,
        requires_prescription=requires_rx,
    )
    db.session.add(order)
    db.session.flush()

    subtotal = 0
    for item in cart.items:
        product = item.product
        line_total = product.selling_price_paise * item.quantity
        subtotal += line_total
        db.session.add(OrderItem(
            order_id=order.id, product_id=product.id,
            brand_name_snapshot=product.brand_name,
            composition_snapshot=product.composition_text,
            pack_snapshot=product.pack_label,
            drug_schedule_snapshot=product.drug_schedule,
            quantity=item.quantity,
            unit_price_paise=product.selling_price_paise,
            unit_mrp_paise=product.mrp_paise,
            line_total_paise=line_total,
        ))

    order.subtotal_paise = subtotal
    order.delivery_paise = 0 if subtotal >= FREE_DELIVERY_THRESHOLD_PAISE \
        else DELIVERY_FEE_PAISE
    order.total_paise = order.subtotal_paise + order.delivery_paise

    payment = None
    if payment_method == "ONLINE":
        # Stays CREATED until a verified payment moves it on.
        try:
            payment = _razorpay_order(order)
        except payments.PaymentError as exc:
            db.session.rollback()
            current_app.logger.warning("razorpay order failed: %s", exc)
            return jsonify({"error": "payment_unavailable",
                            "message": "Online payment is unavailable right now. "
                                       "Try again or choose cash on delivery."}), 502
        order.events.append(OrderEvent(status=OrderStatus.CREATED.value,
                                       actor=f"user:{g.user_id}",
                                       note="Order placed, awaiting payment"))
    else:
        order.transition_to(
            OrderStatus.AWAITING_PRESCRIPTION if requires_rx else OrderStatus.CONFIRMED,
            actor=f"user:{g.user_id}", note="Order placed",
        )

    cart.is_open = False
    audit("ORDER_CREATED", subject_type="order", subject_id=order.order_number)
    db.session.commit()
    body = order_summary(order)
    if payment:
        body["payment"] = payment
    return jsonify(body), 201


@bp.get("/orders")
@auth_required
def list_orders():
    rows = db.session.execute(
        select(Order).where(Order.user_id == g.user_id)
        .order_by(Order.created_at.desc()).limit(25)
    ).scalars().all()
    return jsonify({"items": [order_summary(o) for o in rows]})


@bp.get("/orders/<order_number>")
@auth_required
def get_order(order_number: str):
    order = db.session.execute(
        select(Order).where(Order.order_number == order_number,
                            Order.user_id == g.user_id)
    ).scalar_one_or_none()
    if order is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(order_summary(order))


@bp.post("/orders/<order_number>/cancel")
@auth_required
def cancel_order(order_number: str):
    order = db.session.execute(
        select(Order).where(Order.order_number == order_number,
                            Order.user_id == g.user_id)
    ).scalar_one_or_none()
    if order is None:
        return jsonify({"error": "not_found"}), 404
    if OrderStatus(order.status) not in CANCELLABLE:
        return jsonify({"error": "not_cancellable",
                        "message": "This order can no longer be cancelled."}), 409
    note = "Cancelled by customer"
    if order.payment_status == "PAID":
        # Money has moved: the refund is issued from the Razorpay dashboard.
        order.payment_status = "REFUND_PENDING"
        note += "; refund pending"
    order.transition_to(OrderStatus.CANCELLED, actor=f"user:{g.user_id}", note=note)
    audit("ORDER_CANCELLED", subject_type="order", subject_id=order_number)
    db.session.commit()
    return jsonify(order_summary(order))


# ---------------------------------------------------------------- payments

def cfg_online() -> bool:
    return current_app.config["NIROG"].online_payments_available


def _razorpay_order(order: Order) -> dict:
    """Checkout parameters for an order, creating the Razorpay order once."""
    cfg = current_app.config["NIROG"]
    if not order.payment_reference:
        rzp = payments.create_order(key_id=cfg.razorpay_key_id,
                                    key_secret=cfg.razorpay_key_secret,
                                    amount_paise=order.total_paise,
                                    receipt=order.order_number)
        order.payment_reference = rzp["id"]
    return {"provider": "razorpay", "key_id": cfg.razorpay_key_id,
            "razorpay_order_id": order.payment_reference,
            "amount": order.total_paise, "currency": "INR",
            "name": "Nirog", "description": f"Order {order.order_number}"}


def _mark_paid(order: Order, payment_id: str, actor: str) -> None:
    """Idempotent: a payment confirmed twice (checkout + webhook) counts once."""
    if order.payment_status == "PAID":
        return
    order.payment_status = "PAID"
    if order.status == OrderStatus.CREATED.value:
        order.transition_to(
            OrderStatus.AWAITING_PRESCRIPTION if order.requires_prescription
            else OrderStatus.CONFIRMED,
            actor=actor, note=f"Payment received ({payment_id})")
    audit("PAYMENT_CAPTURED", actor_type="SYSTEM" if actor == "razorpay" else "USER",
          subject_type="order", subject_id=order.order_number, detail=f'{{"payment_id": "{payment_id}"}}')


@bp.get("/payments/config")
def payments_config():
    cfg = current_app.config["NIROG"]
    return jsonify({"online_available": cfg.online_payments_available,
                    "key_id": cfg.razorpay_key_id if cfg.online_payments_available else None})


@bp.post("/orders/<order_number>/pay")
@auth_required
def pay_order(order_number: str):
    """Parameters to (re)open checkout for an unpaid online order."""
    order = db.session.execute(
        select(Order).where(Order.order_number == order_number, Order.user_id == g.user_id)
    ).scalar_one_or_none()
    if order is None:
        return jsonify({"error": "not_found"}), 404
    if (order.payment_method != "ONLINE" or order.payment_status != "PENDING"
            or order.status != OrderStatus.CREATED.value):
        return jsonify({"error": "not_payable", "message": "This order doesn't need payment."}), 409
    if not cfg_online():
        return jsonify({"error": "payment_unavailable",
                        "message": "Online payment is unavailable right now."}), 503
    try:
        payment = _razorpay_order(order)
    except payments.PaymentError as exc:
        current_app.logger.warning("razorpay order failed: %s", exc)
        return jsonify({"error": "payment_unavailable",
                        "message": "Online payment is unavailable right now. Try again shortly."}), 502
    db.session.commit()
    return jsonify(payment)


@bp.post("/orders/<order_number>/payment/verify")
@auth_required
def verify_order_payment(order_number: str):
    data = request.get_json(silent=True) or {}
    order = db.session.execute(
        select(Order).where(Order.order_number == order_number, Order.user_id == g.user_id)
    ).scalar_one_or_none()
    if order is None:
        return jsonify({"error": "not_found"}), 404
    cfg = current_app.config["NIROG"]
    rzp_order = data.get("razorpay_order_id") or ""
    ok = (rzp_order == order.payment_reference and payments.verify_payment(
        key_secret=cfg.razorpay_key_secret, razorpay_order_id=rzp_order,
        razorpay_payment_id=data.get("razorpay_payment_id") or "",
        razorpay_signature=data.get("razorpay_signature") or ""))
    if not ok:
        audit("PAYMENT_SIGNATURE_INVALID", subject_type="order", subject_id=order_number)
        db.session.commit()
        return jsonify({"error": "payment_not_verified",
                        "message": "We couldn't verify this payment. If money was "
                                   "taken, it will be confirmed or refunded automatically."}), 400
    _mark_paid(order, data["razorpay_payment_id"], actor=f"user:{g.user_id}")
    db.session.commit()
    return jsonify(order_summary(order))


@bp.post("/payments/razorpay/webhook")
def razorpay_webhook():
    """Confirms payments even when the customer closed the checkout tab."""
    cfg = current_app.config["NIROG"]
    if not cfg.razorpay_webhook_secret:
        return jsonify({"error": "webhook_not_configured"}), 503
    raw = request.get_data()
    if not payments.verify_webhook(webhook_secret=cfg.razorpay_webhook_secret, raw_body=raw,
                                   signature=request.headers.get("X-Razorpay-Signature", "")):
        return jsonify({"error": "invalid_signature"}), 400
    event = request.get_json(silent=True) or {}
    if event.get("event") not in {"payment.captured", "order.paid"}:
        return jsonify({"ignored": event.get("event")}), 200
    entity = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    order = db.session.execute(
        select(Order).where(Order.payment_reference == entity.get("order_id"))
    ).scalar_one_or_none()
    if order is None:
        return jsonify({"ignored": "unknown order"}), 200
    if int(entity.get("amount") or 0) != order.total_paise:
        current_app.logger.warning("webhook amount mismatch for %s", order.order_number)
        return jsonify({"error": "amount_mismatch"}), 400
    _mark_paid(order, entity.get("id") or "", actor="razorpay")
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/orders/<order_number>/prescription")
@auth_required
def attach_prescription(order_number: str):
    """Attach an uploaded prescription. This does NOT confirm the order —
    it moves it into the pharmacist's queue."""
    order = db.session.execute(
        select(Order).where(Order.order_number == order_number,
                            Order.user_id == g.user_id)
    ).scalar_one_or_none()
    if order is None:
        return jsonify({"error": "not_found"}), 404

    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "file_required"}), 400

    content = upload.read()
    if not content:
        return jsonify({"error": "empty_file"}), 400

    # Production: stream to object storage. The DB only ever holds the key.
    digest = hashlib.sha256(content).hexdigest()
    key = f"prescriptions/{g.user_id}/{digest}"

    prescription = Prescription(
        user_id=g.user_id, member_id=order.member_id,
        file_path=key, file_hash=digest,
    )
    db.session.add(prescription)
    db.session.flush()

    order.prescription_id = prescription.id
    try:
        order.transition_to(OrderStatus.AWAITING_PHARMACIST,
                            actor=f"user:{g.user_id}",
                            note="Prescription uploaded, awaiting verification")
    except IllegalTransition as exc:
        return jsonify({"error": "illegal_transition", "message": str(exc)}), 409

    audit("PRESCRIPTION_UPLOADED", subject_type="order", subject_id=order_number)
    db.session.commit()
    return jsonify(order_summary(order))


@bp.post("/pharmacist/orders/<order_number>/verify")
def pharmacist_verify(order_number: str):
    """Pharmacist console endpoint.

    Deliberately left behind a separate auth boundary — this must be wired to
    a staff identity provider with the pharmacist's registration number, not
    to customer JWTs. It refuses to run until that is configured.
    """
    return jsonify({
        "error": "PHARMACIST_AUTH_NOT_CONFIGURED",
        "message": "Pharmacist verification requires a staff authentication "
                   "provider carrying a registered pharmacist number. See "
                   "docs/COMPLIANCE.md before enabling.",
    }), 501
