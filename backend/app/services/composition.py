"""Composition normalisation.

This is the heart of the catalogue. Indian pack labels write the same
formulation many different ways:

    "Paracetamol (500mg)"
    "Paracetamol 500 mg"
    "PARACETAMOL IP 500MG"
    "Acetaminophen 0.5g"

All four must reduce to the same key, or substitution silently fails and
the customer is shown nothing. The output is deterministic and sorted, so
two products with the same formulation always produce byte-identical keys.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------
# Unit handling
# --------------------------------------------------------------------------

#: Conversion into a base unit. Weight -> MG, volume -> ML, activity -> IU.
UNIT_CONVERSIONS: dict[str, tuple[str, Decimal]] = {
    "mcg": ("MG", Decimal("0.001")),
    "µg": ("MG", Decimal("0.001")),
    "ug": ("MG", Decimal("0.001")),
    "mg": ("MG", Decimal("1")),
    "g": ("MG", Decimal("1000")),
    "gm": ("MG", Decimal("1000")),
    "kg": ("MG", Decimal("1000000")),
    "ml": ("ML", Decimal("1")),
    "l": ("ML", Decimal("1000")),
    "iu": ("IU", Decimal("1")),
    "u": ("IU", Decimal("1")),
    "%": ("PCT", Decimal("1")),
}

#: Dosage forms grouped into families. Substitution is only offered within a
#: family — a syrup is not a swap for a tablet even at identical strength.
FORM_FAMILIES: dict[str, str] = {
    "tablet": "ORAL_SOLID", "tab": "ORAL_SOLID", "caplet": "ORAL_SOLID",
    "capsule": "ORAL_SOLID", "cap": "ORAL_SOLID", "softgel": "ORAL_SOLID",
    "syrup": "ORAL_LIQUID", "suspension": "ORAL_LIQUID", "solution": "ORAL_LIQUID",
    "drops": "ORAL_LIQUID", "elixir": "ORAL_LIQUID",
    "injection": "PARENTERAL", "vial": "PARENTERAL", "ampoule": "PARENTERAL",
    "cream": "TOPICAL", "ointment": "TOPICAL", "gel": "TOPICAL", "lotion": "TOPICAL",
    "inhaler": "INHALATION", "respule": "INHALATION", "rotacap": "INHALATION",
    "eye drops": "OPHTHALMIC", "ear drops": "OTIC",
    "sachet": "ORAL_POWDER", "powder": "ORAL_POWDER", "granules": "ORAL_POWDER",
}

#: Noise words that appear on labels but carry no formulation meaning.
_NOISE = {
    "ip", "bp", "usp", "ph", "eur", "jp", "nf",
    "anhydrous", "equivalent", "eq", "to", "as",
}

#: Salt-form suffixes. These change the *molecule* string but not the
#: therapeutic identity, so they are stripped for grouping while the full
#: printed text is retained on the product for display.
_SALT_SUFFIXES = (
    "hydrochloride", "hcl", "sodium", "potassium", "calcium", "magnesium",
    "sulphate", "sulfate", "phosphate", "maleate", "tartrate", "citrate",
    "besylate", "mesylate", "succinate", "fumarate", "acetate", "trihydrate",
    "dihydrate", "monohydrate", "hemihydrate", "orotate", "gluconate",
)

_SPLIT_PATTERN = re.compile(r"\s*(?:\+|&|,|/|\band\b)\s*", flags=re.IGNORECASE)
_STRENGTH_PATTERN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mcg|µg|ug|mg|gm|g|kg|ml|l|iu|u|%)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class Ingredient:
    """One normalised salt + strength."""

    molecule: str                  # canonical, lowercase, salt-suffix stripped
    display_name: str              # as printed, cleaned
    strength_value: Decimal | None
    strength_unit: str | None      # as printed
    base_value: Decimal | None     # converted
    base_unit: str | None          # MG / ML / IU / PCT

    def key(self) -> str:
        if self.base_value is None:
            return self.molecule
        return f"{self.molecule}:{_format_quantity(self.base_value)}{self.base_unit.lower()}"


@dataclass(frozen=True)
class Composition:
    """The full normalised formulation of a product."""

    ingredients: tuple[Ingredient, ...]
    dosage_form: str
    form_family: str

    @property
    def composition_key(self) -> str:
        """Exact-substitute key: same salts, same strengths, same form family."""
        parts = sorted(i.key() for i in self.ingredients)
        return "|".join(parts) + f"|{self.form_family}"

    @property
    def molecule_key(self) -> str:
        """Looser key: same salts, ignoring strength. Used for 'related', which
        is NEVER presented to a patient as a like-for-like swap."""
        parts = sorted(i.molecule for i in self.ingredients)
        return "|".join(parts) + f"|{self.form_family}"

    @property
    def is_combination(self) -> bool:
        return len(self.ingredients) > 1


def _format_quantity(value: Decimal) -> str:
    """Render a Decimal without scientific notation.

    Decimal('500').normalize() gives '5E+2', which would silently produce
    two different keys for the same strength. Always emit plain digits.
    """
    quantised = value.quantize(Decimal("0.0001")).normalize()
    text = format(quantised, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def _clean_molecule(raw: str) -> str:
    """Reduce a printed salt name to a canonical comparison token."""
    text = _strip_accents(raw).lower()
    text = re.sub(r"\(.*?\)", " ", text)             # parenthetical strengths
    text = _STRENGTH_PATTERN.sub(" ", text)          # inline strengths: "hcl 0.5g"
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    text = re.sub(r"\b\d+(?:[.,]\d+)?\b", " ", text)  # bare leftover numbers
    tokens = [t for t in text.split() if t and t not in _NOISE]

    # Strip trailing salt-form words: "metformin hydrochloride" -> "metformin".
    # Only when something remains: "calcium" on its own IS the ingredient.
    while len(tokens) > 1 and tokens[-1] in _SALT_SUFFIXES:
        tokens.pop()

    return " ".join(tokens).strip()


def _parse_strength(fragment: str) -> tuple[Decimal | None, str | None,
                                             Decimal | None, str | None]:
    match = _STRENGTH_PATTERN.search(fragment)
    if not match:
        return None, None, None, None

    raw_value, raw_unit = match.group(1), match.group(2).lower()
    try:
        value = Decimal(raw_value.replace(",", "."))
    except InvalidOperation:
        return None, None, None, None

    base_unit, factor = UNIT_CONVERSIONS.get(raw_unit, (None, None))
    if base_unit is None:
        return value, raw_unit, None, None
    return value, raw_unit, (value * factor), base_unit


def normalise_form(raw_form: str, pack_label: str = "") -> tuple[str, str]:
    """Return (canonical dosage form, form family)."""
    haystack = f"{raw_form} {pack_label}".lower()
    for token, family in FORM_FAMILIES.items():
        if token in haystack:
            return token.upper().replace(" ", "_"), family
    return (raw_form or "UNKNOWN").upper().replace(" ", "_"), "UNKNOWN"


def parse_composition(composition_text: str, dosage_form: str,
                      pack_label: str = "") -> Composition:
    """Parse a printed composition string into a normalised Composition.

    >>> c = parse_composition("Metformin Hydrochloride (500mg)", "Tablet")
    >>> c.composition_key
    'metformin:500mg|ORAL_SOLID'
    >>> parse_composition("Metformin HCl 0.5g", "Tab").composition_key
    'metformin:500mg|ORAL_SOLID'
    """
    form, family = normalise_form(dosage_form, pack_label)

    fragments = [f for f in _SPLIT_PATTERN.split(composition_text or "") if f.strip()]
    ingredients: list[Ingredient] = []

    for fragment in fragments:
        molecule = _clean_molecule(fragment)
        if not molecule:
            continue
        value, unit, base_value, base_unit = _parse_strength(fragment)
        ingredients.append(
            Ingredient(
                molecule=molecule,
                display_name=fragment.strip(),
                strength_value=value,
                strength_unit=unit,
                base_value=base_value,
                base_unit=base_unit,
            )
        )

    if not ingredients:
        # Never invent a key. An unparseable composition is excluded from
        # substitution entirely rather than grouped with something wrong.
        ingredients = [Ingredient(
            molecule=f"unparsed::{_clean_molecule(composition_text) or 'unknown'}",
            display_name=composition_text,
            strength_value=None, strength_unit=None,
            base_value=None, base_unit=None,
        )]

    return Composition(ingredients=tuple(ingredients),
                       dosage_form=form, form_family=family)
