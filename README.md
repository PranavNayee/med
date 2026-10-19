# Nirog — full-stack build

The prototype was a single HTML file with every medicine, doctor and lab
result hard-coded in JavaScript. This is the same product with a real
backend, a real database, and the frontend rewired to consume it.

```
frontend/nirog.html        the original UI, now API-driven
backend/
  bootstrap_db.py          idempotent DB setup: tables, seed, CSV import
  wsgi.py                  gunicorn entrypoint (production)
  data/                    A_Z_medicines_dataset_of_India.csv
  app/
    config.py              validated, env-driven config with feature gates
    models/                catalog · order · clinical · user
    services/
      composition.py       composition -> deterministic substitution key
      substitution.py      tiered substitute lookup
      rx_policy.py         Schedule H/H1/X gating
      ai_triage.py         red-flag + crisis screening
    api/                   catalog · commerce · clinical blueprints
    security.py            JWT, password hashing, audit trail
  migrations/001_initial_mysql.sql
  seed.py
build_frontend.py          regenerates frontend/nirog.html from the prototype
docs/COMPLIANCE.md         read this before going live
```

## Run it

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bootstrap_db.py          # tables + seed + all 254k medicines (~1 min)
#   or: python bootstrap_db.py --skip-import   (27 demo products, instant)
python run.py                   # http://127.0.0.1:5000
```

The medicine database is `backend/data/A_Z_medicines_dataset_of_India.csv`
(253,973 rows). It is the source of truth; the SQL database is built from it
by `bootstrap_db.py`, which is idempotent and resumes an interrupted import.

## Deploy: API on Render, frontend on Netlify

Push this folder (`med/`) to GitHub as the repository root, then:

**1. Backend — Render.** Dashboard → New → Blueprint → pick the repo.
`render.yaml` creates the `nirog-api` web service and a `nirog-db`
PostgreSQL database and links them. When prompted, set `CORS_ORIGINS` to
your Netlify URL (you can fill it in after step 2 and redeploy). The first
boot imports the CSV into Postgres, which takes a few minutes; later boots
skip it. Check `https://nirog-api.onrender.com/api/health`.

