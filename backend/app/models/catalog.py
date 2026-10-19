"""Product catalogue.

The important idea here: a *product* (a brand SKU like "Crocin Advance
500mg") never stores its substitutes as a list. Substitution is derived at
query time from a normalised `composition_key`, so adding one new product
automatically makes it a substitute for every existing product that shares
its salts, strengths and dosage form.
"""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class Molecule(db.Model):
    """A canonical active ingredient. Brand labels spell these many ways;
    `MoleculeAlias` maps every observed spelling back to one row here."""

    __tablename__ = "molecules"

    id = db.Column(db.Integer, primary_key=True)
    canonical_name = db.Column(db.String(160), nullable=False, unique=True)
    slug = db.Column(db.String(160), nullable=False, unique=True, index=True)
    atc_code = db.Column(db.String(16), index=True)  # WHO ATC, where known
    is_banned = db.Column(db.Boolean, default=False, nullable=False)
    banned_reference = db.Column(db.String(255))  # CDSCO order reference

    aliases = db.relationship("MoleculeAlias", back_populates="molecule",
                              cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Molecule {self.canonical_name}>"


class MoleculeAlias(db.Model):
    """Alternate spellings: Amoxycillin / Amoxicillin / Amoxycillin Trihydrate."""

    __tablename__ = "molecule_aliases"

    id = db.Column(db.Integer, primary_key=True)
    molecule_id = db.Column(db.Integer, db.ForeignKey("molecules.id"), nullable=False)
    alias = db.Column(db.String(160), nullable=False, index=True)
    normalised = db.Column(db.String(160), nullable=False, index=True)

    molecule = db.relationship("Molecule", back_populates="aliases")

    __table_args__ = (db.UniqueConstraint("molecule_id", "normalised",
                                          name="uq_alias_per_molecule"),)


class Manufacturer(db.Model):
    __tablename__ = "manufacturers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    slug = db.Column(db.String(200), nullable=False, unique=True, index=True)


class Product(db.Model):
    """A sellable pack. One row per brand + strength + pack size."""

    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(64), nullable=False, unique=True, index=True)

    brand_name = db.Column(db.String(240), nullable=False, index=True)
    composition_text = db.Column(db.String(500), nullable=False)  # as printed on the pack
    composition_key = db.Column(db.String(500), nullable=False, index=True)  # derived
    molecule_key = db.Column(db.String(400), nullable=False, index=True)  # salts only, no strength

    dosage_form = db.Column(db.String(60), nullable=False)   # TABLET / CAPSULE / SYRUP
    form_family = db.Column(db.String(40), nullable=False)   # ORAL_SOLID / ORAL_LIQUID / ...
    pack_label = db.Column(db.String(120), nullable=False)   # "Strip of 15 tablets"
    pack_units = db.Column(db.Integer)                       # 15

    manufacturer_id = db.Column(db.Integer, db.ForeignKey("manufacturers.id"))
    manufacturer = db.relationship("Manufacturer")

    mrp_paise = db.Column(db.Integer, nullable=False)        # money in paise, never float
    selling_price_paise = db.Column(db.Integer, nullable=False)

    # Regulatory
    drug_schedule = db.Column(db.String(8), default="OTC", nullable=False)  # OTC/H/H1/X/G
    is_prescription_required = db.Column(db.Boolean, default=False, nullable=False)
    is_nlem = db.Column(db.Boolean, default=False, nullable=False)
    nppa_ceiling_price_paise = db.Column(db.Integer)         # DPCO cap where scheduled
    hsn_code = db.Column(db.String(12))

    # Merchandising
    category = db.Column(db.String(80), index=True)
    is_discontinued = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    rating = db.Column(db.Numeric(2, 1))

    # Editorial content, keyed to the molecule not the brand where possible.
    uses = db.Column(db.Text)            # JSON array
    mechanism = db.Column(db.Text)
    side_effects = db.Column(db.Text)    # JSON array
    directions = db.Column(db.Text)
    storage = db.Column(db.Text)
    content_source = db.Column(db.String(120))     # provenance for every claim
    content_reviewed_by = db.Column(db.String(160))
    content_reviewed_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    ingredients = db.relationship("ProductIngredient", back_populates="product",
                                  cascade="all, delete-orphan")

    __table_args__ = (
        db.Index("ix_products_substitution", "composition_key", "is_active"),
        db.Index("ix_products_molecule", "molecule_key", "is_active"),
    )

    @property
    def discount_percent(self) -> int:
        if not self.mrp_paise:
            return 0
        return round((self.mrp_paise - self.selling_price_paise) * 100 / self.mrp_paise)

    def price_breaches_ceiling(self) -> bool:
        """DPCO ceiling prices are a hard legal cap, not a guideline."""
        if self.nppa_ceiling_price_paise is None:
            return False
        return self.selling_price_paise > self.nppa_ceiling_price_paise


class ProductIngredient(db.Model):
    """One salt + strength within a product. A fixed-dose combination has
    several of these rows."""

    __tablename__ = "product_ingredients"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    molecule_id = db.Column(db.Integer, db.ForeignKey("molecules.id"), nullable=False)

    strength_value = db.Column(db.Numeric(12, 4))
    strength_unit = db.Column(db.String(16))          # as printed
    strength_base_value = db.Column(db.Numeric(16, 6))  # normalised to base unit
    strength_base_unit = db.Column(db.String(16))       # MG / ML / IU
    position = db.Column(db.Integer, default=0, nullable=False)

    product = db.relationship("Product", back_populates="ingredients")
    molecule = db.relationship("Molecule")


class Inventory(db.Model):
    """Stock is per pharmacy, not per product — an e-pharmacy dispenses from
    a licensed premises and the licence number belongs on the record."""

    __tablename__ = "inventory"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey("pharmacies.id"), nullable=False)
    batch_number = db.Column(db.String(64))
    expiry_date = db.Column(db.Date)
    quantity_available = db.Column(db.Integer, default=0, nullable=False)

    product = db.relationship("Product")

    __table_args__ = (db.Index("ix_inventory_lookup", "product_id", "pharmacy_id"),)


class Pharmacy(db.Model):
    __tablename__ = "pharmacies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    drug_licence_number = db.Column(db.String(80), nullable=False)
    licence_valid_till = db.Column(db.Date)
    registered_pharmacist_name = db.Column(db.String(160), nullable=False)
    pharmacist_registration_number = db.Column(db.String(80), nullable=False)
    state = db.Column(db.String(80))
    pincode = db.Column(db.String(10), index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class MoleculeContent(db.Model):
    """Patient information for one active ingredient.

    Kept per molecule, not per brand: one reviewed entry for paracetamol
    covers every paracetamol product. Only `status == "published"` rows are
    shown to patients, and every row carries its source and reviewer.
    """

    __tablename__ = "molecule_content"

    id = db.Column(db.Integer, primary_key=True)
    molecule_id = db.Column(db.Integer, db.ForeignKey("molecules.id"),
                            nullable=False, unique=True)
    uses = db.Column(db.Text)              # JSON array of strings
    how_it_works = db.Column(db.Text)
    side_effects = db.Column(db.Text)      # JSON array of strings
    safety = db.Column(db.Text)            # JSON {topic: {"level": ..., "note": ...}}
    directions = db.Column(db.Text)
    storage = db.Column(db.Text)
    source = db.Column(db.String(255), nullable=False)
    reviewed_by = db.Column(db.String(160))
    reviewed_at = db.Column(db.Date)
    status = db.Column(db.String(12), default="draft", nullable=False)  # draft | published
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    molecule = db.relationship("Molecule")
