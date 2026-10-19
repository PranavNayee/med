"""Generate the demo doctor directory and an expanded lab-test catalogue.

Eight doctors across eight specialties is not a directory — you cannot filter
it, search it, or route an AI answer to it. This builds a directory large
enough for those features to mean something.

Everything here is FICTIONAL. Names are assembled from common Indian name
parts, registration numbers are stamped DEMO-, and no output corresponds to a
real practitioner. Real doctor data has to come from a verified source with
NMC registration checks — inventing it would be both useless and dangerous.
"""
from __future__ import annotations

import random

SPECIALTIES = [
    # id, display name, icon, colour, qualifications, what they handle, keywords the AI routes on
    ("gp", "General Physician", "stethoscope", "teal", ["MBBS, MD (General Medicine)", "MBBS, DNB (Medicine)", "MBBS, MD (Internal Medicine)"],
     "Fever, infections, fatigue, lifestyle disorders and first-line care.",
     ["fever", "cold", "cough", "flu", "infection", "weakness", "tired", "body ache", "headache", "viral", "general"]),
    ("derma", "Dermatologist", "sparkle", "coral", ["MBBS, MD (Dermatology)", "MBBS, DDVL", "MBBS, DNB (Dermatology)"],
     "Skin, hair and nail conditions, acne, eczema, pigmentation and hair loss.",
     ["skin", "acne", "pimple", "rash", "hair fall", "hair loss", "eczema", "psoriasis", "itching", "fungal", "dandruff", "pigmentation"]),
    ("cardio", "Cardiologist", "heartpulse", "rose", ["MBBS, MD, DM (Cardiology)", "MBBS, DNB (Cardiology)"],
     "Heart disease, blood pressure, cholesterol, palpitations and chest pain.",
     ["heart", "chest pain", "bp", "blood pressure", "hypertension", "cholesterol", "palpitation", "cardiac", "ecg"]),
    ("ortho", "Orthopaedic Surgeon", "bone", "amber", ["MBBS, MS (Orthopaedics)", "MBBS, DNB (Orthopaedics)"],
     "Bones, joints, fractures, back pain, arthritis and sports injuries.",
     ["bone", "joint", "knee", "back pain", "fracture", "arthritis", "shoulder", "spine", "sprain", "neck pain"]),
    ("gyn", "Gynaecologist", "baby", "violet", ["MBBS, MS (Obstetrics & Gynaecology)", "MBBS, DGO", "MBBS, DNB (OBG)"],
     "Women's health, pregnancy, menstrual and fertility concerns.",
     ["pregnan", "period", "menstrual", "pcod", "pcos", "fertility", "gynae", "menopause", "white discharge"]),
    ("paed", "Paediatrician", "baby", "blue", ["MBBS, MD (Paediatrics)", "MBBS, DCH", "MBBS, DNB (Paediatrics)"],
     "Children's health from newborn to adolescent, growth and vaccination.",
     ["child", "baby", "infant", "kid", "vaccination", "immunisation", "newborn", "toddler", "paediatric"]),
    ("psych", "Psychiatrist", "brain", "teal", ["MBBS, MD (Psychiatry)", "MBBS, DNB (Psychiatry)"],
     "Depression, anxiety, sleep disorders and medication for mental health.",
     ["depress", "anxiety", "panic", "sleep", "insomnia", "mental", "stress", "mood", "bipolar", "ocd"]),
    ("psycho", "Psychologist", "brain", "violet", ["MA, M.Phil (Clinical Psychology)", "M.Sc, PhD (Psychology)"],
     "Counselling and talk therapy for stress, relationships and grief.",
     ["counsel", "therapy", "talk", "grief", "relationship", "trauma"]),
    ("ent", "ENT Specialist", "activity", "green", ["MBBS, MS (ENT)", "MBBS, DLO", "MBBS, DNB (ENT)"],
     "Ear, nose and throat — sinus, hearing, tonsils and vertigo.",
     ["ear", "nose", "throat", "sinus", "tonsil", "hearing", "vertigo", "snoring", "sore throat"]),
    ("eye", "Ophthalmologist", "eye", "blue", ["MBBS, MS (Ophthalmology)", "MBBS, DNB (Ophthalmology)"],
     "Vision, cataract, glaucoma, dry eyes and retinal conditions.",
     ["eye", "vision", "cataract", "glaucoma", "blurred", "spectacle", "retina", "dry eye"]),
    ("dent", "Dentist", "tooth", "amber", ["BDS", "BDS, MDS (Prosthodontics)", "BDS, MDS (Orthodontics)"],
     "Teeth and gums, cavities, root canal, braces and oral hygiene.",
     ["tooth", "teeth", "dental", "gum", "cavity", "root canal", "braces", "toothache"]),
    ("gastro", "Gastroenterologist", "activity", "coral", ["MBBS, MD, DM (Gastroenterology)", "MBBS, DNB (Gastroenterology)"],
     "Digestive system — acidity, IBS, liver, ulcers and constipation.",
     ["stomach", "acidity", "gastric", "liver", "ibs", "constipation", "diarrhoea", "ulcer", "digest", "bloating", "piles"]),
    ("neuro", "Neurologist", "brain", "rose", ["MBBS, MD, DM (Neurology)", "MBBS, DNB (Neurology)"],
     "Brain and nerves — migraine, epilepsy, stroke, numbness and tremor.",
     ["migraine", "epilep", "seizure", "stroke", "numbness", "tremor", "paralysis", "nerve", "giddiness"]),
    ("endo", "Endocrinologist", "glucose", "green", ["MBBS, MD, DM (Endocrinology)", "MBBS, DNB (Endocrinology)"],
     "Diabetes, thyroid, hormones, obesity and metabolic disorders.",
     ["diabet", "sugar", "thyroid", "hormone", "obesity", "weight gain", "metabolic", "insulin"]),
    ("uro", "Urologist", "drop", "blue", ["MBBS, MS, MCh (Urology)", "MBBS, DNB (Urology)"],
     "Kidney stones, urinary infections, prostate and male fertility.",
     ["kidney", "stone", "urine", "urinary", "prostate", "bladder", "burning urination"]),
    ("nephro", "Nephrologist", "drop", "teal", ["MBBS, MD, DM (Nephrology)", "MBBS, DNB (Nephrology)"],
     "Kidney function, dialysis, creatinine and chronic kidney disease.",
     ["kidney failure", "dialysis", "creatinine", "nephro", "renal"]),
    ("pulmo", "Pulmonologist", "lungs", "violet", ["MBBS, MD (Pulmonary Medicine)", "MBBS, DNB (Respiratory Medicine)"],
     "Asthma, COPD, breathlessness, chronic cough and sleep apnoea.",
     ["asthma", "breath", "copd", "lung", "wheez", "chronic cough", "tb", "tuberculosis", "smoking"]),
    ("onco", "Oncologist", "shield2", "rose", ["MBBS, MD, DM (Medical Oncology)", "MBBS, MS, MCh (Surgical Oncology)"],
     "Cancer diagnosis, chemotherapy and long-term follow-up.",
     ["cancer", "tumour", "tumor", "chemo", "oncolog", "lump", "biopsy"]),
    ("rheum", "Rheumatologist", "bone", "amber", ["MBBS, MD, DM (Rheumatology)", "MBBS, DNB (Rheumatology)"],
     "Autoimmune joint disease, rheumatoid arthritis, lupus and gout.",
     ["rheumat", "autoimmune", "lupus", "gout", "joint swelling", "stiffness"]),
    ("surg", "General Surgeon", "activity", "teal", ["MBBS, MS (General Surgery)", "MBBS, DNB (Surgery)"],
     "Hernia, gallstones, appendix, piles and general surgical care.",
     ["surgery", "hernia", "gallstone", "appendix", "operation", "lump removal"]),
    ("nutri", "Dietitian & Nutritionist", "flame", "green", ["M.Sc (Clinical Nutrition), RD", "B.Sc, M.Sc (Dietetics)"],
     "Diet planning for weight, diabetes, PCOS and heart health.",
     ["diet", "nutrition", "weight loss", "weight", "food plan", "protein"]),
    ("physio", "Physiotherapist", "dumbbell", "blue", ["BPT", "BPT, MPT (Orthopaedics)", "BPT, MPT (Neurology)"],
     "Rehabilitation, posture, mobility and pain without medication.",
     ["physio", "rehab", "posture", "mobility", "stiff", "exercise therapy"]),
    ("ayur", "Ayurveda Practitioner", "sparkle", "green", ["BAMS", "BAMS, MD (Ayurveda)"],
     "Ayurvedic assessment and management of chronic complaints.",
     ["ayurved", "herbal", "panchakarma", "ayush"]),
    ("homeo", "Homoeopath", "drop", "violet", ["BHMS", "BHMS, MD (Homoeopathy)"],
     "Homoeopathic consultation for chronic and recurring complaints.",
     ["homoeo", "homeo"]),
    ("derma_cos", "Cosmetologist", "sparkle", "coral", ["MBBS, MD (Dermatology), Fellowship in Cosmetology"],
     "Cosmetic skin and hair procedures, scars and anti-ageing.",
     ["cosmetic", "botox", "laser", "scar", "anti ageing", "anti aging"]),
    ("sexo", "Sexual Health Specialist", "shield", "teal", ["MBBS, MD (Psychiatry), Fellowship in Sexual Medicine", "MBBS, DVD"],
     "Sexual health, confidential consultation for men and women.",
     ["sexual", "erectile", "libido", "std", "sti"]),
]

