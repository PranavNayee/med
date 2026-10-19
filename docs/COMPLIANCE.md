# Compliance and safety notes

This is engineering documentation, not legal advice. Get an opinion from a
lawyer who practises in Indian pharmaceutical and health-data regulation
before this handles a real patient or a real rupee. What follows is what
the code assumes, so that the assumptions are visible rather than buried.

## 1. Dispensing medicines

The Drugs and Cosmetics Act 1940 and the Drugs Rules 1945 govern this.
Schedule H and H1 medicines require a valid prescription and dispensing by
or under the supervision of a registered pharmacist. Schedule H1 carries
an additional register with a three-year retention requirement. Schedule X
and narcotics carry duplicate-copy and storage duties that ordinary
e-commerce fulfilment does not satisfy.

**What the code does**

- `services/rx_policy.py` is the only place that reads `drug_schedule`.
- Schedule X and narcotics are refused outright, not partially supported.
- An unrecognised schedule value is treated as the most restrictive case,
  never as OTC. Failing closed matters more than catalogue tidiness.
- `Order.transition_to()` refuses to reach `CONFIRMED` or `PACKED` on a
  prescription order without a pharmacist-verified prescription attached.
  This is enforced in the model, so no API route can bypass it.
- `ENABLE_RX_DISPATCH` defaults to false and the checkout route returns
  503 while it is off.

**What you must supply before enabling it**

1. A valid retail drug licence, recorded on the `pharmacies` row.
2. A registered pharmacist, with their registration number, and a staff
   authentication provider wired to
   `POST /api/pharmacist/orders/<n>/verify`. That endpoint currently
   returns 501 by design — it must not accept customer JWTs.
3. Schedule H1 register generation and retention.
4. A decision on which states you ship to, since state drug controllers
   differ in their treatment of online supply.

## 2. Telemedicine

The Telemedicine Practice Guidelines 2020 (appended to the Indian Medical
Council regulations) require a Registered Medical Practitioner, patient
consent, and specific rules on which medicine lists may be prescribed by
which consultation mode.

**What the code does**

- `doctors.nmc_registration_number` is mandatory and
  `registration_verified_at` must be set before a doctor is bookable.
- `GET /api/doctors` filters on both. An unverified doctor is invisible,
  not shown as "pending".
- The registration number is returned to the client, because patients are
  entitled to verify it themselves.
- Booking returns 503 until `ENABLE_TELECONSULT` is set.

**Before enabling:** verify each registration against the NMC register,
store the evidence, and implement consent capture at the start of every
consultation.

## 3. The AI assistant

The prototype's symptom assistant recommended specific medicines and doses
in plain text — for example a paracetamol dose and a pantoprazole
suggestion. That is prescribing. It has been removed entirely rather than
softened, and client-side triage no longer exists.

**Layered design, most authoritative first**

1. **Crisis screening.** Language suggesting self-harm or suicidal
   ideation returns human support options immediately — Tele-MANAS 14416
   and KIRAN 1800-599-0019. No model is consulted, no AI-written reply is
   produced, and the exchange does not continue as a chat.
2. **Emergency screening.** Possible medical emergencies return
   emergency guidance and stop.
3. **Model call**, only for messages clearing both. The system prompt
   forbids diagnosis, medicine names, dose advice, lab interpretation and
   any suggestion that seeing a doctor is unnecessary.
4. **Output validation.** Replies matching diagnostic or dosing patterns
   are discarded whole and replaced with a safe fallback. A partially
   scrubbed answer is not shipped.

Escalations are audited by rule name only. The message body is not logged
— the fact of the escalation is operationally necessary; its content is
not, and storing it would create a record of someone's worst moment.

Both pattern lists are deliberately broad. A false positive costs one
extra tap. A false negative can cost far more.

**Before setting `AI_PROVIDER`:** have a clinician review both pattern
lists and the system prompt, and run adversarial testing specifically on
paraphrase — people rarely use the exact words in the list.

## 4. Emergency SOS

The prototype's SOS screen said it "books the nearest available
ambulance". Nothing in the system can do that. Promising dispatch you
cannot deliver is worse than promising nothing, because someone may wait
for it.

`GET /api/emergency` returns an explicit capability object:

```json
"capabilities": {
  "shares_live_location_with_contacts": true,
  "books_ambulance": false,
  "notifies_hospital": false
}
```

and a notice telling the user to call 108. If you later integrate a real
dispatch partner, flip the flag — do not flip the copy first.

## 5. Health data under the DPDP Act 2023

Health data is sensitive personal data.

**What the code does**

- `Consent` rows are per-purpose, timestamped and versioned. Withdrawal
  is recorded, never deleted, so the audit trail can show consent existed
  at the time of processing.
- Clinical records hang off `family_members`, not off the account, so a
  parent ordering for a child does not merge the two histories.
- `_owns_member()` guards every member-scoped route; a cross-tenant read
  returns 403.
- `AuditLog` is append-only and records who read health records, when.
- API responses carry `Cache-Control: no-store`.
- IP addresses are hashed, not stored raw.
- Uploaded prescriptions store an object key and a SHA-256 digest — the
  database never holds file bytes or a public URL.

**Still to do:** data retention and deletion policy, a Data Protection
Officer if you cross the Significant Data Fiduciary threshold, breach
notification runbook, and encryption at rest on the object store.

## 6. Pricing

DPCO ceiling prices for scheduled formulations are a legal cap.
`Product.nppa_ceiling_price_paise` holds the cap and
`price_breaches_ceiling()` checks it; the product detail endpoint surfaces
a `compliance_warning` rather than quietly selling above the cap.

The ceiling column is empty in the seed data. Populate it from NPPA
before selling anything.

## 7. Catalogue data provenance

The seeded catalogue is demo data. `content_source` on every product says
so explicitly, and `content_reviewed_by` is null.

Drug monographs on commercial pharmacy sites are copyrighted editorial.
Do not scrape them. Either license clinical content, or generate
molecule-level content from open sources and have a registered pharmacist
review it before publishing — and record that review in
`content_reviewed_by` / `content_reviewed_at`, which exist for exactly
this purpose.

## Pre-launch checklist

- [ ] Drug licence recorded; pharmacist auth provider wired
- [ ] Schedule H1 register implemented
- [ ] Doctor registrations verified against the NMC register
- [ ] Clinician sign-off on triage patterns and system prompt
- [ ] NPPA ceiling prices loaded
- [ ] Licensed product master replacing the demo catalogue
- [ ] Clinical content licensed or pharmacist-reviewed
- [ ] Object storage with encryption at rest for prescriptions
- [ ] Retention and deletion policy
- [ ] `SECRET_KEY` set; `APP_ENV` not `development`
- [ ] Demo login removed from `boot()` in the frontend
- [ ] Penetration test, with authorisation boundaries specifically in scope
