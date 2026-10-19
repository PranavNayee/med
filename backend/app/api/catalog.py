"""Pharmacy catalogue endpoints."""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import or_, select

from ..extensions import db
from ..models import Product
from ..serializers import product_card, product_detail, rupees, substitute
from ..services.substitution import find_substitutes

bp = Blueprint("catalog", __name__, url_prefix="/api/catalog")

MAX_PAGE_SIZE = 48


@bp.get("/products")
def list_products():
    """Search and browse. Matches brand, composition and category together,
    because people search by brand ("Dolo") far more often than by salt."""
    query = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    size = min(request.args.get("size", 24, type=int), MAX_PAGE_SIZE)

    stmt = select(Product).where(
        Product.is_active.is_(True),
        Product.is_discontinued.is_(False),
    )

    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                Product.brand_name.ilike(like),
                Product.composition_text.ilike(like),
                Product.molecule_key.ilike(like),
            )
        )
    if category and category.lower() != "all":
        stmt = stmt.where(Product.category == category)

    total = db.session.execute(
        select(db.func.count()).select_from(stmt.subquery())
    ).scalar_one()

    rows = db.session.execute(
        stmt.order_by(Product.brand_name.asc())
        .offset((page - 1) * size)
        .limit(size)
    ).scalars().all()

    return jsonify({
        "items": [product_card(p) for p in rows],
        "page": page, "size": size, "total": total,
    })


@bp.get("/products/<sku>")
def get_product(sku: str):
    product = db.session.execute(
        select(Product).where(Product.sku == sku, Product.is_active.is_(True))
    ).scalar_one_or_none()
    if product is None:
        return jsonify({"error": "not_found"}), 404

    payload = product_detail(product)

    # Price-cap compliance is surfaced, not hidden. If a listing breaches a
    # DPCO ceiling the API says so rather than quietly selling it.
    if product.price_breaches_ceiling():
        payload["compliance_warning"] = {
            "code": "PRICE_ABOVE_NPPA_CEILING",
            "ceiling": rupees(product.nppa_ceiling_price_paise),
        }

    return jsonify(payload)


@bp.get("/products/<sku>/substitutes")
def get_substitutes(sku: str):
    product = db.session.execute(
        select(Product).where(Product.sku == sku, Product.is_active.is_(True))
    ).scalar_one_or_none()
    if product is None:
        return jsonify({"error": "not_found"}), 404

    result = find_substitutes(product)

    return jsonify({
        "source": product_card(product),
        "unparsed": result.unparsed,
        "exact": [substitute(r) for r in result.exact],
        "related": [substitute(r) for r in result.related],
        "best_saving": substitute(result.best_saving) if result.best_saving else None,
        "note": (
            "Same-salt substitutes contain the identical active ingredient at the "
            "same strength. Related products differ in strength or pack and are "
            "shown for information only — do not switch to one without asking "
            "your doctor or pharmacist."
        ),
    })


@bp.get("/categories")
def list_categories():
    rows = db.session.execute(
        select(Product.category, db.func.count(Product.id))
        .where(Product.is_active.is_(True), Product.category.isnot(None))
        .group_by(Product.category)
        .order_by(Product.category.asc())
    ).all()
    return jsonify({"items": [{"name": name, "count": count} for name, count in rows]})