FIRST_M = ["Aarav", "Vivek", "Rohan", "Karan", "Nikhil", "Siddharth", "Aditya", "Manish", "Rajesh", "Amit",
           "Pranav", "Harsh", "Devang", "Kunal", "Sanjay", "Vikram", "Anand", "Ravi", "Jayesh", "Tushar",
           "Mihir", "Parth", "Chirag", "Nirav", "Bhavin", "Ashwin", "Sameer", "Gaurav", "Yash", "Rahul",
           "Arjun", "Naveen", "Sunil", "Deepak", "Prakash", "Vishal", "Hardik", "Kaushik", "Mehul", "Ronak"]
FIRST_F = ["Ananya", "Priya", "Sneha", "Neha", "Kavita", "Meera", "Riya", "Divya", "Anjali", "Pooja",
           "Shreya", "Nisha", "Rupal", "Hetal", "Krupa", "Aditi", "Swati", "Falguni", "Bhavna", "Trupti",
           "Ishita", "Radhika", "Payal", "Vaishali", "Namrata", "Sejal", "Nidhi", "Kruti", "Zeal", "Rashmi",
           "Ritu", "Sonal", "Charmi", "Dhara", "Jinal", "Purvi", "Tanvi", "Urvi", "Vidhi", "Yamini"]