Render's free Postgres expires after 30 days. For a database that stays,
create a free [Neon](https://neon.tech) project, delete the `databases:`
block and the `fromDatabase` entry from `render.yaml`, and set
`DATABASE_URL` on the service to Neon's connection string. The catalogue
uses about 175 MB.

**2. Frontend — Netlify.** Add new site → Import from Git → same repo.
`netlify.toml` sets everything; just add the environment variable
`NIROG_API_BASE=https://nirog-api.onrender.com` (your Render URL, no `/api`)
and deploy. The build writes `frontend/config.js` from it. The site root
serves `nirog.html`; `nirog-standalone.html` is also published and needs no
backend.

Render's free tier sleeps after 15 minutes idle, so the first request after
that takes ~30–60 s while it wakes up.

Then serve the frontend from any static server:

```bash
cd ../frontend && python -m http.server 8080
```

Open `http://localhost:8080/nirog.html`. Demo login is wired in `boot()`:
`9825000000` / `nirog-demo-2026`. Replace it with the real OTP flow.

### MySQL

SQLite is the default so the project runs with no setup. For MySQL:

```sql
CREATE DATABASE nirog CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```
```bash
mysql -u root -p nirog < migrations/001_initial_mysql.sql
# then in .env:
# DATABASE_URI=mysql+pymysql://nirog:PASSWORD@127.0.0.1:3306/nirog?charset=utf8mb4
```

## The part that matters: substitution

The catalogue does not store a substitutes list. Every product carries a
`composition_key` derived from its printed composition, so these all
collapse to `metformin:500mg|ORAL_SOLID`:

| Printed on the pack | Key |
|---|---|
| `Metformin Hydrochloride (500mg)` | `metformin:500mg` |
| `Metformin HCl 0.5g` | `metformin:500mg` |
| `METFORMIN IP 500 MG` | `metformin:500mg` |

Adding one new product automatically makes it a substitute for every
existing product sharing that key. No backfill, no manual mapping.

Spelling variants that survive normalisation (`Amoxycillin` vs
`Amoxicillin`, `Clavulanic Acid` vs `Clavulanate Potassium`) are resolved
through the `molecule_aliases` table. That table is the curated layer —
it is the thing no purchased dataset gives you, and it is where ongoing
effort goes.

Results come back in two tiers, and the distinction is clinical, not
cosmetic:

- **EXACT** — same salts, same strengths, same form family. Offered as a
  one-tap swap.
- **RELATED** — same salts, different strength or form. Shown for
  information with an explicit warning. Dolo 650 is *related* to Crocin
  500, not a substitute for it. The prototype's flat `subs` array made no
  such distinction.

A product whose composition cannot be parsed is excluded from
substitution entirely rather than grouped with a guess.

## Feature gates

Four capabilities are off by default and refuse to run rather than
degrade quietly:

| Gate | What it unlocks | Blocked because |
|---|---|---|
| `ENABLE_RX_DISPATCH` | Ordering Schedule H/H1 medicines | Needs a drug licence and a registered pharmacist wired to the verification endpoint |
| `ENABLE_TELECONSULT` | Booking consultations | Needs practitioner agreements + Telemedicine Practice Guidelines compliance |
| `ENABLE_LIVE_PAYMENTS` | Real money movement | Needs a gateway and reconciliation |
| `AI_PROVIDER` | Model-generated symptom guidance | Needs the clinical review in `docs/COMPLIANCE.md` |

Setting `ENABLE_RX_DISPATCH=true` alongside
`PHARMACIST_VERIFICATION_REQUIRED=false` aborts startup. That combination
is unlawful, so it isn't configurable.

## What's wired vs. stubbed

**Working end to end:** catalogue search, product detail, substitution,
cart with server-enforced Rx rules, checkout with idempotency, order state
machine, prescription upload, family profiles, health-record timeline,
vitals trends, medication reminders, doctor and lab listings, AI triage
screening, emergency screen, audit log.

**Deliberately stubbed, returning explicit 501/503:** pharmacist
verification console, consultation booking, WebRTC video, payment
gateway, insurance, ABDM/ABHA linkage, object storage for uploads.

Each stub returns a message naming what has to exist first. None of them
fail silently or pretend to work.

## Regenerating the frontend

`build_frontend.py` patches the original prototype rather than replacing
it, so design changes made in the prototype carry forward:

```bash
python build_frontend.py path/to/nirog.html frontend/nirog.html
```

It will fail loudly if the prototype's structure has changed enough that
a patch anchor no longer matches.

## Accounts, orders and payments

- Mobile number + password accounts (`/auth/register`, `/auth/login`),
  throttled after 10 failed attempts in 15 minutes. SMS OTP needs an SMS
  provider and DLT-registered templates before it can be added.
- Guests can browse; cart, checkout, orders, profile and records need login.
- Server-side cart, address book, checkout (cash on delivery or online),
  order timeline and cancellation.
- **Online payment (Razorpay):** set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`
  and, for the webhook, `RAZORPAY_WEBHOOK_SECRET`. Test keys work at once;
  live keys also need `ENABLE_LIVE_PAYMENTS=true`. In the Razorpay dashboard
  add the webhook `https://<api>/api/payments/razorpay/webhook` for
  `payment.captured` and `order.paid`. An order is marked paid only after the
  server verifies Razorpay's signature; the webhook covers customers who
  close the tab. Cancelling a paid order marks it `REFUND_PENDING`: issue the
  refund from the Razorpay dashboard.
- Prescription (Rx) medicines can't be checked out until
  `ENABLE_RX_DISPATCH=true`, which needs a drug licence and a pharmacist
  (docs/COMPLIANCE.md).

## Medicine information

Product pages show composition, manufacturer, schedule, pack and price per
unit for every product, plus patient information (uses, how it works, side
effects, safety advice for alcohol / pregnancy / breastfeeding / driving /
kidney / liver, directions, storage) **when a pharmacist has published it**.

Information is stored per molecule (`molecule_content`), so one reviewed
paracetamol entry covers every paracetamol brand. Content comes from a CSV:

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" -F file=@medicine_content.csv \
     https://<api>/api/admin/content        # upload / update
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://<api>/api/admin/content/template
```

Columns: `molecule, uses, how_it_works, side_effects, alcohol, pregnancy,
breastfeeding, driving, kidney, liver, directions, storage, source,
reviewed_by, reviewed_at, status`. Lists use `|`; safety cells are
`level: note` with level `safe | caution | unsafe | consult`. Rows are shown
only with `status=published`, a `source` and a `reviewed_by`; the upload
returns a report of rows it skipped and why.

`backend/data/medicine_content_drafts.csv` holds 8 unreviewed drafts from the
prototype as a starting point. They are marked `draft` and are not shown.
Write content from an openly licensed or your own reviewed source, never
copied from another pharmacy site.

## Prescription reader

`Upload prescription` (Pharmacy page and sidebar) reads a photo or PDF of a
prescription with Google Gemini, matches each medicine to the catalogue, and
lists products with **exactly the same salts, strength and form** at a lower
price. It never suggests a different medicine for a condition.

- `backend/app/services/prescription_reader.py` sends the image with
  `store=False`, asks for a strict JSON transcription, and keeps the model to
  copying what was written. Unclear lines come back as `confidence: low`.
- `backend/app/services/prescription_match.py` matches brands first, generics
  by composition (trying every alias spelling), and flags a line
  `needs_review` when the match is a guess: a strength that wasn't written,
  an extra word in the brand ("Pan-D" for "Pan 40"), a different form, or
  close candidates with different compositions.
- `POST /api/prescriptions/read` needs `GEMINI_API_KEY` and is capped at
  `PRESCRIPTION_READS_PER_DAY` per user. `POST /api/prescriptions/match`
  takes typed lines and needs no AI, so the screen still works without a key.
- The uploaded file is processed in memory and not stored by Nirog. The
  screen says the image goes to Google's Gemini service before anyone uploads.
- **Use a paid-tier Gemini key (billing enabled) for real prescriptions.** On
  the free tier Google's terms let it use submitted content to improve its
  products, which is not acceptable for patients' health records.

## Two builds

| File | Needs | Use it for |
|---|---|---|
| `frontend/nirog-standalone.html` | nothing — double-click it | demos, client review, sharing by email |
| `frontend/nirog.html` | the Flask backend running | development, real data, real orders |

### Standalone build

`nirog-standalone.html` is one self-contained file. React, ReactDOM and htm
are inlined, the seeded catalogue is embedded as JSON, and an offline shim
answers the same API calls the online build makes — so the React components
are identical between the two.

Substitution offline is recomputed in the browser from the same
`composition_key` the Python normaliser produced, so results match the
server exactly. Crisis and emergency triage patterns are exported from
`services/ai_triage.py` at build time rather than rewritten by hand, so the
two cannot drift apart. Prescription and quantity rules are enforced too:
the demo refuses what the server would refuse.

What is necessarily different: the cart and orders live in memory and reset
on reload, there is no pharmacist, payment, teleconsult or prescription
path, and the emergency screen states plainly that it cannot alert anyone.
A dismissible banner says so on screen, so nobody mistakes the demo for the
product.

Fonts fall back to the system stack, since Google Fonts would need the
network.

Rebuild it after changing the catalogue or the triage rules:

```bash
cd backend && python seed.py --reset && cd ..
python build_standalone.py frontend/nirog.html frontend/nirog-standalone.html
```

### Doctors, AI and uploads

`demo_directory.py` and `demo_components.py` feed `build_full_catalog.py`.

**Doctor directory.** 312 doctors across 26 specialties, with city, hospital,
qualification, experience, languages, fee, mode and next slot, so the filters
and the AI routing have something to work on. Fees track experience and how
specialised the field is. All of it is FICTIONAL - registration numbers are
stamped DEMO- and a banner on the page says so. A live directory has to come
from NMC-verified registrations with a signed agreement per doctor.

**Grounded assistant.** The old offline assistant answered every question with
"this needs the backend". It now routes to the three data sources that are
actually present: the 254,000-product index, the doctor directory, and the
35-test lab catalogue.

Routing is intent-driven, and the fixed ordering it replaced was the bug worth
recording: with catalogue lookup first, "doctor for my child fever" matched a
syrup called Childryl, and "do I fast for a lipid profile" matched a brand
called Acondec-Fast. Explicit doctor or lab intent now wins over a name
collision. Separately, candidate phrases are scored rather than taken
first-match: "how much dose of paracetamol" offered "much" (a prefix of the
brand Muchek) before "paracetamol", and first-wins answered about the wrong
drug entirely.

What it will not do, by design: diagnose, suggest a dose, or name a medicine
for a symptom. Crisis and emergency phrasing is checked before anything else
runs and short-circuits to helplines. Where the honest answer is "a doctor
needs to look at this", that is the answer.

**Uploads.** One `FileUpload` component behind every upload point -
prescriptions, lab reports, health records, insurance claims, consultation
attachments. PDF, JPG, PNG, WEBP and HEIC, validated for type and size, image
previews, up to 8 files. Each context states what would happen to the file in
the full build, and that offline it goes nowhere.

**Search from any tab.** `SearchSuggest` previously had no Enter handler, so
typing and pressing Enter from Home did nothing. Enter now lands on the
pharmacy with the query intact via a `searchAll` store action - `go()` clears
the query by design, so it could not be routed through that.

**AI action buttons.** The escalation panel only rendered `urgent` and `spec`
and ignored the `actions` array entirely, so every suggested next step was
dead. It now renders them, wired to tel: links, product pages, and specialty-
filtered doctor lists.
