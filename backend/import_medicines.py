"""Import the Indian medicine dataset into the Nirog catalogue.

    python import_medicines.py data/A_Z_medicines_dataset_of_India.csv --reset-catalog
    python import_medicines.py data/A_Z_medicines_dataset_of_India.csv --limit 5000
    python import_medicines.py data/A_Z_medicines_dataset_of_India.csv --demo 2500 --demo-out demo.sqlite3

Expected CSV columns:
    id, name, price(₹), Is_discontinued, manufacturer_name, type,
    pack_size_label, short_composition1, short_composition2

The importer reuses `services.composition.parse_composition`, so every
imported product gets the same `composition_key` the seeded products get and
drops straight into the existing substitution engine with no extra mapping.

THE SCHEDULE PROBLEM
--------------------
This dataset carries no regulatory schedule, and `rx_policy` needs one for
every product. Guessing OTC would put antibiotics and benzodiazepines into an
unauthenticated basket, so this module classifies by active molecule against
three curated lists and defaults everything it does not recognise to
Schedule H — prescription required. Failing closed produces some
over-restriction, which a pharmacist can relax. Failing open would produce an
offence under the Drugs and Cosmetics Act, which nobody can undo.

Every row records how its schedule was decided in `schedule_basis`, and
`--report` writes out the ones that were defaulted so a pharmacist can work
through them.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app import create_app                              # noqa: E402
from app.extensions import db                           # noqa: E402
from app.models import (                                # noqa: E402
    Manufacturer, Molecule, MoleculeAlias, Product, ProductIngredient,
)
from app.services.composition import parse_composition  # noqa: E402

csv.field_size_limit(10 ** 7)

# ---------------------------------------------------------------------------
# Schedule classification
# ---------------------------------------------------------------------------

#: Schedule X and NDPS. `rx_policy.REFUSED_SCHEDULES` blocks these from online
#: sale outright — they need a dual-copy prescription and a bound register.
SCHEDULE_X = {
    "amobarbital", "amphetamine", "barbitone", "phenobarbitone", "phenobarbital",
    "secobarbital", "cyclobarbital", "pentobarbital", "methaqualone",
    "methylphenidate", "dexamphetamine", "methamphetamine", "glutethimide",
    "ethchlorvynol", "phenmetrazine", "phencyclidine", "pethidine",
    "pentazocine", "secbutabarbital",
}
NARCOTIC = {
    "morphine", "fentanyl", "buprenorphine", "codeine", "oxycodone",
    "methadone", "hydromorphone", "tapentadol", "tramadol", "dihydrocodeine",
}

#: Schedule H1 — the published list. Sale requires an additional bound
#: register recording the prescriber and the patient, retained three years.
SCHEDULE_H1 = {
    # Cephalosporins and carbapenems
    "cefdinir", "cefditoren", "cefepime", "cefetamet", "cefixime",
    "cefoperazone", "cefotaxime", "cefpirome", "cefpodoxime", "ceftazidime",
    "ceftibuten", "ceftizoxime", "ceftriaxone", "cefuroxime", "cefadroxil",
    "doripenem", "ertapenem", "imipenem", "meropenem", "faropenem",
    # Other reserve antibacterials
    "sulbactam", "tigecycline", "colistin", "linezolid", "vancomycin",
    "teicoplanin", "polymyxin",
    # Fluoroquinolones
    "moxifloxacin", "levofloxacin", "sparfloxacin", "gatifloxacin",
    "ofloxacin", "ciprofloxacin", "norfloxacin", "prulifloxacin",
    # Anti-tubercular
    "isoniazid", "rifampicin", "rifampin", "pyrazinamide", "ethambutol",
    "ethionamide", "cycloserine", "capreomycin", "clofazimine",
    "thiacetazone", "aminosalicylic acid", "bedaquiline", "delamanid",
    # Habit-forming / dependence risk
    "alprazolam", "chlordiazepoxide", "clonazepam", "diazepam",
    "diphenoxylate", "lorazepam", "midazolam", "nitrazepam", "zolpidem",
    "zopiclone", "etizolam", "flunitrazepam", "triazolam",
    # Anti-diabetic sulfonylureas / others on the list
    "glibenclamide", "gliclazide", "glimepiride", "glipizide",
    # Antivirals on the list
    "oseltamivir", "zanamivir",
}

#: Molecules commonly sold across the counter in India. Nothing here carries a
#: Schedule H entry; the list is deliberately conservative.
OTC = {
    "paracetamol", "acetaminophen", "ibuprofen", "aspirin", "cetirizine",
    "chlorpheniramine", "dextromethorphan", "guaifenesin", "ambroxol",
    "bromhexine", "menthol", "camphor", "eucalyptus oil",
    "aluminium hydroxide", "magnesium hydroxide", "sodium bicarbonate",
    "calcium carbonate", "simethicone", "dimethicone", "activated charcoal",
    "oral rehydration salts", "electrolyte", "dextrose", "glucose",
    "ascorbic acid", "vitamin c", "cholecalciferol", "ergocalciferol",
    "cyanocobalamin", "methylcobalamin", "folic acid", "thiamine",
    "riboflavin", "niacinamide", "pyridoxine", "biotin", "tocopherol",
    "vitamin e", "retinol", "zinc", "ferrous ascorbate", "ferrous fumarate",
    "ferrous sulphate", "ferrous sulfate", "carbonyl iron", "calcium citrate",
    "povidone iodine", "chlorhexidine", "hydrogen peroxide", "boric acid",
    "glycerin", "glycerine", "liquid paraffin", "lactulose", "ispaghula",
    "psyllium", "isabgol", "senna", "sodium picosulphate",
    "clotrimazole", "miconazole", "terbinafine", "calamine", "zinc oxide",
    "sucralfate", "lactic acid bacillus", "saccharomyces boulardii",
    "diclofenac diethylamine", "methyl salicylate", "capsaicin",
    "sodium chloride", "carboxymethylcellulose", "hypromellose",
    "xylometazoline", "oxymetazoline", "sodium cromoglycate",
    "silver sulphadiazine", "framycetin", "mupirocin",
}

#: Ranked most-restrictive first — a combination takes its strictest member.
_LADDER = [
    ("NARCOTIC", NARCOTIC),
    ("X", SCHEDULE_X),
    ("H1", SCHEDULE_H1),
]


def classify_schedule(molecules: list[str]) -> tuple[str, str]:
    """Return (schedule, basis). Anything unrecognised defaults to H."""
    hay = [m.lower() for m in molecules if m and not m.startswith("unparsed::")]
    if not hay:
        return "H", "DEFAULTED_UNPARSEABLE"

    for schedule, table in _LADDER:
        for molecule in hay:
            for entry in table:
                if entry in molecule:
                    return schedule, f"LIST_MATCH:{entry}"

    # OTC only when EVERY active molecule is on the OTC list. One unknown
    # ingredient in a combination pulls the whole pack back to H.
    matched = 0
    for molecule in hay:
        if any(entry in molecule for entry in OTC):
            matched += 1
    if matched == len(hay):
        return "OTC", "LIST_MATCH:otc_all_ingredients"

    return "H", "DEFAULTED_NOT_ON_LIST"


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------

_FORM_HINTS = [
    ("eye drop", "Eye Drops"), ("ear drop", "Ear Drops"),
    ("nasal spray", "Nasal Spray"), ("respule", "Respule"),
    ("rotacap", "Rotacap"), ("inhaler", "Inhaler"), ("rotahaler", "Inhaler"),
    ("injection", "Injection"), ("infusion", "Injection"),
    ("suspension", "Suspension"), ("syrup", "Syrup"), ("solution", "Solution"),
    ("elixir", "Elixir"), ("drop", "Drops"),
    ("capsule", "Capsule"), ("tablet", "Tablet"),
    ("cream", "Cream"), ("ointment", "Ointment"), ("gel", "Gel"),
    ("lotion", "Lotion"), ("soap", "Soap"), ("shampoo", "Shampoo"),
    ("powder", "Powder"), ("granule", "Granules"), ("sachet", "Sachet"),
    ("suppositor", "Suppository"), ("patch", "Patch"), ("spray", "Spray"),
]

_PACK_UNITS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ml|gm|g\b|tablet|capsule|"
                         r"unit|piece|sachet|vial|ampoule|strip)", re.I)
_LEADING_COUNT = re.compile(r"of\s+(\d+(?:\.\d+)?)", re.I)


def detect_form(name: str, pack_label: str) -> str:
    hay = f"{pack_label} {name}".lower()
    for token, form in _FORM_HINTS:
        if token in hay:
            return form
    return "Tablet"


def pack_units(pack_label: str) -> int | None:
    m = _LEADING_COUNT.search(pack_label or "")
    if m:
        try:
            return int(float(m.group(1)))
        except ValueError:
            pass
    m = _PACK_UNITS.search(pack_label or "")
    if m:
        try:
            return int(float(m.group(1)))
        except ValueError:
            pass
    return None


def category_for(molecules: list[str], schedule: str) -> str:
    hay = " ".join(molecules)
    table = [
        ("Antibiotic", ("cillin", "cef", "azithromycin", "floxacin",
                        "mycin", "penem", "doxycycline", "metronidazole",
                        "linezolid", "cycline")),
        ("Diabetes", ("metformin", "glipizide", "glimepiride", "gliclazide",
                      "insulin", "sitagliptin", "vildagliptin", "dapagliflozin",
                      "empagliflozin", "pioglitazone")),
        ("Cardiac", ("atenolol", "metoprolol", "amlodipine", "telmisartan",
                     "losartan", "ramipril", "enalapril", "atorvastatin",
                     "rosuvastatin", "clopidogrel", "nebivolol", "olmesartan")),
        ("Pain relief", ("paracetamol", "ibuprofen", "diclofenac", "aceclofenac",
                         "naproxen", "aspirin", "tramadol", "etoricoxib")),
        ("Gastro", ("omeprazole", "pantoprazole", "rabeprazole", "esomeprazole",
                    "ranitidine", "domperidone", "ondansetron", "sucralfate",
                    "lactulose", "antacid")),
        ("Respiratory", ("salbutamol", "levosalbutamol", "budesonide",
                         "montelukast", "formoterol", "theophylline",
                         "ambroxol", "guaifenesin")),
        ("Allergy", ("cetirizine", "levocetirizine", "loratadine",
                     "fexofenadine", "chlorpheniramine", "hydroxyzine")),
        ("Vitamins & supplements", ("vitamin", "calciferol", "cobalamin",
                                    "ascorbic", "folic", "ferrous", "zinc",
                                    "calcium", "multivitamin", "biotin")),
        ("Skin care", ("clotrimazole", "ketoconazole", "terbinafine",
                       "betamethasone", "clobetasol", "mometasone",
                       "adapalene", "benzoyl", "permethrin")),
        ("Mental health", ("sertraline", "escitalopram", "fluoxetine",
                           "alprazolam", "clonazepam", "olanzapine",
                           "risperidone", "quetiapine", "amitriptyline")),
    ]
    for label, tokens in table:
        if any(t in hay for t in tokens):
            return label
    return "Prescription medicine" if schedule in {"H", "H1"} else "General"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "unknown"


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_csv(path: Path, limit: int | None, reset: bool,
               batch: int, report: Path | None,
               only_skus: set[str] | None = None) -> dict:
    app = create_app()
    stats = Counter()
    schedules = Counter()
    started = time.time()

    with app.app_context():
        if reset:
            # Ingredients first — they carry the FK.
            db.session.query(ProductIngredient).delete()
            db.session.query(Product).delete()
            db.session.commit()
            print("cleared existing products and ingredients")

        mfr_ids: dict[str, int] = {
            m.name: m.id for m in db.session.query(Manufacturer).all()
        }
        mol_ids: dict[str, int] = {}
        # The alias table is loaded once. Calling substitution.resolve_alias
        # per ingredient costs one query each (~370k), which is invisible
        # against a local database and ~40 minutes against a hosted one.
        alias_map: dict[str, str] = {
            normalised: canonical.strip().lower()
            for normalised, canonical in db.session.query(
                MoleculeAlias.normalised, Molecule.canonical_name
            ).join(Molecule, MoleculeAlias.molecule_id == Molecule.id)
        }

        def resolve_alias(raw: str) -> str:
            # Same result as substitution.resolve_alias, without the query.
            normalised = raw.strip().lower()
            return alias_map.get(normalised, normalised) if normalised else normalised

        used_slugs: set[str] = set()
        for m in db.session.query(Molecule).all():
            mol_ids[m.canonical_name] = m.id
            used_slugs.add(m.slug)

        def unique_slug(text: str) -> str:
            base = slugify(text)[:150]
            slug, n = base, 1
            while slug in used_slugs:
                n += 1
                slug = f"{base}-{n}"
            used_slugs.add(slug)
            return slug
        used_mfr_slugs: set[str] = {
            m.slug for m in db.session.query(Manufacturer).all()
        }

        def unique_mfr_slug(text: str) -> str:
            base = slugify(text)[:190]
            slug, n = base, 1
            while slug in used_mfr_slugs:
                n += 1
                slug = f"{base}-{n}"
            used_mfr_slugs.add(slug)
            return slug

        seen_skus: set[str] = {
            sku for (sku,) in db.session.query(Product.sku).all()
        }

        product_rows: list[dict] = []
        ingredient_specs: list[tuple[str, list]] = []
        defaulted: list[tuple] = []

        def flush() -> None:
            """Write one batch. Products first, then their ingredients, so the
            FK is satisfiable without holding every ORM object in memory."""
            if not product_rows:
                return
            db.session.bulk_insert_mappings(Product, product_rows)
            db.session.flush()

            skus = [r["sku"] for r in product_rows]
            id_by_sku = dict(
                db.session.query(Product.sku, Product.id)
                .filter(Product.sku.in_(skus)).all()
            )
            ing_rows = []
            for sku, ings in ingredient_specs:
                pid = id_by_sku.get(sku)
                if pid is None:
                    continue
                for position, ing in enumerate(ings):
                    canonical = resolve_alias(ing.molecule)
                    mid = mol_ids.get(canonical)
                    if mid is None:
                        molecule = Molecule(canonical_name=canonical[:160],
                                            slug=unique_slug(canonical))
                        db.session.add(molecule)
                        db.session.flush()
                        mol_ids[canonical] = mid = molecule.id
                        db.session.add(MoleculeAlias(
                            molecule_id=mid, alias=canonical[:160],
                            normalised=canonical.lower()[:160]))
                        alias_map.setdefault(canonical.lower()[:160], canonical)
                        stats["molecules_created"] += 1
                    ing_rows.append(dict(
                        product_id=pid, molecule_id=mid,
                        strength_value=ing.strength_value,
                        strength_unit=(ing.strength_unit or None),
                        strength_base_value=ing.base_value,
                        strength_base_unit=ing.base_unit,
                        position=position,
                    ))
            if ing_rows:
                db.session.bulk_insert_mappings(ProductIngredient, ing_rows)
            db.session.commit()
            product_rows.clear()
            ingredient_specs.clear()

        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                stats["read"] += 1
                if limit and stats["imported"] >= limit:
                    break

                sku = f"IMD-{(row.get('id') or '').strip()}"
                if only_skus is not None and sku not in only_skus:
                    continue
                if not sku.strip("IMD-") or sku in seen_skus:
                    stats["skipped_duplicate_sku"] += 1
                    continue

                name = (row.get("name") or "").strip()
                if not name:
                    stats["skipped_no_name"] += 1
                    continue

                parts = [(row.get("short_composition1") or "").strip(),
                         (row.get("short_composition2") or "").strip()]
                composition = " + ".join(p for p in parts if p)
                if not composition:
                    stats["skipped_no_composition"] += 1
                    continue

                pack = (row.get("pack_size_label") or "").strip() or "1 unit"
                form = detect_form(name, pack)
                parsed = parse_composition(composition, form, pack)

                molecules = [i.molecule for i in parsed.ingredients]
                schedule, basis = classify_schedule(molecules)
                schedules[schedule] += 1
                if basis.startswith("DEFAULTED"):
                    stats["schedule_defaulted"] += 1
                    if report is not None and len(defaulted) < 250000:
                        defaulted.append((sku, name, composition, schedule, basis))

                if any(m.startswith("unparsed::") for m in molecules):
                    stats["composition_unparsed"] += 1

                try:
                    price = float((row.get("price(₹)") or "0").strip() or 0)
                except ValueError:
                    price = 0.0
                paise = int(round(price * 100))

                discontinued = (row.get("Is_discontinued") or "").strip().upper() == "TRUE"

                mfr_name = (row.get("manufacturer_name") or "").strip() or "Unknown"
                mfr_name = re.sub(r"\s+", " ", mfr_name)[:200]
                mfr_id = mfr_ids.get(mfr_name)
                if mfr_id is None:
                    manufacturer = Manufacturer(name=mfr_name,
                                                slug=unique_mfr_slug(mfr_name))
                    db.session.add(manufacturer)
                    db.session.flush()
                    mfr_ids[mfr_name] = mfr_id = manufacturer.id
                    stats["manufacturers_created"] += 1

                product_rows.append(dict(
                    sku=sku,
                    brand_name=name[:240],
                    composition_text=composition[:500],
                    composition_key=parsed.composition_key[:500],
                    molecule_key=parsed.molecule_key[:400],
                    dosage_form=parsed.dosage_form[:60],
                    form_family=parsed.form_family[:40],
                    pack_label=pack[:120],
                    pack_units=pack_units(pack),
                    manufacturer_id=mfr_id,
                    # The dataset price is the printed MRP. No discount is
                    # invented — selling price equals MRP until a real
                    # commercial rule sets one.
                    mrp_paise=paise,
                    selling_price_paise=paise,
                    drug_schedule=schedule,
                    is_prescription_required=schedule in {"H", "H1", "X", "NARCOTIC"},
                    is_nlem=False,
                    category=category_for(molecules, schedule),
                    is_discontinued=discontinued,
                    is_active=not discontinued,
                    rating=None,
                    uses=None, mechanism=None, side_effects=None,
                    directions=None, storage=None,
                    content_source=f"indian_medicine_data.csv | schedule:{basis} "
                                   f"| NOT CLINICALLY REVIEWED",
                ))
                ingredient_specs.append((sku, list(parsed.ingredients)))
                seen_skus.add(sku)
                stats["imported"] += 1

                if len(product_rows) >= batch:
                    flush()
                    if stats["imported"] % (batch * 10) == 0:
                        rate = stats["imported"] / max(time.time() - started, 0.001)
                        print(f"  {stats['imported']:>7,} imported "
                              f"({rate:,.0f}/s)", flush=True)

        flush()

        stats["products_total"] = db.session.query(Product).count()
        stats["molecules_total"] = db.session.query(Molecule).count()
        stats["manufacturers_total"] = db.session.query(Manufacturer).count()

        # Portable across SQLite and PostgreSQL: bound boolean, aliased subquery.
        groups = db.session.execute(db.text(
            "SELECT COUNT(*) FROM (SELECT composition_key FROM products "
            "WHERE is_active = :active AND composition_key NOT LIKE 'unparsed::%' "
            "GROUP BY composition_key HAVING COUNT(*) > 1) AS grouped"
        ), {"active": True}).scalar_one()
        stats["substitution_groups"] = groups

    if report is not None and defaulted:
        with report.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["sku", "name", "composition",
                             "assigned_schedule", "basis"])
            writer.writerows(defaulted)
        print(f"wrote {len(defaulted):,} rows needing pharmacist review -> {report}")

    stats["seconds"] = round(time.time() - started, 1)
    return {"stats": dict(stats), "schedules": dict(schedules)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--limit", type=int, default=None,
                    help="import at most N products")
    ap.add_argument("--reset-catalog", action="store_true",
                    help="delete existing products before importing")
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--report", type=Path, default=None,
                    help="write the schedule-defaulted rows to this CSV")
    args = ap.parse_args()

    result = import_csv(args.csv, args.limit, args.reset_catalog,
                        args.batch, args.report)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
