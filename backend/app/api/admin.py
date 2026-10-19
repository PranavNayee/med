"""Staff-only endpoints, guarded by ADMIN_TOKEN.

    POST /api/admin/content            CSV upload (field `file`) of medicine information
    GET  /api/admin/content/template   the CSV header to fill in

This is a stopgap until staff accounts exist: one shared secret, sent as the
X-Admin-Token header, compared in constant time. Without ADMIN_TOKEN set the
endpoints refuse to run.
"""
from __future__ import annotations

import functools
import hmac

from flask import Blueprint, Response, current_app, jsonify, request

from ..security import audit
from ..services.content import COLUMNS, import_rows

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def admin_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        expected = current_app.config["NIROG"].admin_token
        if not expected:
            return jsonify({"error": "admin_not_configured",
                            "message": "Set ADMIN_TOKEN to use staff endpoints."}), 503
        given = request.headers.get("X-Admin-Token", "")
        if not hmac.compare_digest(given.encode(), expected.encode()):
            return jsonify({"error": "forbidden"}), 403
        return view(*args, **kwargs)
    return wrapper


@bp.get("/content/template")
@admin_required
def content_template():
    return Response(",".join(COLUMNS) + "\n", mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=medicine_content.csv"})


@bp.post("/content")
@admin_required
def upload_content():
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "file_required",
                        "message": "Send the CSV as multipart field `file`."}), 400
    text = upload.read().decode("utf-8-sig", errors="replace")
    report = import_rows(text)
    audit("CONTENT_IMPORTED", actor_type="STAFF", subject_type="molecule_content",
          detail=f'{{"created": {report.get("created", 0)}, "updated": {report.get("updated", 0)}}}')
    from ..extensions import db
    db.session.commit()
    return jsonify(report), (200 if report.get("ok") else 400)
