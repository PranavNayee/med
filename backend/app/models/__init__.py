"""All ORM models. Importing this package registers every table."""
from .catalog import (  # noqa: F401
    Inventory,
    Manufacturer,
    Molecule,
    MoleculeAlias,
    MoleculeContent,
    Pharmacy,
    Product,
    ProductIngredient,
)
from .clinical import (  # noqa: F401
    Consultation,
    Doctor,
    DoctorSlot,
    EmergencyContact,
    HealthRecord,
    LabBooking,
    LabTest,
    MedicationReminder,
    ReminderLog,
    Specialty,
    VitalReading,
)
from .order import (  # noqa: F401
    ALLOWED_TRANSITIONS,
    Cart,
    CartItem,
    IllegalTransition,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    Prescription,
)
from .user import Address, AuditLog, Consent, FamilyMember, User  # noqa: F401
