"""HTTP API blueprints."""
from .admin import bp as admin_bp  # noqa: F401
from .catalog import bp as catalog_bp  # noqa: F401
from .clinical import bp as clinical_bp  # noqa: F401
from .commerce import bp as commerce_bp  # noqa: F401
from .labs import bp as labs_bp  # noqa: F401
from .vendors import bp as vendors_bp  # noqa: F401
from .prescriptions import bp as prescriptions_bp  # noqa: F401

__all__ = ["admin_bp", "catalog_bp", "clinical_bp", "commerce_bp", "labs_bp", "prescriptions_bp", "vendors_bp"]