LAST = ["Sharma", "Patel", "Shah", "Desai", "Mehta", "Trivedi", "Joshi", "Bhatt", "Pandya", "Gandhi",
        "Rao", "Reddy", "Nair", "Menon", "Iyer", "Kulkarni", "Deshpande", "Chauhan", "Rathod", "Solanki",
        "Vyas", "Dave", "Modi", "Kapoor", "Malhotra", "Chopra", "Bansal", "Agarwal", "Singh", "Verma",
        "Thakkar", "Amin", "Parikh", "Doshi", "Zaveri", "Contractor", "Sheth", "Raval", "Purohit", "Bhavsar"]

CITIES = [
    ("Ahmedabad", ["Apollo Hospitals International, Ahmedabad", "Sterling Hospital, Ahmedabad",
                   "Zydus Hospital, Ahmedabad", "CIMS Hospital, Ahmedabad", "SAL Hospital, Ahmedabad",
                   "Shalby Hospital, Ahmedabad", "HCG Cancer Centre, Ahmedabad", "Nirog Clinic, Satellite"], 34),
    ("Surat", ["Kiran Multi Super Speciality Hospital, Surat", "BAPS Pramukhswami Hospital, Surat",
               "Unity Hospital, Surat", "Nirog Clinic, Adajan"], 14),
    ("Vadodara", ["Sterling Hospital, Vadodara", "Bhailal Amin General Hospital, Vadodara",
                  "Nirog Clinic, Alkapuri"], 11),
    ("Rajkot", ["Sterling Hospital, Rajkot", "Wockhardt Hospital, Rajkot", "Nirog Clinic, Kalawad Road"], 9),
    ("Gandhinagar", ["Apollo CVHF Hospital, Gandhinagar", "Civil Hospital, Gandhinagar"], 6),
    ("Mumbai", ["Lilavati Hospital, Mumbai", "Kokilaben Dhirubhai Ambani Hospital, Mumbai",
                "Hinduja Hospital, Mumbai"], 10),
    ("Bengaluru", ["Manipal Hospital, Bengaluru", "Fortis Hospital, Bengaluru"], 8),
    ("Delhi NCR", ["Max Super Speciality Hospital, Saket", "Medanta - The Medicity, Gurugram"], 8),
]

