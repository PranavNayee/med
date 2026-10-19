"""Add columns introduced after a database was first created.

`db.create_all()` creates missing tables but never alters existing ones, so a
column added to a model after the live database was built has to be added
here as well. Each entry is applied only if the column is missing, so this is
safe to run on every start. The DDL is valid on PostgreSQL and SQLite.
"""
from __future__ import annotations

from sqlalchemy import inspect, text

#: (table, column, column definition)
COLUMNS: list[tuple[str, str, str]] = [
    ("addresses", "recipient_name", "VARCHAR(160)"),
    ("addresses", "phone", "VARCHAR(15)"),
    ("addresses", "is_archived", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("orders", "payment_method", "VARCHAR(20) NOT NULL DEFAULT 'COD'"),
    ("orders", "shipping_address", "TEXT"),
    ("lab_bookings", "patient_name", "VARCHAR(160)"),
    ("lab_bookings", "address_text", "TEXT"),
    ("lab_bookings", "payment_method", "VARCHAR(20) NOT NULL DEFAULT 'PAY_AT_COLLECTION'"),
]


def apply(db) -> list[str]:
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    added = []
    for table, column, ddl in COLUMNS:
        if table not in tables:
            continue  # create_all builds it with every column
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            continue
        db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        added.append(f"{table}.{column}")
    db.session.commit()
    return added
