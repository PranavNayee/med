"""Application factory."""
from __future__ import annotations

import logging
import uuid

from flask import Flask, g, jsonify, request
from flask_cors import CORS

from .config import Config, ConfigError
from .extensions import db


def create_app(config: Config | None = None) -> Flask:
    app = Flask(__name__)

    try:
        cfg = config or Config.load()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    app.config["NIROG"] = cfg
    app.config["SECRET_KEY"] = cfg.secret_key
    app.config["SQLALCHEMY_DATABASE_URI"] = cfg.database_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # prescription uploads

    db.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": _cors_origins(cfg)}})

    from . import models  # noqa: F401  (registers tables)
    from .api import admin_bp, catalog_bp, clinical_bp, commerce_bp, prescriptions_bp, vendors_bp

    app.register_blueprint(catalog_bp)
    app.register_blueprint(commerce_bp)
    app.register_blueprint(clinical_bp)
    app.register_blueprint(prescriptions_bp)
    app.register_blueprint(vendors_bp)
    app.register_blueprint(admin_bp)

    _configure_logging(app)
    _register_hooks(app)
    _register_error_handlers(app)

    @app.get("/api/health")
    def health():
        try:
            db.session.execute(db.text("SELECT 1"))
            database_ok = True
        except Exception:  # noqa: BLE001 - health check must never raise
            database_ok = False
        status = 200 if database_ok else 503
        return jsonify({
            "status": "ok" if database_ok else "degraded",
            "database": database_ok,
            "env": cfg.env,
            "features": {
                "rx_dispatch": cfg.enable_rx_dispatch,
                "teleconsult": cfg.enable_teleconsult,
                "ai_triage": cfg.enable_ai_triage,
                "live_payments": cfg.enable_live_payments,
            },
        }), status

    return app


def _cors_origins(cfg: Config) -> list[str]:
    import os
    raw = os.environ.get("CORS_ORIGINS", "")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return ["*"] if cfg.env == "development" else []


def _configure_logging(app: Flask) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
        '"msg":"%(message)s"}'
    ))
    app.logger.handlers = [handler]
    app.logger.setLevel(logging.INFO)


def _register_hooks(app: Flask) -> None:
    @app.before_request
    def assign_correlation_id():
        g.correlation_id = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex

    @app.after_request
    def apply_response_headers(response):
        response.headers["X-Correlation-Id"] = getattr(g, "correlation_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Health data should not sit in shared caches.
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"error": "not_found"}), 404

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"error": "file_too_large", "max_mb": 8}), 413

    @app.errorhandler(500)
    def server_error(exc):
        # Log the detail; never leak internals to the client.
        app.logger.exception("unhandled error: %s", exc)
        db.session.rollback()
        return jsonify({"error": "internal_error",
                        "correlation_id": getattr(g, "correlation_id", "")}), 500