LANGS = [
    (["English", "Hindi", "Gujarati"], 46),
    (["English", "Gujarati"], 16),
    (["English", "Hindi"], 16),
    (["English", "Hindi", "Gujarati", "Marathi"], 8),
    (["English", "Hindi", "Gujarati", "Sindhi"], 5),
    (["English", "Hindi", "Marathi"], 4),
    (["English", "Kannada", "Hindi"], 3),
    (["English", "Tamil", "Hindi"], 2),
]

SLOTS = ["Today, 6:30 PM", "Today, 7:15 PM", "Today, 8:00 PM", "Tomorrow, 10:00 AM",
         "Tomorrow, 11:30 AM", "Tomorrow, 4:45 PM", "Tomorrow, 6:00 PM", "In 20 minutes"]


def _weighted(table, rng):
    total = sum(w for _, w in table)
    pick = rng.uniform(0, total)
    upto = 0
    for value, weight in table:
        upto += weight
        if pick <= upto:
            return value
    return table[-1][0]


def build_doctors(per_spec: int = 12, seed: int = 20260903) -> tuple[list, list]:
    rng = random.Random(seed)
    doctors = []
    used_names = set()
    n = 0

    for sid, sname, icon, colour, quals, about, _kw in SPECIALTIES:
        for _ in range(per_spec):
            for _attempt in range(40):
                female = rng.random() < (0.75 if sid in ("gyn", "nutri") else 0.42)
                first = rng.choice(FIRST_F if female else FIRST_M)
                name = f"Dr. {first} {rng.choice(LAST)}"
                if name not in used_names:
                    break
            used_names.add(name)
            n += 1

            city, hospitals = _weighted([((c, h), w) for c, h, w in CITIES], rng)
            exp = rng.choice([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 25, 28, 31])
            # Fee tracks experience and how specialised the field is.
            base = {"gp": 350, "nutri": 500, "physio": 450, "psycho": 900, "dent": 400,
                    "ayur": 400, "homeo": 350}.get(sid, 700)
            if sid in ("cardio", "neuro", "onco", "endo", "nephro", "gastro", "uro", "rheum"):
                base = 1100
            fee = int(round((base + exp * 28 + rng.randint(-90, 160)) / 50.0)) * 50

            doctors.append({
                "id": f"d{n}",
                "name": name,
                "spec": sname,
                "specId": sid,
                "qual": rng.choice(quals),
                "exp": exp,
                "rating": round(rng.uniform(4.1, 5.0), 1),
                "reviews": rng.choice([48, 96, 143, 221, 310, 486, 702, 940, 1280, 1655, 2140, 2840, 3610]),
                "fee": float(fee),
                "online": rng.random() < 0.42,
                "langs": _weighted(LANGS, rng),
                "hospital": rng.choice(hospitals),
                "city": city,
                "about": about,
                "next_slot": rng.choice(SLOTS),
                "video": rng.random() < 0.88,
                "clinic": rng.random() < 0.72,
                "bookable": True,
                "registration": f"DEMO-NMC-{n:05d}",
                "demo": True,
            })

    counts = {}
    for d in doctors:
        counts[d["specId"]] = counts.get(d["specId"], 0) + 1
    specialties = [{"id": sid, "name": sname, "icon": icon, "color": colour,
                    "count": counts.get(sid, 0), "about": about,
                    "keywords": kw}
                   for sid, sname, icon, colour, _q, about, kw in SPECIALTIES]
    return doctors, specialties


# ---------------------------------------------------------------------------
# Lab tests
# ---------------------------------------------------------------------------

