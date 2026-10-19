"""Cart, orders, prescriptions.

Order status is an explicit state machine. A prescription order cannot
reach PACKED without a pharmacist having verified the uploaded
prescription — that is enforced in code, not left to a UI flow.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from ..extensions import db


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    AWAITING_PRESCRIPTION = "AWAITING_PRESCRIPTION"
    AWAITING_PHARMACIST = "AWAITING_PHARMACIST"
    CONFIRMED = "CONFIRMED"
    PACKED = "PACKED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


#: Only these transitions are legal. Anything else raises.
ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.AWAITING_PRESCRIPTION, OrderStatus.CONFIRMED,
                          OrderStatus.CANCELLED},
    OrderStatus.AWAITING_PRESCRIPTION: {OrderStatus.AWAITING_PHARMACIST,
                                        OrderStatus.CANCELLED},
    OrderStatus.AWAITING_PHARMACIST: {OrderStatus.CONFIRMED, OrderStatus.REJECTED,
                                      OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PACKED, OrderStatus.CANCELLED},
    OrderStatus.PACKED: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
}


class IllegalTransition(RuntimeError):
    pass


class Cart(db.Model):
    __tablename__ = "carts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    member_id = db.Column(db.Integer, db.ForeignKey("family_members.id"))
    is_open = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    items = db.relationship("CartItem", back_populates="cart",
                            cascade="all, delete-orphan")


class CartItem(db.Model):
    __tablename__ = "cart_items"

    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(db.Integer, db.ForeignKey("carts.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)

    cart = db.relationship("Cart", back_populates="items")
    product = db.relationship("Product")

    __table_args__ = (db.UniqueConstraint("cart_id", "product_id", name="uq_cart_product"),)


class Prescription(db.Model):
    """An uploaded prescription plus its verification outcome."""

    __tablename__ = "prescriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("family_members.id"))

    file_path = db.Column(db.String(500), nullable=False)  # object store key, not a URL
    file_hash = db.Column(db.String(64), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    doctor_name = db.Column(db.String(160))
    doctor_registration_number = db.Column(db.String(80))
    prescribed_on = db.Column(db.Date)
    valid_until = db.Column(db.Date)

    verification_status = db.Column(db.String(20), default="PENDING", nullable=False)
    verified_by_pharmacist = db.Column(db.String(160))
    pharmacist_registration_number = db.Column(db.String(80))
    verified_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.String(255))

    def is_usable(self) -> bool:
        return self.verification_status == "VERIFIED"


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(24), nullable=False, unique=True, index=True)
    idempotency_key = db.Column(db.String(64), unique=True, index=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    member_id = db.Column(db.Integer, db.ForeignKey("family_members.id"))
    address_id = db.Column(db.Integer, db.ForeignKey("addresses.id"))
    pharmacy_id = db.Column(db.Integer, db.ForeignKey("pharmacies.id"))
    prescription_id = db.Column(db.Integer, db.ForeignKey("prescriptions.id"))

    status = db.Column(db.String(30), default=OrderStatus.CREATED.value, nullable=False)
    requires_prescription = db.Column(db.Boolean, default=False, nullable=False)

    subtotal_paise = db.Column(db.Integer, nullable=False, default=0)
    discount_paise = db.Column(db.Integer, nullable=False, default=0)
    delivery_paise = db.Column(db.Integer, nullable=False, default=0)
    total_paise = db.Column(db.Integer, nullable=False, default=0)

    payment_method = db.Column(db.String(20), default="COD", nullable=False)  # COD / ONLINE
    payment_status = db.Column(db.String(20), default="PENDING", nullable=False)
    payment_reference = db.Column(db.String(80))
    # Snapshot: the order must show where it went even if the address changes.
    shipping_address = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    items = db.relationship("OrderItem", back_populates="order",
                            cascade="all, delete-orphan")
    events = db.relationship("OrderEvent", back_populates="order",
                             cascade="all, delete-orphan",
                             order_by="OrderEvent.created_at")
    prescription = db.relationship("Prescription")

    def transition_to(self, new: OrderStatus, *, actor: str, note: str = "") -> None:
        current = OrderStatus(self.status)
        if new not in ALLOWED_TRANSITIONS[current]:
            raise IllegalTransition(f"{current.value} -> {new.value} is not permitted")

        # Hard gate: prescription orders never skip verification.
        if new in {OrderStatus.CONFIRMED, OrderStatus.PACKED} and self.requires_prescription:
            if self.prescription is None or not self.prescription.is_usable():
                raise IllegalTransition(
                    "Order contains prescription medicines and has no pharmacist-"
                    "verified prescription attached."
                )

        self.status = new.value
        self.events.append(OrderEvent(status=new.value, actor=actor, note=note))


class OrderItem(db.Model):
    """Prices are snapshotted. A later catalogue price change must never
    silently rewrite what a customer was charged."""

    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    brand_name_snapshot = db.Column(db.String(240), nullable=False)
    composition_snapshot = db.Column(db.String(500), nullable=False)
    pack_snapshot = db.Column(db.String(120), nullable=False)
    drug_schedule_snapshot = db.Column(db.String(8), nullable=False)

    quantity = db.Column(db.Integer, nullable=False)
    unit_price_paise = db.Column(db.Integer, nullable=False)
    unit_mrp_paise = db.Column(db.Integer, nullable=False)
    line_total_paise = db.Column(db.Integer, nullable=False)

    batch_number = db.Column(db.String(64))
    expiry_date = db.Column(db.Date)

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product")


class OrderEvent(db.Model):
    __tablename__ = "order_events"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    actor = db.Column(db.String(80), nullable=False)
    note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    order = db.relationship("Order", back_populates="events")
