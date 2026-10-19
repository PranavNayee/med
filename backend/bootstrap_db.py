"""Bring a database up to a deployable state. Safe to run on every deploy.

    python bootstrap_db.py                 # tables + seed + full CSV import
    python bootstrap_db.py --skip-import   # tables + seed only (fast, demo data)

On Render the start command runs `--skip-import` first so the web server can
bind its port within Render's timeout, then runs the full import in the
background while the API is already serving.

Each step checks before it acts, so a second run is a no-op and an import
that was interrupted part-way resumes where it stopped (the importer skips
SKUs that already exist).

  1. create any missing tables
  2. seed the curated molecules, demo doctors, labs and demo user — only if
     the catalogue is empty
  3. import the medicines CSV — only if fewer IMD- products exist than rows
     in the file
  4. on PostgreSQL, add trigram indexes so `ILIKE '%dolo%'` search over
     254,000 products uses an index instead of a sequential scan
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app import create_app                  # noqa: E402
from app.extensions import db               # noqa: E402
from app.models import Product              # noqa: E402

DEFAULT_CSV = Path(__file__).parent / "data" / "A_Z_medicines_dataset_of_India.csv"

TRGM_INDEXES = {
    "ix_products_brand_trgm": "brand_name",
    "ix_products_composition_trgm": "composition_text",
    "ix_products_molecule_trgm": "molecule_key",
}


def csv_row_count(path: Path) -> int:
    csv.field_size_limit(10 ** 7)
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def imported_count() -> int:
    return db.session.query(Product).filter(Product.sku.like("IMD-%")).count()


def ensure_search_indexes() -> None:
    if db.engine.dialect.name != "postgresql":
        return
    try:
        db.session.execute(db.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        for name, column in TRGM_INDEXES.items():
            db.session.execute(db.text(
                f"CREATE INDEX IF NOT EXISTS {name} "
                f"ON products USING gin ({column} gin_trgm_ops)"
            ))
        db.session.commit()
        print("search indexes: ok")
    except Exception as exc:  # noqa: BLE001 - search still works, just slower
        db.session.rollback()
        print(f"search indexes skipped ({exc.__class__.__name__}): {exc}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--skip-import", action="store_true",
                    help="seed demo data only; do not import the CSV")
    args = ap.parse_args()

    # seed.main() creates tables and is itself a no-op on a seeded database.
    # Columns added since the database was created, before anything queries them.
    from app import schema_upgrades
    app = create_app()
    with app.app_context():
        db.create_all()
        added = schema_upgrades.apply(db)
        print(f"schema upgrades: {', '.join(added) or 'none needed'}")

    import seed
    sys.argv = [sys.argv[0]]
    seed.main()

    if args.skip_import:
        print("CSV import skipped (--skip-import)")
    elif not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")
    else:
        app = create_app()
        with app.app_context():
            have = imported_count()
        want = csv_row_count(args.csv)
        if have >= want:
            print(f"catalogue already imported ({have:,} of {want:,} rows)")
        else:
            print(f"importing {want - have:,} of {want:,} rows from {args.csv.name}")
            from import_medicines import import_csv
            result = import_csv(args.csv, limit=None, reset=False,
                                batch=2000, report=None)
            print(result)

    app = create_app()
    with app.app_context():
        # Built after the import: GIN indexes make 254k inserts much slower.
        if not args.skip_import:
            ensure_search_indexes()
        print(f"products in catalogue: {db.session.query(Product).count():,}")


if __name__ == "__main__":
    main()