LAB_TESTS = [
    # id, name, params, price, mrp, fasting, turnaround, sample, tag, package, for, why it is ordered
    ("CBC", "Complete Blood Count (CBC)", 28, 299, 450, False, "6 hrs", "Blood", "Most booked", False, None,
     "Screens for infection, anaemia and blood disorders. Usually the first test in unexplained fever or fatigue."),
    ("FBS", "Fasting Blood Sugar", 1, 99, 180, True, "4 hrs", "Blood", None, False, None,
     "Blood glucose after 8-10 hours without food. Used to screen for and monitor diabetes."),
    ("PPBS", "Post Prandial Blood Sugar", 1, 99, 180, False, "4 hrs", "Blood", None, False, None,
     "Blood glucose two hours after a meal, read alongside the fasting value."),
    ("HBA1C", "HbA1c (Glycated Haemoglobin)", 1, 449, 700, False, "8 hrs", "Blood", "Diabetes", False, None,
     "Average blood sugar over roughly three months. The standard measure of diabetes control."),
    ("LIPID", "Lipid Profile", 8, 499, 800, True, "8 hrs", "Blood", "Heart", False, None,
     "Total, LDL and HDL cholesterol plus triglycerides. Assesses cardiovascular risk."),
    ("LFT", "Liver Function Test (LFT)", 12, 549, 900, True, "8 hrs", "Blood", None, False, None,
     "Liver enzymes, bilirubin and proteins. Ordered for jaundice, alcohol use or long-term medication."),
    ("KFT", "Kidney Function Test (KFT)", 10, 549, 900, True, "8 hrs", "Blood", None, False, None,
     "Creatinine, urea and electrolytes. Checks how well the kidneys are filtering."),
    ("TFT", "Thyroid Profile (T3 T4 TSH)", 3, 399, 650, False, "8 hrs", "Blood", "Popular", False, None,
     "Thyroid hormones. Ordered for unexplained weight change, fatigue, hair fall or irregular periods."),
    ("VITD", "Vitamin D (25-OH)", 1, 899, 1400, False, "24 hrs", "Blood", None, False, None,
     "Vitamin D level. Commonly low in India and linked to bone pain and fatigue."),
    ("VITB12", "Vitamin B12", 1, 699, 1100, False, "24 hrs", "Blood", None, False, None,
     "B12 level. Low values cause tiredness, tingling and poor concentration, often in vegetarians."),
    ("IRON", "Iron Studies", 4, 749, 1200, True, "24 hrs", "Blood", None, False, None,
     "Serum iron, ferritin and binding capacity. Distinguishes types of anaemia."),
    ("URINE", "Urine Routine & Microscopy", 18, 199, 350, False, "6 hrs", "Urine", None, False, None,
     "Screens for urinary infection, kidney disease and diabetes."),
    ("CRP", "C-Reactive Protein (CRP)", 1, 449, 700, False, "8 hrs", "Blood", None, False, None,
     "Marker of inflammation or infection anywhere in the body."),
    ("ESR", "ESR", 1, 149, 250, False, "6 hrs", "Blood", None, False, None,
     "Non-specific inflammation marker, often paired with CRP."),
    ("DENGUE", "Dengue NS1 + IgG/IgM", 3, 899, 1400, False, "8 hrs", "Blood", "Seasonal", False, None,
     "Confirms dengue in fever with body ache and low platelets."),
    ("TYPHI", "Typhoid (Widal + Typhidot)", 2, 549, 900, False, "12 hrs", "Blood", None, False, None,
     "Screens for typhoid in prolonged fever."),
    ("MALARIA", "Malaria Antigen & Smear", 2, 399, 650, False, "6 hrs", "Blood", None, False, None,
     "Detects malarial parasite in fever with chills."),
    ("THYROID_AB", "Anti-TPO Antibody", 1, 899, 1400, False, "24 hrs", "Blood", None, False, None,
     "Checks for autoimmune thyroid disease when TSH is abnormal."),
    ("PSA", "PSA (Prostate Specific Antigen)", 1, 799, 1250, False, "24 hrs", "Blood", None, False, "Men 50+",
     "Prostate screening for men, usually from age 50."),
    ("PAP", "PAP Smear", 1, 999, 1600, False, "48 hrs", "Swab", None, False, "Women 21+",
     "Cervical cancer screening for women."),
    ("HIV", "HIV I & II (Confidential)", 1, 549, 900, False, "12 hrs", "Blood", None, False, None,
     "Confidential HIV screening. Results are released only to the person tested."),
    ("HBSAG", "Hepatitis B Surface Antigen", 1, 449, 700, False, "12 hrs", "Blood", None, False, None,
     "Screens for hepatitis B infection."),
    ("ECG", "ECG (Electrocardiogram)", 1, 299, 500, False, "Same day", "Clinic", None, False, None,
     "Records the heart's electrical activity. First test for chest pain or palpitations."),
    ("XRAY_CHEST", "Chest X-Ray", 1, 449, 700, False, "Same day", "Imaging", None, False, None,
     "Images the lungs and heart outline. Used for persistent cough or breathlessness."),
    ("USG_ABD", "Ultrasound — Whole Abdomen", 1, 1299, 2000, True, "Same day", "Imaging", None, False, None,
     "Images liver, gallbladder, kidneys and pelvis. Ordered for abdominal pain."),
    ("SEMEN", "Semen Analysis", 8, 799, 1250, False, "24 hrs", "Sample", None, False, None,
     "Assesses male fertility. Requires 2-5 days of abstinence before the sample."),
    ("PREG", "Beta hCG (Pregnancy)", 1, 549, 900, False, "8 hrs", "Blood", None, False, "Women",
     "Confirms and dates pregnancy more precisely than a urine test."),
    ("ALLERGY", "Allergy Panel — Food & Inhalant", 36, 3499, 5200, False, "72 hrs", "Blood", None, False, None,
     "Identifies specific allergy triggers when reactions are recurrent."),
    ("FULLBODY", "Full Body Checkup — Advanced", 92, 1499, 4000, True, "24 hrs", "Blood + Urine", "Best value", True, None,
     "92 parameters covering blood, sugar, lipids, liver, kidney, thyroid and vitamins."),
    ("BASIC_PKG", "Essential Health Checkup", 45, 899, 2200, True, "24 hrs", "Blood + Urine", None, True, None,
     "45 parameters — a yearly baseline for an adult with no known condition."),
    ("DIAB_PKG", "Diabetes Care Package", 26, 1199, 2800, True, "24 hrs", "Blood + Urine", None, True, "Diabetics",
     "HbA1c, sugars, kidney and lipid panel for someone managing diabetes."),
    ("HEART_PKG", "Heart Health Package", 34, 1799, 3600, True, "24 hrs", "Blood + Clinic", None, True, None,
     "Lipids, ECG, homocysteine and inflammatory markers for cardiac risk."),
    ("WOMEN_PKG", "Women's Wellness Package", 58, 1999, 4200, True, "48 hrs", "Blood + Urine", None, True, "Women",
     "Thyroid, iron, vitamins, hormones and a full metabolic panel."),
    ("SENIOR_PKG", "Senior Citizen Package", 78, 2499, 5500, True, "48 hrs", "Blood + Urine", None, True, "60+",
     "Broad screening panel appropriate for adults over 60."),
    ("THYRO_PKG", "Thyroid Care Package", 12, 1099, 2400, False, "24 hrs", "Blood", None, True, None,
     "Full thyroid profile with antibodies and related vitamins."),
]


def build_labs() -> list:
    out = []
    for (tid, name, params, price, mrp, fasting, time, sample,
         tag, package, for_whom, why) in LAB_TESTS:
        out.append({
            "id": tid, "name": name, "params": params,
            "price": float(price), "mrp": float(mrp),
            "fasting": fasting, "time": time, "sample": sample,
            "tag": tag, "package": package, "for": for_whom,
            "why": why, "lab": "Nirog Labs (demo)",
        })
    return out


if __name__ == "__main__":
    docs, specs = build_doctors()
    labs = build_labs()
    print(f"{len(docs)} doctors across {len(specs)} specialties, {len(labs)} lab tests")
    for s in specs:
        print(f"  {s['id']:10} {s['name']:28} {s['count']}")
