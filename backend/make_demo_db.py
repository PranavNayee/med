"""Carve a demo-sized catalogue out of the full imported database.

    python make_demo_db.py --source instance/nirog.db --out instance/demo.db --target 2200

The standalone build embeds the whole catalogue as JSON in one HTML file, so
254,000 products is not an option — the file would be several hundred
megabytes and the browser would stall parsing it. This picks a subset that
still demonstrates the thing the catalogue exists to do.

Selection favours substitution density over raw count: composition groups
holding several brands at different prices, capped per group so one molecule
cannot swamp the demo, spread across categories. A group of one brand teaches
the user nothing, so singletons are only used to top up at the end.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path


def build(source: Path, out: Path, target: int, per_group: int) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, out)

    con = sqlite3.connect(out)
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT p.id, p.sku, p.composition_key, p.category, p.selling_price_paise,
               p.drug_schedule, p.brand_name
        FROM products p
        WHERE p.is_active = 1
          AND p.composition_key NOT LIKE 'unparsed::%'
          AND p.selling_price_paise > 0
        ORDER BY p.composition_key, p.selling_price_paise
    """).fetchall()

    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        groups[row["composition_key"]].append(row)

    # A group is interesting when it holds several brands AND a real price
    # spread — that is what makes the "switch and save" panel say anything.
    ranked = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        low = members[0]["selling_price_paise"]
        high = members[-1]["selling_price_paise"]
        spread = (high - low) / high if high else 0
        ranked.append((min(len(members), 40) * (0.35 + spread), key, members))
    ranked.sort(reverse=True, key=lambda t: t[0])

    keep: set[int] = set()
    per_category: dict[str, int] = defaultdict(int)
    category_cap = max(target // 4, 200)

    for _, _key, members in ranked:
        if len(keep) >= target:
            break
        category = members[0]["category"] or "General"
        if per_category[category] >= category_cap:
            continue
        # Cheapest, dearest, and a spread in between — not just the first N.
        picks = members[:per_group]
        if len(members) > per_group:
            picks = ([members[0]] +
                     members[len(members) // 3::max(len(members) // per_group, 1)][:per_group - 2] +
                     [members[-1]])
        for row in picks:
            if len(keep) >= target:
                break
            keep.add(row["id"])
            per_category[category] += 1

    if len(keep) < target:
        for row in rows:
            if len(keep) >= target:
                break
            keep.add(row["id"])

    # Keep the original seeded demo products — they carry the reviewed
    # editorial content the imported rows do not have.
    for (pid,) in con.execute(
            "SELECT id FROM products WHERE sku NOT LIKE 'IMD-%'").fetchall():
        keep.add(pid)

    placeholders = ",".join("?" * len(keep))
    ids = list(keep)
    con.execute(f"DELETE FROM product_ingredients WHERE product_id NOT IN ({placeholders})", ids)
    con.execute(f"DELETE FROM products WHERE id NOT IN ({placeholders})", ids)
    con.execute("DELETE FROM molecules WHERE id NOT IN "
                "(SELECT DISTINCT molecule_id FROM product_ingredients)")
    con.execute("DELETE FROM molecule_aliases WHERE molecule_id NOT IN "
                "(SELECT id FROM molecules)")
    con.execute("DELETE FROM manufacturers WHERE id NOT IN "
                "(SELECT DISTINCT manufacturer_id FROM products WHERE manufacturer_id IS NOT NULL)")
    con.commit()
    con.execute("VACUUM")

    stats = {
        "products": con.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "manufacturers": con.execute("SELECT COUNT(*) FROM manufacturers").fetchone()[0],
        "molecules": con.execute("SELECT COUNT(*) FROM molecules").fetchone()[0],
        "substitution_groups": con.execute(
            "SELECT COUNT(*) FROM (SELECT composition_key FROM products "
            "WHERE is_active=1 GROUP BY composition_key HAVING COUNT(*) > 1)"
        ).fetchone()[0],
        "categories": con.execute(
            "SELECT COUNT(DISTINCT category) FROM products").fetchone()[0],
        "size_mb": round(out.stat().st_size / 1e6, 1),
    }
    con.close()
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=Path("instance/nirog.db"))
    ap.add_argument("--out", type=Path, default=Path("instance/demo.db"))
    ap.add_argument("--target", type=int, default=2200)
    ap.add_argument("--per-group", type=int, default=6)
    args = ap.parse_args()
    print(build(args.source, args.out, args.target, args.per_group))
