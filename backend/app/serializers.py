"""Model -> JSON. Kept separate so the API shape can change without
touching the ORM, and so money is formatted in exactly one place.
"""
from __future__ import annotations

import json


def rupees(paise: int | None) -> float | None:
    """Paise are the storage unit; rupees are the presentation unit."""
    return None if paise is None else round(paise / 100, 2)


def _json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def product_card(product) -> dict:
    """Compact shape for grids and search results."""
    return {
        "id": product.sku,
        "name": product.brand_name,
        "salt": product.composition_text,
        "form": product.dosage_form.title(),
        "mfr": product.manufacturer.name if product.manufacturer else None,
        "pack": product.pack_label,
        "price": rupees(product.selling_price_paise),
        "mrp": rupees(product.mrp_paise),
        "discount": product.discount_percent,
        "rx": product.is_prescription_required,
        "schedule": product.drug_schedule,
        "cat": product.category,
        "rating": float(product.rating) if product.rating is not None else None,
        "nlem": product.is_nlem,
    }


def product_detail(product) -> dict:
    from .services.content import info_for  # local: services import serializers' deps
    data = product_card(product)
    data.update({
        "uses": _json_list(product.uses),
        "how": product.mechanism,
        "side": _json_list(product.side_effects),
        "directions": product.directions,
        "storage": product.storage,
        "content": {
            "source": product.content_source,
            "reviewed_by": product.content_reviewed_by,
            "reviewed_at": product.content_reviewed_at.isoformat()
            if product.content_reviewed_at else None,
        },
        "info": info_for(product),
        "pack_units": product.pack_units,
        "per_unit": (rupees(product.selling_price_paise / product.pack_units)
                     if product.pack_units else None),
        "ingredients": [
            {
                "molecule": ing.molecule.canonical_name if ing.molecule else None,
                "strength": float(ing.strength_value) if ing.strength_value else None,
                "unit": ing.strength_unit,
            }
            for ing in sorted(product.ingredients, key=lambda i: i.position)
        ],
    })
    return data


def substitute(result) -> dict:
    card = product_card(result.product)
    card.update({
        "tier": result.tier.value,
        "saving": rupees(result.saving_paise),
        "saving_percent": result.saving_percent,
    })
    return card


def doctor_card(doctor) -> dict:
    return {
        "id": f"d{doctor.id}",
        "name": doctor.full_name,
        "spec": doctor.specialty.name if doctor.specialty else None,
        "specId": doctor.specialty.code if doctor.specialty else None,
        "qual": doctor.qualification,
        "exp": doctor.experience_years,
        "rating": float(doctor.rating) if doctor.rating is not None else None,
        "reviews": doctor.review_count,
        "fee": rupees(doctor.consultation_fee_paise),
        "online": doctor.is_online,
        "langs": _json_list(doctor.languages),
        "hospital": doctor.affiliation,
        "about": doctor.about,
        "bookable": doctor.is_bookable,
        # Registration number is shown because patients are entitled to verify it.
        "registration": doctor.nmc_registration_number,
    }


def lab_test(test) -> dict:
    return {
        "id": test.code,
        "name": test.name,
        "params": test.parameter_count,
        "price": rupees(test.price_paise),
        "mrp": rupees(test.mrp_paise),
        "fasting": test.fasting_required,
        "time": f"{test.turnaround_hours} hrs" if test.turnaround_hours else None,
        "sample": test.sample_type,
        "tag": test.tag,
        "package": test.is_package,
        "for": test.audience,
        "lab": test.nabl_lab_name,
    }


def address_card(address) -> dict:
    return {
        "id": address.id,
        "label": address.label,
        "recipient_name": address.recipient_name,
        "phone": address.phone,
        "line1": address.line1,
        "line2": address.line2,
        "city": address.city,
        "state": address.state,
        "pincode": address.pincode,
        "is_default": address.is_default,
        "text": address.one_line(),
    }


def order_summary(order) -> dict:
    return {
        "id": order.order_number,
        "status": order.status,
        "requires_prescription": order.requires_prescription,
        "total": rupees(order.total_paise),
        "subtotal": rupees(order.subtotal_paise),
        "discount": rupees(order.discount_paise),
        "delivery": rupees(order.delivery_paise),
        "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "shipping_address": order.shipping_address,
        "can_cancel": order.status in {"CREATED", "AWAITING_PRESCRIPTION",
                                       "AWAITING_PHARMACIST", "CONFIRMED"},
        "awaiting_payment": (order.payment_method == "ONLINE"
                             and order.payment_status == "PENDING"
                             and order.status == "CREATED"),
        "created_at": order.created_at.isoformat(),
        "items": [
            {
                "name": item.brand_name_snapshot,
                "salt": item.composition_snapshot,
                "pack": item.pack_snapshot,
                "qty": item.quantity,
                "price": rupees(item.unit_price_paise),
                "line_total": rupees(item.line_total_paise),
                "schedule": item.drug_schedule_snapshot,
            }
            for item in order.items
        ],
        "timeline": [
            {"status": e.status, "at": e.created_at.isoformat(), "note": e.note}
            for e in order.events
        ],
    }


def health_record(record) -> dict:
    return {
        "id": f"r{record.id}",
        "type": record.record_type.lower(),
        "title": record.title,
        "by": record.issued_by,
        "date": record.record_date.strftime("%d %b %Y"),
        "source": record.source,
        "has_file": bool(record.file_key),
    }


def family_member(member) -> dict:
    return {
        "id": f"f{member.id}",
        "name": member.full_name,
        "rel": member.relation.title(),
        "is_self": member.is_self,
        "is_minor": member.is_minor,
    }
