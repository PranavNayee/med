"""Substitution engine.

Three tiers, deliberately kept distinct because they carry very different
clinical weight:

  EXACT       same salts, same strengths, same dosage-form family.
              Safe to present as "cheaper substitute".
  RELATED     same salts, different strength or pack. Shown as information
              only, never as a one-tap swap.
  THERAPEUTIC same ATC class, different molecule. A prescriber's decision.
              This tier is computed but NOT returned to patient-facing
              endpoints — it exists for the pharmacist console.

Nothing here invents a substitute. If the composition could not be parsed,
the product is excluded and the API returns an empty list rather than a
guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import select

from ..extensions import db
from ..models.catalog import Molecule, MoleculeAlias, Product
from .composition import parse_composition


class SubstitutionTier(str, Enum):
    EXACT = "EXACT"
    RELATED = "RELATED"
    THERAPEUTIC = "THERAPEUTIC"


@dataclass
class SubstituteResult:
    product: Product
    tier: SubstitutionTier
    saving_paise: int
    saving_percent: int


@dataclass
class SubstitutionSet:
    source: Product
    exact: list[SubstituteResult] = field(default_factory=list)
    related: list[SubstituteResult] = field(default_factory=list)
    best_saving: SubstituteResult | None = None
    unparsed: bool = False


def resolve_alias(raw_molecule: str) -> str:
    """Map a printed molecule token to its canonical name via the alias table.

    Falls back to the input unchanged when no alias exists, so an unseeded
    database still works — just with lower recall on spelling variants.
    """
    normalised = raw_molecule.strip().lower()
    if not normalised:
        return normalised

    row = db.session.execute(
        select(Molecule.canonical_name)
        .join(MoleculeAlias, MoleculeAlias.molecule_id == Molecule.id)
        .where(MoleculeAlias.normalised == normalised)
        .limit(1)
    ).scalar_one_or_none()
    return (row or raw_molecule).strip().lower()


def build_keys(composition_text: str, dosage_form: str,
               pack_label: str = "") -> tuple[str, str, bool]:
    """Return (composition_key, molecule_key, is_unparsed) with aliases applied."""
    parsed = parse_composition(composition_text, dosage_form, pack_label)

    unparsed = any(i.molecule.startswith("unparsed::") for i in parsed.ingredients)

    exact_parts: list[str] = []
    molecule_parts: list[str] = []
    for ingredient in parsed.ingredients:
        canonical = resolve_alias(ingredient.molecule)
        molecule_parts.append(canonical)
        if ingredient.base_value is None:
            exact_parts.append(canonical)
        else:
            from .composition import _format_quantity  # local: formatting helper
            qty = _format_quantity(ingredient.base_value)
            exact_parts.append(f"{canonical}:{qty}{ingredient.base_unit.lower()}")

    composition_key = "|".join(sorted(exact_parts)) + f"|{parsed.form_family}"
    molecule_key = "|".join(sorted(molecule_parts)) + f"|{parsed.form_family}"
    return composition_key, molecule_key, unparsed


def _saving(source: Product, candidate: Product) -> tuple[int, int]:
    delta = source.selling_price_paise - candidate.selling_price_paise
    if delta <= 0 or not source.selling_price_paise:
        return 0, 0
    return delta, round(delta * 100 / source.selling_price_paise)


def find_substitutes(source: Product, *, limit: int = 8,
                     include_related: bool = True) -> SubstitutionSet:
    """Find substitutes for a product, cheapest first."""
    result = SubstitutionSet(source=source)

    if source.composition_key.startswith("unparsed::") or "unparsed::" in source.composition_key:
        result.unparsed = True
        return result

    exact_rows = db.session.execute(
        select(Product)
        .where(
            Product.composition_key == source.composition_key,
            Product.id != source.id,
            Product.is_active.is_(True),
            Product.is_discontinued.is_(False),
        )
        .order_by(Product.selling_price_paise.asc())
        .limit(limit)
    ).scalars().all()

    for candidate in exact_rows:
        # A banned molecule must never surface as a substitute, whatever the
        # catalogue says about availability.
        if _has_banned_molecule(candidate):
            continue
        saving, pct = _saving(source, candidate)
        result.exact.append(
            SubstituteResult(product=candidate, tier=SubstitutionTier.EXACT,
                             saving_paise=saving, saving_percent=pct)
        )

    if include_related:
        related_rows = db.session.execute(
            select(Product)
            .where(
                Product.molecule_key == source.molecule_key,
                Product.composition_key != source.composition_key,
                Product.id != source.id,
                Product.is_active.is_(True),
                Product.is_discontinued.is_(False),
            )
            .order_by(Product.selling_price_paise.asc())
            .limit(limit)
        ).scalars().all()

        for candidate in related_rows:
            if _has_banned_molecule(candidate):
                continue
            saving, pct = _saving(source, candidate)
            result.related.append(
                SubstituteResult(product=candidate, tier=SubstitutionTier.RELATED,
                                 saving_paise=saving, saving_percent=pct)
            )

    cheaper = [r for r in result.exact if r.saving_paise > 0]
    if cheaper:
        result.best_saving = max(cheaper, key=lambda r: r.saving_paise)

    return result


def _has_banned_molecule(product: Product) -> bool:
    for ingredient in product.ingredients:
        if ingredient.molecule and ingredient.molecule.is_banned:
            return True
    return False
