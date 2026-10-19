"""Application configuration.

Everything is environment-driven. Nothing sensitive has a default that
would work in production — missing required secrets abort startup rather
than silently falling back.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """Raised when configuration is missing or internally inconsistent."""


def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise ConfigError(
            f"Required environment variable {key} is not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


def database_uri_from_env() -> str:
    """DATABASE_URI, or DATABASE_URL as Render/Neon/Heroku inject it.

    Those hosts hand out `postgres://...`, which SQLAlchemy 2 rejects, so the
    scheme is rewritten to use the psycopg 3 driver.
    """
    uri = (os.environ.get("DATABASE_URI") or os.environ.get("DATABASE_URL") or "").strip()
    for prefix in ("postgres://", "postgresql://"):
        if uri.startswith(prefix):
            return "postgresql+psycopg://" + uri[len(prefix):]
    return uri


def _flag(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    """Validated runtime configuration."""

    env: str = field(default_factory=lambda: os.environ.get("APP_ENV", "development"))
    secret_key: str = ""
    database_uri: str = ""

    # Security
    jwt_ttl_minutes: int = 60 * 12
    bcrypt_rounds: int = 12

    # Feature gates — every risky capability is off unless explicitly enabled.
    enable_rx_dispatch: bool = False      # dispensing Schedule H/H1 drugs
    enable_live_payments: bool = False    # real money movement
    enable_ai_triage: bool = True         # symptom guidance (never diagnosis)
    enable_teleconsult: bool = False      # requires signed doctor agreements

    # Pharmacy policy
    pharmacist_verification_required: bool = True
    max_rx_quantity_per_order: int = 3

    # AI
    ai_provider: str = "none"
    ai_timeout_seconds: int = 12

    # Prescription reader (Gemini). Off unless GEMINI_API_KEY is set; typed
    # entry and matching work either way.
    gemini_api_key: str = field(default="", repr=False)
    prescription_reader_model: str = "gemini-3.8-flash"
    prescription_reads_per_day: int = 20
    prescription_reader_thinking: str = "low"

    # Vendor configurations — each integration is off-by-default and fails
    # closed with an explicit 503/501.  Keys live only in backend env vars.
    vendor_config: dict = field(default_factory=dict)

    # Razorpay. Test keys (rzp_test_) work on their own; live keys also need
    # ENABLE_LIVE_PAYMENTS=true.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = field(default="", repr=False)
    razorpay_webhook_secret: str = field(default="", repr=False)

    @property
    def online_payments_available(self) -> bool:
        if not (self.razorpay_key_id and self.razorpay_key_secret):
            return False
        return self.razorpay_key_id.startswith("rzp_test_") or self.enable_live_payments

    # Shared secret for /api/admin (content uploads) until staff accounts exist.
    admin_token: str = field(default="", repr=False)

    @classmethod
    def load(cls) -> "Config":
        env = os.environ.get("APP_ENV", "development")

        # In development we allow a generated secret so the app runs out of
        # the box; in any other environment it must be supplied.
        if env == "development":
            secret = os.environ.get("SECRET_KEY") or "dev-only-insecure-key"
            db = database_uri_from_env() or "sqlite:///nirog.db"
        else:
            secret = _require("SECRET_KEY")
            db = database_uri_from_env()
            if not db:
                raise ConfigError(
                    "Required environment variable DATABASE_URL (or DATABASE_URI) "
                    "is not set.")

        cfg = cls(
            env=env,
            secret_key=secret,
            database_uri=db,
            enable_rx_dispatch=_flag("ENABLE_RX_DISPATCH", False),
            enable_live_payments=_flag("ENABLE_LIVE_PAYMENTS", False),
            enable_ai_triage=_flag("ENABLE_AI_TRIAGE", True),
            enable_teleconsult=_flag("ENABLE_TELECONSULT", False),
            pharmacist_verification_required=_flag("PHARMACIST_VERIFICATION_REQUIRED", True),
            ai_provider=os.environ.get("AI_PROVIDER", "none"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            prescription_reader_model=(os.environ.get("PRESCRIPTION_READER_MODEL", "").strip()
                                       or "gemini-3.8-flash"),
            prescription_reads_per_day=int(os.environ.get("PRESCRIPTION_READS_PER_DAY", "20")),
            admin_token=os.environ.get("ADMIN_TOKEN", "").strip(),
            razorpay_key_id=os.environ.get("RAZORPAY_KEY_ID", "").strip(),
            razorpay_key_secret=os.environ.get("RAZORPAY_KEY_SECRET", "").strip(),
            razorpay_webhook_secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip(),
            prescription_reader_thinking=(os.environ.get("PRESCRIPTION_READER_THINKING", "").strip()
                                          or "low"),
        )
        cfg._validate()
        # Build vendor_config dict from env vars
        cfg.vendor_config = cls._build_vendor_config(cfg)
        return cfg

    def _validate(self) -> None:
        if self.enable_rx_dispatch and not self.pharmacist_verification_required:
            raise ConfigError(
                "ENABLE_RX_DISPATCH=true with PHARMACIST_VERIFICATION_REQUIRED=false "
                "is not a permitted combination. Dispensing prescription medicines "
                "without pharmacist verification is unlawful under the Drugs and "
                "Cosmetics Act."
            )
        if self.env != "development" and self.secret_key == "dev-only-insecure-key":
            raise ConfigError("The development secret key cannot be used outside development.")

    @staticmethod
    def _build_vendor_config(cfg: "Config") -> dict:
        """Build the vendor_config dict from environment variables.

        Each vendor sub-dict contains:
        - configured: bool — whether the minimal secrets are present
        - mode: "mock" | "sandbox" | "live" — current operating mode
        - enabled_features: list — which features are available
        """
        vendor_cfg = {}

        # Razorpay
        rk_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
        rk_sec = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
        wh_sec = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip()
        vendor_cfg["razorpay"] = {
            "configured": bool(rk_id and rk_sec),
            "mode": "live" if (cfg.enable_live_payments and rk_id.startswith("rzp_live_")) else (
                        "sandbox" if rk_id.startswith("rzp_test_") else "mock"),
            "enabled_features": ["create_order", "verify_payment", "webhook"]
            if rk_id and rk_sec else[],
        }

        # Shiprocket
        se = os.environ.get("SHIPROCKET_EMAIL", "").strip()
        sp = os.environ.get("SHIPROCKET_PASSWORD", "").strip()
        vendor_cfg["shiprocket"] = {
            "configured": bool(se and sp),
            "mode": "mock",
            "enabled_features": ["serviceability", "create_shipment", "track", "webhook"]
            if se and sp else[],
        }

        # DrugSetu
        ds_key = os.environ.get("DRUGSETU_API_KEY", "").strip()
        vendor_cfg["drugsetu"] = {
            "configured": bool(ds_key),
            "mode": "mock",
            "enabled_features": ["sync", "lookup"]
            if ds_key else[],
        }

        # PhonePe
        pp_merchant = os.environ.get("PHONEPE_MERCHANT_ID", "").strip()
        pp_salt = os.environ.get("PHONEPE_SALT_KEY", "").strip()
        vendor_cfg["phonepe"] = {
            "configured": bool(pp_merchant and pp_salt),
            "mode": "mock",
            "enabled_features": ["create_payment", "check_status", "verify_callback"]
            if pp_merchant and pp_salt else[],
        }

        # MSG91
        mg_key = os.environ.get("MSG91_AUTH_KEY", "").strip()
        vendor_cfg["msg91"] = {
            "configured": bool(mg_key),
            "mode": "mock",
            "enabled_features": ["send_otp", "verify_otp"]
            if mg_key else[],
        }

        # Gemini
        ga_key = os.environ.get("GEMINI_API_KEY", "").strip()
        vendor_cfg["gemini"] = {
            "configured": bool(ga_key),
            "mode": "live" if ga_key else "mock",
            "enabled_features": ["prescription_read"]
            if ga_key else[],
        }

        return vendor_cfg
