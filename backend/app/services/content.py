"""Patient information for medicines: parsing, validation and lookup.

Content is written or reviewed by a pharmacist in a spreadsheet and loaded
with `POST /api/admin/content` (or `import_content.py` locally). Each row is
one molecule. Only rows with status "published" and a named reviewer are
shown to patients; everything else stays a draft.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date

from sqlalchemy import select

from ..extensions import db
from ..models import Molecule, MoleculeContent
from .substitution import resolve_alias

SAFETY_TOPICS = ("alcohol", "pregnancy", "breastfeeding", "driving", "kidney", "liver")
SAFETY_LEVELS = ("safe", "caution", "unsafe", "consult")

COLUMNS = ("molecule", "uses", "how_it_works", "side_effects", *SAFETY_TOPICS,
           "directions", "storage", "source", "reviewed_by", "reviewed_at", "status")


def _split(value: str) -> list[str]:
    return [p.strip() for p in (value or "").split("|") if p.strip()]


def _safety(row: dict) -> tuple[dict, list[str]]:
    """Cells look like "caution: Avoid alcohol while taking it"."""
    out, problems = {}, []
    for topic in SAFETY_TOPICS:
        cell = (row.get(topic) or "").strip()
        if not cell:
            continue
        level, _, note = cell.partition(":")
        level = level.strip().lower()
        if level not in SAFETY_LEVELS:
            problems.append(f"{topic}: level must be one of {', '.join(SAFETY_LEVELS)}")
            continue
        out[topic] = {"level": level, "note": note.strip()}
    return out, problems


def import_rows(text: str) -> dict:
    """Upsert molecule content from CSV text. Returns a per-row report."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in ("molecule", "source") if c not in (reader.fieldnames or [])]
    if missing:
        return {"ok": False, "error": f"missing columns: {', '.join(missing)}",
                "expected": list(COLUMNS)}

    report = {"ok": True, "created": 0, "updated": 0, "skipped": []}
    for n, row in enumerate(reader, start=2):   # row 1 is the header
        name = (row.get("molecule") or "").strip()
        if not name:
            continue
        canonical = resolve_alias(name)
        molecule = db.session.execute(
            select(Molecule).where(Molecule.canonical_name == canonical)
        ).scalar_one_or_none()
        if molecule is None:
            report["skipped"].append({"row": n, "molecule": name,
                                      "reason": "not in the catalogue"})
            continue

        safety, problems = _safety(row)
        status = (row.get("status") or "draft").strip().lower()
        reviewed_by = (row.get("reviewed_by") or "").strip()
        source = (row.get("source") or "").strip()
        if not source:
            problems.append("source is required")
        if status not in ("draft", "published"):
            problems.append('status must be "draft" or "published"')
        if status == "published" and not reviewed_by:
            problems.append("published content needs reviewed_by")
        reviewed_at = None
        if (row.get("reviewed_at") or "").strip():
            try:
                reviewed_at = date.fromisoformat(row["reviewed_at"].strip())
            except ValueError:
                problems.append("reviewed_at must be YYYY-MM-DD")
        if problems:
            report["skipped"].append({"row": n, "molecule": name, "reason": "; ".join(problems)})
            continue

        content = db.session.execute(
            select(MoleculeContent).where(MoleculeContent.molecule_id == molecule.id)
        ).scalar_one_or_none()
        if content is None:
            content = MoleculeContent(molecule_id=molecule.id, source=source)
            db.session.add(content)
            report["created"] += 1
        else:
            report["updated"] += 1
        content.uses = json.dumps(_split(row.get("uses")))
        content.how_it_works = (row.get("how_it_works") or "").strip() or None
        content.side_effects = json.dumps(_split(row.get("side_effects")))
        content.safety = json.dumps(safety)
        content.directions = (row.get("directions") or "").strip() or None
        content.storage = (row.get("storage") or "").strip() or None
        content.source = source[:255]
        content.reviewed_by = reviewed_by[:160] or None
        content.reviewed_at = reviewed_at
        content.status = status

    db.session.commit()
    return report


def info_for(product) -> list[dict]:
    """Published information for each active ingredient of a product."""
    out = []
    for ingredient in sorted(product.ingredients, key=lambda i: i.position):
        if ingredient.molecule is None:
            continue
        content = db.session.execute(
            select(MoleculeContent).where(
                MoleculeContent.molecule_id == ingredient.molecule_id,
                MoleculeContent.status == "published")
        ).scalar_one_or_none()
        if content is None:
            continue
        out.append({
            "molecule": ingredient.molecule.canonical_name,
            "uses": json.loads(content.uses or "[]"),
            "how": content.how_it_works,
            "side": json.loads(content.side_effects or "[]"),
            "safety": json.loads(content.safety or "{}"),
            "directions": content.directions,
            "storage": content.storage,
            "source": content.source,
            "reviewed_by": content.reviewed_by,
            "reviewed_at": content.reviewed_at.isoformat() if content.reviewed_at else None,
        })
    return out
