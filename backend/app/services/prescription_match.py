"""Match lines read from a prescription to catalogue products.

For each line the result says which product we think was prescribed and
which products carry exactly the same composition for less. "Same
composition" is the EXACT substitution tier: same salts, same strengths,
same form family. Nothing here proposes a different medicine for the same
condition; that is a prescriber's decision.

Two ways a line can match:

  brand        the written name is a brand in the catalogue ("Augmentin 625
               Duo"). The brand is the match, and the cheaper options are its
               exact substitutes.
  composition  the prescriber wrote a generic ("Amoxicillin 500 mg + Clav
               125 mg"). There is no brand to compare against, so the options
               are every product with that composition, cheapest first.

When the brand match is weak (the written strength does not appear, or the
form contradicts what was written) the line is marked `needs_review` and the
other close candidates are returned so the patient can pick the right one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import product

from sqlalchemy import select

from ..extensions import db
from ..models import Molecule, MoleculeAlias, Product
from .composition import normalise_form, parse_composition
from .substitution import (
    SubstituteResult, build_keys, find_substitutes, resolve_alias,
)

#: Words prescribers put in front of a name that are not part of it.
_PREFIXES = {
    "tab", "tabs", "tablet", "t", "cap", "caps", "capsule", "c", "syp", "syr",
    "syrup", "susp", "inj", "injection", "oint", "ointment", "crm", "cream",
    "gel", "drop", "drops", "dr", "sol", "lot", "inh", "rx",
}
#: Brand-name words that describe the pack, not the formulation. Release
#: modifiers (SR, ER, CR, MR, XR) are deliberately absent: they matter.
_FORM_WORDS = {
    "tablet", "tablets", "tab", "capsule", "capsules", "cap", "syrup", "suspension",
    "susp", "drop", "drops", "oral", "solution", "injection", "inj", "vial",
    "cream", "ointment", "gel", "lotion", "inhaler", "rotacap", "respules",
    "sachet", "powder", "granules", "mg", "ml", "gm", "g", "strip", "bottle",
}
_SPLIT = re.compile(r"\s*(?:\+|&|,|/|\band\b)\s*", re.IGNORECASE)
#: What a leading prefix says about the form, when no form was given.
_PREFIX_FORMS = {
    "tab": "tablet", "tabs": "tablet", "tablet": "tablet", "t": "tablet",
    "cap": "capsule", "caps": "capsule", "capsule": "capsule", "c": "capsule",
    "syp": "syrup", "syr": "syrup", "syrup": "syrup", "susp": "suspension",
    "inj": "injection", "injection": "injection", "oint": "ointment",
    "ointment": "ointment", "crm": "cream", "cream": "cream", "gel": "gel",
    "drop": "drops", "drops": "drops", "inh": "inhaler",
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_QUANTITY = re.compile(r"\d+(?:\.\d+)?(?:mg|mcg|ml|g|gm|iu|kg)?")
_WORD = re.compile(r"[a-z0-9]+(?:\.\d+)?")

MAX_CANDIDATES = 200
MAX_OPTIONS = 6
MAX_SPELLINGS = 24


@dataclass
class LineMatch:
    basis: str | None = None             # "brand" | "composition" | None
    product: Product | None = None
    needs_review: bool = False
    candidates: list[Product] = field(default_factory=list)
    options: list[SubstituteResult | Product] = field(default_factory=list)
    best_saving: SubstituteResult | None = None


def match_line(name: str, strength: str = "", form: str = "") -> LineMatch:
    tokens, prefix = _name_tokens(name)
    numbers = set(_NUMBER.findall(f"{name} {strength}"))
    form = _expand_form(form)
    wanted_form, wanted_family = normalise_form(form or _PREFIX_FORMS.get(prefix, "") or name)

    # A generic name ("Paracetamol 650 mg") is matched on composition first;
    # otherwise a product that happens to be branded "Paracetamol" at another
    # strength would win on name alone.
    if _is_known_molecule(name):
        found = _match_composition(name, strength, form or _PREFIX_FORMS.get(prefix, ""))
        if found.options:
            return found

    ranked = _rank_brand_candidates(tokens, numbers, wanted_form, wanted_family)
    if ranked:
        top_score, best, strong = ranked[0]
        # Close runners-up with a different composition mean the prescription
        # does not pin the product down ("Crocin" with no strength).
        # Keys are compared after alias resolution: seeded and imported rows
        # spell the same salt differently (Amoxicillin / Amoxycillin).
        best_key = _resolved_key(best)
        ambiguous = any(score >= top_score - 3 and _resolved_key(p) != best_key
                        for score, p, _ in ranked[1:6])
        # No strength written, but the brand carries one: that is our guess.
        guessed_strength = not numbers and bool(_NUMBER.search(best.brand_name))
        result = LineMatch(basis="brand", product=best,
                           needs_review=not strong or ambiguous or guessed_strength,
                           candidates=[p for _, p, _ in ranked[1:5]])
        subs = find_substitutes(best, limit=MAX_OPTIONS, include_related=False)
        result.options = subs.exact
        result.best_saving = subs.best_saving
        return result

    return _match_composition(name, strength, form)


def _expand_form(form: str) -> str:
    """ "Syp." -> "syrup", "Tab" -> "tablet"; anything else unchanged."""
    words = _WORD.findall((form or "").lower())
    if words and words[0] in _PREFIX_FORMS:
        return _PREFIX_FORMS[words[0]]
    return form or ""


def _resolved_key(product: Product) -> str:
    return build_keys(product.composition_text, product.dosage_form)[0]


def _name_tokens(name: str) -> tuple[list[str], str]:
    """Words of the name without leading "Tab."/"Syp" etc., and that prefix."""
    words = _WORD.findall((name or "").lower())
    prefix = ""
    while words and words[0] in _PREFIXES:
        prefix = prefix or words[0]
        words.pop(0)
    return words, prefix


def _is_known_molecule(name: str) -> bool:
    first = _SPLIT.split(re.sub(r"\d.*$", "", name or ""))[0]
    tokens, _ = _name_tokens(first)
    if not tokens:
        return False
    canonical = resolve_alias(" ".join(tokens))
    return db.session.execute(
        select(Molecule.id).where(Molecule.canonical_name == canonical).limit(1)
    ).first() is not None


def _rank_brand_candidates(tokens: list[str], numbers: set[str], wanted_form: str,
                           wanted_family: str) -> list[tuple[int, Product, bool]]:
    lead = next((t for t in tokens if not t.isdigit() and len(t) >= 3), None)
    if lead is None:
        return []

    rows = db.session.execute(
        select(Product)
        .where(Product.brand_name.ilike(f"{lead}%"),
               Product.is_active.is_(True),
               Product.is_discontinued.is_(False))
        .limit(MAX_CANDIDATES)
    ).scalars().all()

    words = [t for t in tokens if not t.isdigit()]
    written = set(tokens)
    ranked = []
    for product in rows:
        brand_words = set(_WORD.findall(product.brand_name.lower()))
        name_numbers = set(_NUMBER.findall(product.brand_name))
        composition_numbers = set(_NUMBER.findall(product.composition_text))

        # The written name must start the brand as a whole word, or it is a
        # different product that merely shares a prefix ("Dolo" vs "Dolokind").
        if words and words[0] not in brand_words:
            continue
        score = 10 * sum(1 for w in words if w in brand_words)

        number_hit = bool(numbers & (name_numbers | composition_numbers))
        if numbers:
            score += 8 if numbers & name_numbers else (4 if number_hit else -6)

        family_ok = wanted_family == "UNKNOWN" or product.form_family == wanted_family
        score += 4 if family_ok else -8
        if wanted_family != "UNKNOWN":
            # Same family, different form: drops are not the syrup written.
            product_form, _ = normalise_form(product.dosage_form)
            score += 5 if product_form == wanted_form else -3
        score += 3 * len(brand_words & written & _FORM_WORDS)

        # A brand word the prescriber did not write usually means another
        # product: "Pan-D" adds domperidone to "Pan 40", "SR" changes release.
        extras = {w for w in brand_words - written - _FORM_WORDS
                  if not _QUANTITY.fullmatch(w)}
        score -= 6 * len(extras)

        strong = (not numbers or number_hit) and family_ok and not extras
        ranked.append((score, product, strong))

    ranked.sort(key=lambda t: (-t[0], t[1].selling_price_paise))
    return ranked


def _match_composition(name: str, strength: str, form: str) -> LineMatch:
    dosage_form = form or "Tablet"
    keys = {
        key
        for text in _composition_texts(name, strength)
        for key in (parse_composition(text, dosage_form).composition_key,
                    build_keys(text, dosage_form)[0])
        if "unparsed::" not in key
    }
    if not keys:
        return LineMatch()

    rows = db.session.execute(
        select(Product)
        .where(Product.composition_key.in_(keys),
               Product.is_active.is_(True),
               Product.is_discontinued.is_(False))
        .order_by(Product.selling_price_paise.asc())
        .limit(MAX_OPTIONS)
    ).scalars().all()
    if not rows:
        return LineMatch()
    return LineMatch(basis="composition", options=list(rows))


def _composition_texts(name: str, strength: str) -> list[str]:
    """Composition strings to try for a generic line.

    Pairs salts with strengths written separately ("Amoxicillin + Clavulanic
    acid", "500 mg + 125 mg"), then expands each salt into its known
    spellings, because imported rows keep the label's spelling (Amoxycillin)
    while the alias table knows it as Amoxicillin.
    """
    names = [n.strip() for n in _SPLIT.split(name or "") if n.strip()]
    strengths = [x.strip() for x in _SPLIT.split(strength or "") if x.strip()]
    if len(names) == len(strengths) > 0:
        fragments = [f"{n} ({x})" for n, x in zip(names, strengths)]
    elif len(names) == 1 and strength:
        fragments = [f"{names[0]} ({strength})"]
    else:
        fragments = names

    spellings = []
    for fragment in fragments:
        molecule = re.sub(r"\(.*?\)", "", fragment).strip()
        dose = fragment[len(molecule):].strip()
        variants = [molecule] + [a for a in _alias_spellings(molecule)
                                 if a.lower() != molecule.lower()]
        spellings.append([f"{v} {dose}".strip() for v in variants])

    texts = []
    for combo in product(*spellings):
        texts.append(" + ".join(combo))
        if len(texts) >= MAX_SPELLINGS:
            break
    return texts


def _alias_spellings(molecule: str) -> list[str]:
    canonical = resolve_alias(molecule)
    rows = db.session.execute(
        select(MoleculeAlias.alias)
        .join(Molecule, MoleculeAlias.molecule_id == Molecule.id)
        .where(Molecule.canonical_name == canonical)
    ).scalars().all()
    return [canonical] + list(rows)
