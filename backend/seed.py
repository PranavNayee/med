"""Seed the database.

Source of the catalogue rows is the original Nirog prototype's mock data,
re-expressed as normalised records. Prices here are demo values, NOT a
licensed product master — replace with a real feed before any live use
(see docs/DATA_SOURCES.md).

Run:  python seed.py            # create tables + seed
      python seed.py --reset    # drop everything first
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    Address,
    Consent,
    Doctor,
    DoctorSlot,
    EmergencyContact,
    FamilyMember,
    HealthRecord,
    LabTest,
    MedicationReminder,
    Manufacturer,
    Molecule,
    MoleculeAlias,
    Pharmacy,
    Product,
    ProductIngredient,
    Specialty,
    User,
    VitalReading,
)
from app.security import hash_password
from app.services.composition import parse_composition
from app.services.substitution import build_keys

# --------------------------------------------------------------------------
# Molecule dictionary. This is the curated layer — the piece no dataset gives
# you for free, and the reason substitution works at all.
# --------------------------------------------------------------------------
MOLECULES: list[tuple[str, str, list[str]]] = [
    # (canonical, ATC, aliases)
    ("paracetamol", "N02BE01", ["acetaminophen", "paracetamol ip", "pcm"]),
    ("azithromycin", "J01FA10", ["azithromycin dihydrate", "azithromycin ip"]),
    ("pantoprazole", "A02BC02", ["pantoprazole sodium", "pantoprazole sesquihydrate"]),
    ("domperidone", "A03FA03", ["domperidone maleate"]),
    ("metformin", "A10BA02", ["metformin hydrochloride", "metformin hcl"]),
    ("telmisartan", "C09CA07", ["telmisartan ip"]),
    ("fexofenadine", "R06AX26", ["fexofenadine hydrochloride", "fexofenadine hcl"]),
    ("calcium", "A12AA", ["calcium carbonate", "elemental calcium"]),
    ("vitamin d3", "A11CC05", ["cholecalciferol", "vit d3"]),
    ("ambroxol", "R05CB06", ["ambroxol hydrochloride"]),
    ("levosalbutamol", "R03AC02", ["levosalbutamol sulphate", "levalbuterol"]),
    ("guaifenesin", "R05CA03", ["guaiphenesin"]),
    ("amoxicillin", "J01CA04", ["amoxycillin", "amoxicillin trihydrate",
                                "amoxycillin trihydrate"]),
    ("clavulanic acid", "J01CR02", ["clavulanate", "potassium clavulanate",
                                    "clavulanate potassium"]),
    ("cetirizine", "R06AE07", ["cetirizine hydrochloride", "cetrizine"]),
    ("omeprazole", "A02BC01", ["omeprazole magnesium"]),
]

MANUFACTURERS = [
    "GSK", "Alembic", "Alkem", "USV", "Glenmark", "Sanofi", "Torrent",
    "Micro Labs", "Cipla", "Sun Pharma", "IPCA", "Aristo", "Mankind",
    "Dr Reddy's", "Abbott", "Meyer", "Franco-Indian", "HealthKart",
]

# sku, brand, composition, form, pack, units, mfr, mrp₹, price₹, schedule, category,
# rating, nlem
PRODUCTS = [
    ("NRG-CRO-500-15", "Crocin Advance 500mg", "Paracetamol (500mg)", "Tablet",
     "Strip of 15 tablets", 15, "GSK", 33, 28, "OTC", "Pain & Fever", 4.6, True),
    ("NRG-DOL-650-15", "Dolo 650", "Paracetamol (650mg)", "Tablet",
     "Strip of 15 tablets", 15, "Micro Labs", 34, 31, "OTC", "Pain & Fever", 4.8, True),
    ("NRG-CAL-500-15", "Calpol 500", "Paracetamol 500 mg", "Tablet",
     "Strip of 15 tablets", 15, "GSK", 31, 26, "OTC", "Pain & Fever", 4.5, True),
    ("NRG-PCP-500-15", "Paracip 500", "Paracetamol IP 500mg", "Tablet",
     "Strip of 15 tablets", 15, "Cipla", 22, 14, "OTC", "Pain & Fever", 4.3, True),

    ("NRG-AZI-500-5", "Azithral 500mg", "Azithromycin (500mg)", "Tablet",
     "Strip of 5 tablets", 5, "Alembic", 142, 118, "H1", "Antibiotic", 4.4, True),
    ("NRG-AZE-500-5", "Azee 500", "Azithromycin Dihydrate 500mg", "Tablet",
     "Strip of 5 tablets", 5, "Cipla", 151, 131, "H1", "Antibiotic", 4.5, True),
    ("NRG-ZIT-500-5", "Zithrox 500", "Azithromycin 500 mg", "Tablet",
     "Strip of 5 tablets", 5, "Sun Pharma", 135, 109, "H1", "Antibiotic", 4.4, True),
    ("NRG-AZG-500-5", "Azithromycin 500 (Generic)", "Azithromycin IP 500mg", "Tablet",
     "Strip of 5 tablets", 5, "IPCA", 98, 58, "H1", "Antibiotic", 4.2, True),

    ("NRG-PAND-15", "Pan-D Capsule", "Pantoprazole (40mg) + Domperidone (30mg)",
     "Capsule", "Strip of 15 capsules", 15, "Alkem", 166, 142, "H", "Acidity & Gas",
     4.5, False),
    ("NRG-PTD-15", "Pantop-D", "Domperidone 30mg + Pantoprazole 40mg", "Capsule",
     "Strip of 15 capsules", 15, "Aristo", 152, 128, "H", "Acidity & Gas", 4.4, False),
    ("NRG-PDG-15", "Pantoprazole+Dom (Generic)",
     "Pantoprazole Sodium 40 mg + Domperidone Maleate 30 mg", "Capsule",
     "Strip of 15 capsules", 15, "Torrent", 120, 74, "H", "Acidity & Gas", 4.1, False),

    ("NRG-MET-500-20", "Metformin 500mg", "Metformin Hydrochloride (500mg)", "Tablet",
     "Strip of 20 tablets", 20, "USV", 31, 24, "H", "Diabetes", 4.7, True),
    ("NRG-GLY-500-20", "Glycomet 500", "Metformin HCl 500 mg", "Tablet",
     "Strip of 20 tablets", 20, "USV", 34, 27, "H", "Diabetes", 4.6, True),
    ("NRG-GLU-500-20", "Gluformin 500", "Metformin Hydrochloride IP 0.5g", "Tablet",
     "Strip of 20 tablets", 20, "Abbott", 36, 29, "H", "Diabetes", 4.5, True),
    ("NRG-MTG-500-20", "Metformin 500 (Generic)", "Metformin Hydrochloride 500mg",
     "Tablet", "Strip of 20 tablets", 20, "Sun Pharma", 24, 12, "H", "Diabetes",
     4.4, True),

    ("NRG-TEL-40-15", "Telma 40mg", "Telmisartan (40mg)", "Tablet",
     "Strip of 15 tablets", 15, "Glenmark", 174, 148, "H", "Blood Pressure", 4.6, True),
    ("NRG-TLK-40-15", "Telmikind 40", "Telmisartan IP 40 mg", "Tablet",
     "Strip of 15 tablets", 15, "Mankind", 148, 121, "H", "Blood Pressure", 4.5, True),
    ("NRG-TLG-40-15", "Telmisartan 40 (Generic)", "Telmisartan 40mg", "Tablet",
     "Strip of 15 tablets", 15, "Cipla", 120, 69, "H", "Blood Pressure", 4.3, True),

    ("NRG-ALL-120-10", "Allegra 120mg", "Fexofenadine (120mg)", "Tablet",
     "Strip of 10 tablets", 10, "Sanofi", 225, 198, "OTC", "Allergy", 4.5, False),
    ("NRG-FEX-120-10", "Fexova 120", "Fexofenadine Hydrochloride 120 mg", "Tablet",
     "Strip of 10 tablets", 10, "Torrent", 205, 172, "OTC", "Allergy", 4.4, False),
    ("NRG-HIS-120-10", "Histafree 120", "Fexofenadine HCl 120mg", "Tablet",
     "Strip of 10 tablets", 10, "Mankind", 190, 158, "OTC", "Allergy", 4.3, False),

    ("NRG-SHE-500-15", "Shelcal 500", "Calcium Carbonate 500mg + Vitamin D3 250 IU",
     "Tablet", "Strip of 15 tablets", 15, "Torrent", 128, 108, "OTC", "Vitamins",
     4.7, False),
    ("NRG-CAX-500-15", "Calcimax", "Elemental Calcium 500 mg + Cholecalciferol 250 IU",
     "Tablet", "Strip of 15 tablets", 15, "Meyer", 118, 98, "OTC", "Vitamins",
     4.4, False),

    ("NRG-ASC-LS-100", "Ascoril LS Syrup",
     "Ambroxol 30mg + Levosalbutamol 1mg + Guaifenesin 50mg", "Syrup",
     "100 ml bottle", 100, "Glenmark", 155, 132, "H", "Cough & Cold", 4.3, False),
    ("NRG-GRL-L-100", "Grilinctus-L",
     "Ambroxol Hydrochloride 30 mg + Levosalbutamol Sulphate 1 mg + Guaiphenesin 50 mg",
     "Syrup", "100 ml bottle", 100, "Franco-Indian", 140, 118, "H", "Cough & Cold",
     4.2, False),

    # Deliberate spelling-variant pair — proves the alias table is doing work.
    ("NRG-AUG-625-10", "Augmentin 625 Duo",
     "Amoxycillin (500mg) + Clavulanic Acid (125mg)", "Tablet",
     "Strip of 10 tablets", 10, "GSK", 210, 189, "H1", "Antibiotic", 4.6, True),
    ("NRG-CLV-625-10", "Clavam 625",
     "Amoxicillin Trihydrate 500 mg + Clavulanate Potassium 125 mg", "Tablet",
     "Strip of 10 tablets", 10, "Alkem", 198, 172, "H1", "Antibiotic", 4.5, True),
]

CONTENT = {
    "paracetamol": {
        "uses": ["Fever", "Headache", "Body ache", "Mild to moderate pain"],
        "how": "Paracetamol blocks chemical messengers (prostaglandins) in the brain "
               "that signal pain and raise body temperature.",
        "side": ["Nausea (rare)", "Allergic skin rash (rare)", "Liver strain if overdosed"],
        "directions": "As directed by your doctor. Do not exceed 4g in 24 hours.",
        "storage": "Store below 30°C, away from direct sunlight.",
    },
    "azithromycin": {
        "uses": ["Bacterial throat infection", "Respiratory infections",
                 "Skin infections", "Typhoid"],
        "how": "Stops bacteria producing essential proteins, halting their growth.",
        "side": ["Nausea", "Stomach upset", "Diarrhoea", "Headache"],
        "directions": "Take exactly as prescribed and complete the full course.",
        "storage": "Store below 25°C in a dry place.",
    },
    "metformin": {
        "uses": ["Type 2 diabetes", "Blood sugar control"],
        "how": "Lowers glucose production by the liver and improves how the body "
               "responds to insulin.",
        "side": ["Nausea", "Metallic taste", "Loose stools (early)", "Reduced appetite"],
        "directions": "Take with or just after meals, as advised by your doctor.",
        "storage": "Store below 30°C.",
    },
}


def _content_for(composition_text: str) -> dict:
    lowered = composition_text.lower()
    for key, value in CONTENT.items():
        if key in lowered:
            return value
    return {}


def seed_catalog() -> None:
    mols: dict[str, Molecule] = {}
    for canonical, atc, aliases in MOLECULES:
        molecule = Molecule(canonical_name=canonical,
                            slug=canonical.replace(" ", "-"), atc_code=atc)
        db.session.add(molecule)
        db.session.flush()
        mols[canonical] = molecule
        for alias in {canonical, *aliases}:
            db.session.add(MoleculeAlias(molecule_id=molecule.id, alias=alias,
                                         normalised=alias.lower().strip()))
    db.session.flush()

    mfrs = {}
    for name in MANUFACTURERS:
        m = Manufacturer(name=name, slug=name.lower().replace(" ", "-").replace("'", ""))
        db.session.add(m)
        db.session.flush()
        mfrs[name] = m

    pharmacy = Pharmacy(
        name="Nirog Pharmacy — Ahmedabad",
        drug_licence_number="GJ-AHM-20B-DEMO-0001",
        licence_valid_till=date.today() + timedelta(days=365),
        registered_pharmacist_name="DEMO — replace before go-live",
        pharmacist_registration_number="GSPC-DEMO-00000",
        state="Gujarat", pincode="380015",
    )
    db.session.add(pharmacy)

    for (sku, brand, composition, form, pack, units, mfr, mrp, price,
         schedule, category, rating, nlem) in PRODUCTS:
        comp_key, mol_key, unparsed = build_keys(composition, form, pack)
        parsed = parse_composition(composition, form, pack)
        content = _content_for(composition)

        product = Product(
            sku=sku, brand_name=brand, composition_text=composition,
            composition_key=comp_key, molecule_key=mol_key,
            dosage_form=parsed.dosage_form, form_family=parsed.form_family,
            pack_label=pack, pack_units=units,
            manufacturer_id=mfrs[mfr].id,
            mrp_paise=mrp * 100, selling_price_paise=price * 100,
            drug_schedule=schedule,
            is_prescription_required=schedule in {"H", "H1", "X"},
            is_nlem=nlem, category=category, rating=rating,
            uses=json.dumps(content.get("uses", [])),
            mechanism=content.get("how"),
            side_effects=json.dumps(content.get("side", [])),
            directions=content.get("directions"),
            storage=content.get("storage"),
            content_source="DEMO SEED — not clinically reviewed",
        )
        db.session.add(product)
        db.session.flush()

        for position, ingredient in enumerate(parsed.ingredients):
            from app.services.substitution import resolve_alias
            canonical = resolve_alias(ingredient.molecule)
            molecule = mols.get(canonical)
            if molecule is None:
                molecule = Molecule(canonical_name=canonical,
                                    slug=canonical.replace(" ", "-"))
                db.session.add(molecule)
                db.session.flush()
                mols[canonical] = molecule
            db.session.add(ProductIngredient(
                product_id=product.id, molecule_id=molecule.id,
                strength_value=ingredient.strength_value,
                strength_unit=ingredient.strength_unit,
                strength_base_value=ingredient.base_value,
                strength_base_unit=ingredient.base_unit,
                position=position,
            ))
    db.session.flush()


def seed_clinical() -> None:
    specs = [
        ("gp", "General Physician", "stethoscope", "teal"),
        ("derma", "Dermatologist", "sparkle", "coral"),
        ("cardio", "Cardiologist", "heartpulse", "rose"),
        ("peds", "Pediatrician", "baby", "amber"),
        ("gyno", "Gynecologist", "user", "violet"),
        ("ortho", "Orthopedic", "bone", "blue"),
        ("psych", "Psychologist", "brain", "green"),
        ("nutri", "Nutritionist", "flask", "teal"),
    ]
    spec_rows = {}
    for code, name, icon, accent in specs:
        s = Specialty(code=code, name=name, icon=icon, accent=accent)
        db.session.add(s)
        db.session.flush()
        spec_rows[code] = s

    doctors = [
        ("Dr. Ananya Sharma", "gp", "MBBS, MD (Medicine)", 12, 4.9, 2840, 399,
         "Apollo Clinic, Ahmedabad", ["English", "Hindi", "Gujarati"],
         "Internal medicine — fever, infections, lifestyle disorders, preventive care."),
        ("Dr. Rohan Mehta", "derma", "MBBS, MD (Dermatology)", 9, 4.8, 1920, 549,
         "SkinPlus, Satellite", ["English", "Hindi"],
         "Acne, pigmentation, hair fall and skin allergies."),
        ("Dr. Priya Nair", "cardio", "MBBS, DM (Cardiology)", 16, 4.9, 1410, 799,
         "CIMS Hospital", ["English", "Hindi", "Malayalam"],
         "Heart failure, hypertension and post-angioplasty care."),
        ("Dr. Karan Patel", "peds", "MBBS, DCH", 11, 4.8, 3120, 449,
         "Rainbow Children's", ["English", "Hindi", "Gujarati"],
         "Newborn to teen care — growth, vaccination, fevers and feeding."),
        ("Dr. Meera Desai", "gyno", "MBBS, MS (OBG)", 14, 4.9, 2210, 599,
         "Sunshine Women's", ["English", "Gujarati", "Hindi"],
         "Pregnancy care, PCOS, menstrual health and menopause."),
        ("Dr. Aditya Rao", "ortho", "MBBS, MS (Ortho)", 13, 4.7, 980, 649,
         "Bone & Joint Clinic", ["English", "Hindi"],
         "Joint pain, sports injuries and back problems."),
        ("Dr. Sneha Kulkarni", "psych", "MA, M.Phil Clinical Psych", 8, 4.9, 760, 899,
         "MindWell Centre", ["English", "Hindi", "Marathi"],
         "Anxiety, stress, sleep and relationship counselling."),
        ("Dr. Vikram Singh", "nutri", "PhD Nutrition, RD", 10, 4.8, 1340, 499,
         "NutriLife", ["English", "Hindi", "Punjabi"],
         "Weight, diabetes and gut-friendly Indian diet plans."),
    ]
    for idx, (name, spec, qual, exp, rating, reviews, fee, aff, langs, about) \
            in enumerate(doctors, start=1):
        doctor = Doctor(
            full_name=name, specialty_id=spec_rows[spec].id, qualification=qual,
            nmc_registration_number=f"DEMO-NMC-{idx:05d}",
            registration_council="DEMO — verify before go-live",
            registration_verified_at=datetime.utcnow(),
            experience_years=exp, rating=rating, review_count=reviews,
            consultation_fee_paise=fee * 100, affiliation=aff,
            languages=json.dumps(langs), about=about,
            is_online=idx % 5 != 0, is_active=True,
        )
        db.session.add(doctor)
        db.session.flush()
        for offset in (3, 4, 5, 27, 28):
            db.session.add(DoctorSlot(
                doctor_id=doctor.id,
                starts_at=datetime.utcnow() + timedelta(hours=offset),
            ))

    tests = [
        ("CBC", "Complete Blood Count (CBC)", 28, "Blood", False, 6, 450, 299,
         False, None, "Most booked"),
        ("FULLBODY", "Comprehensive Full Body Checkup", 92, "Blood & Urine", True,
         24, 3200, 1499, False, None, "Best value"),
        ("HBA1C", "HbA1c (Diabetes 3-month)", 1, "Blood", False, 8, 600, 399,
         False, None, "Diabetic care"),
        ("LIPID", "Lipid Profile (Cholesterol)", 9, "Blood", True, 10, 700, 449,
         False, None, None),
        ("THYROID", "Thyroid Profile (T3 T4 TSH)", 3, "Blood", False, 10, 650, 399,
         False, None, None),
        ("VITD-B12", "Vitamin D & B12 Combo", 2, "Blood", False, 24, 1900, 1199,
         False, None, None),
        ("LFT", "Liver Function Test (LFT)", 12, "Blood", True, 12, 850, 549,
         False, None, None),
        ("KFT", "Kidney Function Test (KFT)", 11, "Blood", True, 12, 900, 599,
         False, None, None),
        ("PKG-ANNUAL", "Annual Health Checkup", 64, "Blood & Urine", True, 24,
         4500, 1999, True, "Adults 25+", None),
        ("PKG-EXEC", "Executive Wellness", 88, "Blood & Urine", True, 24, 7800,
         3499, True, "Working professionals", None),
        ("PKG-SENIOR", "Senior Citizen Care", 74, "Blood & Urine", True, 24, 6200,
         2799, True, "Age 60+", None),
        ("PKG-WOMEN", "Women's Health", 71, "Blood & Urine", True, 24, 5400, 2499,
         True, "Women 30+", None),
    ]
    for (code, name, params, sample, fasting, hours, mrp, price, is_pkg,
         audience, tag) in tests:
        db.session.add(LabTest(
            code=code, name=name, parameter_count=params, sample_type=sample,
            fasting_required=fasting, turnaround_hours=hours,
            mrp_paise=mrp * 100, price_paise=price * 100,
            is_package=is_pkg, audience=audience, tag=tag,
            nabl_lab_name="Nirog Labs (demo)",
        ))


def seed_demo_user() -> None:
    user = User(mobile="9825000000", full_name="Parshwa Shah",
                password_hash=hash_password("nirog-demo-2026"), is_verified=True)
    db.session.add(user)
    db.session.flush()

    members = [
        ("Parshwa Shah", "SELF", True, False),
        ("Priya", "SPOUSE", False, False),
        ("Aarav", "SON", False, True),
        ("Maa", "MOTHER", False, False),
    ]
    member_rows = []
    for name, relation, is_self, is_minor in members:
        m = FamilyMember(user_id=user.id, full_name=name, relation=relation,
                         is_self=is_self, is_minor=is_minor)
        db.session.add(m)
        db.session.flush()
        member_rows.append(m)

    db.session.add(Address(user_id=user.id, label="Home",
                           line1="Demo address", city="Ahmedabad",
                           state="Gujarat", pincode="380015", is_default=True))
    db.session.add(Consent(user_id=user.id, purpose="ORDER_FULFILMENT",
                           granted=True, policy_version="v1.0"))
    db.session.add(Consent(user_id=user.id, purpose="LAB_REPORT_STORAGE",
                           granted=True, policy_version="v1.0"))

    self_member = member_rows[0]
    records = [
        ("CONSULT", "Consultation — Viral Fever", "Dr. Ananya Sharma", date(2026, 6, 18)),
        ("LAB", "Complete Blood Count", "Nirog Labs · Ahmedabad", date(2026, 6, 12)),
        ("RX", "Pan-D, Crocin Advance", "e-Prescription", date(2026, 6, 18)),
        ("RADIOLOGY", "Chest X-Ray (PA view)", "CIMS Imaging", date(2026, 5, 2)),
        ("VACCINE", "Influenza Vaccine", "Apollo Clinic", date(2026, 4, 15)),
        ("LAB", "HbA1c — 6.1%", "Nirog Labs", date(2026, 3, 28)),
    ]
    for rtype, title, by, on in records:
        db.session.add(HealthRecord(user_id=user.id, member_id=self_member.id,
                                    record_type=rtype, title=title,
                                    issued_by=by, record_date=on, source="NIROG"))

    vitals = [
        ("HBA1C", "%", [(date(2025, 9, 1), 7.2), (date(2025, 12, 1), 6.8),
                        (date(2026, 3, 1), 6.4), (date(2026, 6, 1), 6.1)]),
        ("BP_SYS", "mmHg", [(date(2026, 2, 1), 142), (date(2026, 3, 1), 138),
                            (date(2026, 4, 1), 132), (date(2026, 5, 1), 128),
                            (date(2026, 6, 1), 124)]),
        ("WEIGHT", "kg", [(date(2026, 2, 1), 81), (date(2026, 3, 1), 79),
                          (date(2026, 4, 1), 77), (date(2026, 5, 1), 75),
                          (date(2026, 6, 1), 73)]),
    ]
    for metric, unit, points in vitals:
        for measured_on, value in points:
            db.session.add(VitalReading(member_id=self_member.id, metric=metric,
                                        value=value, unit=unit,
                                        measured_on=measured_on))

    for label, dose, at in [("Metformin 500mg", "1 tablet · After breakfast", "08:00"),
                            ("Telma 40mg", "1 tablet · Morning", "09:00"),
                            ("Shelcal 500", "1 tablet · After dinner", "21:00")]:
        db.session.add(MedicationReminder(member_id=self_member.id, label=label,
                                          dose_instruction=dose, time_of_day=at))

    for name, relation, mobile, priority in [
        ("Priya", "Spouse", "9825011111", 1),
        ("Dr. Ananya Sharma", "Family physician", "9825022222", 2),
    ]:
        db.session.add(EmergencyContact(user_id=user.id, name=name, relation=relation,
                                        mobile=mobile, priority=priority))


def main() -> None:
    app = create_app()
    with app.app_context():
        if "--reset" in sys.argv:
            db.drop_all()
            print("dropped existing tables")
        db.create_all()

        if db.session.execute(db.select(Product).limit(1)).scalar_one_or_none():
            print("catalogue already seeded — pass --reset to rebuild")
            return

        seed_catalog()
        seed_clinical()
        seed_demo_user()
        db.session.commit()

        counts = {
            "products": db.session.query(Product).count(),
            "molecules": db.session.query(Molecule).count(),
            "aliases": db.session.query(MoleculeAlias).count(),
            "doctors": db.session.query(Doctor).count(),
            "lab tests": db.session.query(LabTest).count(),
        }
        print("seeded:", ", ".join(f"{v} {k}" for k, v in counts.items()))
        print("demo login: 9825000000 / nirog-demo-2026")


if __name__ == "__main__":
    main()
