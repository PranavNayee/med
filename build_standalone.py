"""Build a standalone, single-file Nirog demo.

Produces one HTML file that opens by double-clicking — no Python, no server,
no network. It works by exporting the seeded database to JSON, embedding it,
and installing an offline shim that answers the same API calls the online
build makes. The React components are byte-identical between the two builds.

What is preserved offline:
  - substitution, computed in the browser from the same composition_key the
    backend computes, so results match the server exactly
  - crisis and emergency triage, with the pattern lists exported from
    services/ai_triage.py rather than rewritten by hand
  - prescription and quantity rules, so the UI still refuses what the server
    would refuse

What is necessarily different offline: orders and cart changes live in
memory and vanish on reload, and there is no pharmacist, payment or
teleconsult path at all.

Run: python build_standalone.py <api-build.html> <output.html>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Doctor,
    FamilyMember,
    HealthRecord,
    LabTest,
    MedicationReminder,
    Product,
    Specialty,
    User,
    VitalReading,
)
from app.serializers import (  # noqa: E402
    doctor_card,
    family_member,
    health_record,
    lab_test,
    product_detail,
)
from app.services.ai_triage import CRISIS_PATTERNS, EMERGENCY_PATTERNS  # noqa: E402


def export_data() -> dict:
    """Dump the seeded database into the shape the offline shim expects."""
    app = create_app()
    with app.app_context():
        products = []
        for product in db.session.execute(
            db.select(Product).where(Product.is_active.is_(True))
        ).scalars().all():
            row = product_detail(product)
            # The two keys are what make offline substitution match the server.
            row["_ck"] = product.composition_key
            row["_mk"] = product.molecule_key
            products.append(row)

        doctors = [doctor_card(d) for d in db.session.execute(
            db.select(Doctor).where(Doctor.is_active.is_(True))
        ).scalars().all()]

        specialties = []
        for spec in db.session.execute(db.select(Specialty)).scalars().all():
            count = db.session.execute(
                db.select(db.func.count(Doctor.id)).where(Doctor.specialty_id == spec.id)
            ).scalar_one()
            specialties.append({"id": spec.code, "name": spec.name,
                                "icon": spec.icon, "color": spec.accent,
                                "count": count})

        labs = [lab_test(t) for t in db.session.execute(
            db.select(LabTest).where(LabTest.is_active.is_(True))
        ).scalars().all()]

        user = db.session.execute(db.select(User)).scalars().first()
        members = [family_member(m) for m in db.session.execute(
            db.select(FamilyMember).where(FamilyMember.user_id == user.id)
        ).scalars().all()]

        self_member = db.session.execute(
            db.select(FamilyMember).where(FamilyMember.user_id == user.id,
                                          FamilyMember.is_self.is_(True))
        ).scalar_one()

        records = [health_record(r) for r in db.session.execute(
            db.select(HealthRecord)
            .where(HealthRecord.member_id == self_member.id)
            .order_by(HealthRecord.record_date.desc())
        ).scalars().all()]

        series: dict[str, dict] = {}
        for reading in db.session.execute(
            db.select(VitalReading)
            .where(VitalReading.member_id == self_member.id)
            .order_by(VitalReading.measured_on.asc())
        ).scalars().all():
            entry = series.setdefault(reading.metric.lower(),
                                      {"label": reading.metric,
                                       "unit": reading.unit, "data": []})
            entry["data"].append({"d": reading.measured_on.strftime("%b"),
                                  "v": float(reading.value)})

        reminders = [
            {"id": r.id, "t": r.label, "d": r.dose_instruction,
             "time": r.time_of_day, "taken": r.time_of_day < "12:00"}
            for r in db.session.execute(
                db.select(MedicationReminder)
                .where(MedicationReminder.member_id == self_member.id)
                .order_by(MedicationReminder.time_of_day.asc())
            ).scalars().all()
        ]

        return {
            "products": products,
            "specialties": specialties,
            "doctors": doctors,
            "labs": labs,
            "me": {"name": user.full_name, "members": members},
            "records": records,
            "vitals": series,
            "reminders": reminders,
            "self_member_id": self_member.id,
        }


OFFLINE_SHIM = r"""
/* ====================== OFFLINE MODE ======================================
   This build has no backend. The shim below answers exactly the calls the
   online build makes, from the data embedded above, so every React component
   is unchanged between the two builds.

   Substitution is recomputed here from the same composition_key the Python
   normaliser produced, so offline results match the server.
   ====================================================================== */
const OFFLINE = true;
const DB = window.__NIROG_DATA__;
const bySku = {}; DB.products.forEach(p => { bySku[p.id] = p; });

/* Mirrors services/rx_policy.py. Kept in sync deliberately — the UI must
   refuse what the server would refuse. */
const REFUSED_SCHEDULES = ['X','NARCOTIC'];
const RX_SCHEDULES = ['H','H1'];
const MAX_RX_QTY = 3;

function policyFor(schedule, qty){
  if(REFUSED_SCHEDULES.indexOf(schedule) >= 0)
    return {allowed:false, rx:true, code:'SCHEDULE_NOT_DISPENSABLE_ONLINE',
      message:'This medicine cannot be supplied through online ordering. Please visit a pharmacy in person with your prescription.'};
  if(RX_SCHEDULES.indexOf(schedule) >= 0){
    if(qty > MAX_RX_QTY)
      return {allowed:false, rx:true, code:'RX_QUANTITY_EXCEEDED',
        message:'A maximum of '+MAX_RX_QTY+' packs of a prescription medicine can be ordered at a time.'};
    return {allowed:true, rx:true, code:'RX_REQUIRED',
      message:"A valid doctor's prescription is required. Our pharmacist will verify it before dispatch."};
  }
  return {allowed:true, rx:false, code:'OK', message:''};
}

/* Same tiering as services/substitution.py: EXACT shares composition_key,
   RELATED shares molecule_key only (different strength or form). */
function substitutesFor(sku){
  const src = bySku[sku];
  if(!src) return null;
  const mk = (list) => list.map(p => {
    const saving = Math.max(0, src.price - p.price);
    return Object.assign({}, p, {
      saving: saving,
      saving_percent: src.price ? Math.round(saving * 100 / src.price) : 0,
    });
  }).sort((a,b) => a.price - b.price);

  const exact = mk(DB.products.filter(p => p.id !== sku && p._ck === src._ck));
  const related = mk(DB.products.filter(p => p.id !== sku && p._mk === src._mk && p._ck !== src._ck));
  const cheaper = exact.filter(p => p.saving > 0);
  return {source:src, exact:exact, related:related,
          best_saving: cheaper.length ? cheaper[0] : null, unparsed:false};
}

/* Exported verbatim from services/ai_triage.py so the offline build escalates
   on exactly the same language the server does. */
const CRISIS_RE = __CRISIS_PATTERNS__.map(p => new RegExp(p, 'i'));
const EMERGENCY_RE = __EMERGENCY_PATTERNS__.map(p => new RegExp(p, 'i'));

function triage(message){
  const text = message || '';
  if(CRISIS_RE.some(r => r.test(text))) return {
    level:'CRISIS', model_used:false,
    message:"It sounds like you're going through something very painful, and I'm glad you said something. I'm not able to help with this the way a person can \u2014 please talk to someone who can support you properly, right now.",
    actions:[
      {type:'helpline', label:'Tele-MANAS (24\u00d77, free)', value:'14416', primary:true},
      {type:'helpline', label:'KIRAN mental health helpline', value:'1800-599-0019'},
      {type:'consult', label:'Talk to a psychologist on Nirog', specialty:'psych'},
      {type:'contact', label:'Call someone you trust'},
    ]};
  if(EMERGENCY_RE.some(r => r.test(text))) return {
    level:'EMERGENCY', model_used:false,
    message:"What you've described may need emergency medical care. Please don't wait for an online answer \u2014 call an ambulance or get to the nearest emergency department now.",
    actions:[
      {type:'call', label:'Ambulance \u00b7 108', value:'108', primary:true},
      {type:'navigate', label:'Nearest hospitals', route:'emergency'},
      {type:'contact', label:'Alert my emergency contacts'},
    ]};
  return {
    level:'ROUTINE', model_used:false,
    message:"This offline demo doesn't answer symptom questions \u2014 that needs the backend, and even then it never diagnoses or names a medicine. A short consultation with a doctor is the reliable next step.",
    actions:[{type:'consult', label:'Consult a doctor', specialty:'gp', primary:true}],
  };
}

/* In-memory cart and orders. These reset on reload — there is nowhere to
   persist them, and pretending otherwise would be worse than saying so. */
let OFFLINE_CART = {};
let OFFLINE_ORDERS = [];

function cartPayload(){
  let subtotal = 0, mrpTotal = 0, allowed = true, requiresRx = false;
  const items = Object.keys(OFFLINE_CART).map(sku => {
    const p = bySku[sku], qty = OFFLINE_CART[sku];
    const pol = policyFor(p.schedule, qty);
    if(!pol.allowed) allowed = false;
    if(pol.rx) requiresRx = true;
    subtotal += p.price * qty; mrpTotal += p.mrp * qty;
    return Object.assign({}, p, {qty:qty, line_total:+(p.price*qty).toFixed(2),
      policy:{allowed:pol.allowed, requires_prescription:pol.rx, reason:pol.code, message:pol.message}});
  });
  const delivery = (subtotal >= 499 || subtotal === 0) ? 0 : 40;
  return {items:items, subtotal:+subtotal.toFixed(2), mrp_total:+mrpTotal.toFixed(2),
    you_save:+(mrpTotal-subtotal).toFixed(2), delivery:delivery,
    total:+(subtotal+delivery).toFixed(2),
    checkout_allowed: allowed && items.length > 0, requires_prescription: requiresRx};
}

/* Replaces the network client. Same signature, same return shapes. */
async function api(path, options){
  options = options || {};
  const method = options.method || 'GET';
  const body = options.body || {};
  const [route, qs] = path.split('?');
  const params = new URLSearchParams(qs || '');

  if(route === '/auth/login' || route === '/auth/register')
    return {token:'offline-demo', user:{name:DB.me.name}};
  if(route === '/me') return DB.me;
  if(route === '/specialties') return {items:DB.specialties};
  if(route === '/doctors'){
    const spec = params.get('specialty');
    return {items: (spec && spec !== 'all') ? DB.doctors.filter(d => d.specId === spec) : DB.doctors};
  }
  if(route === '/lab-tests'){
    const pkg = params.get('packages');
    if(pkg === null) return {items:DB.labs};
    return {items: DB.labs.filter(l => l.package === (pkg === '1'))};
  }
  if(route === '/catalog/products'){
    const q = (params.get('q')||'').toLowerCase();
    const cat = params.get('category');
    let items = DB.products;
    if(q) items = items.filter(p =>
      (p.name+' '+p.salt+' '+(p.cat||'')).toLowerCase().indexOf(q) >= 0);
    if(cat && cat !== 'all') items = items.filter(p => p.cat === cat);
    return {items:items, page:1, size:items.length, total:items.length};
  }
  if(route === '/catalog/categories'){
    const counts = {};
    DB.products.forEach(p => { if(p.cat) counts[p.cat] = (counts[p.cat]||0)+1; });
    return {items: Object.keys(counts).sort().map(n => ({name:n, count:counts[n]}))};
  }
  let m;
  if((m = route.match(/^\/catalog\/products\/([^/]+)\/substitutes$/))){
    const r = substitutesFor(decodeURIComponent(m[1]));
    if(!r) { const e = new Error('Not found'); e.status = 404; throw e; }
    return Object.assign({}, r, {note:'Same-salt substitutes contain the identical active ingredient at the same strength. Related products differ in strength or pack and are shown for information only \u2014 do not switch to one without asking your doctor or pharmacist.'});
  }
  if((m = route.match(/^\/catalog\/products\/([^/]+)$/))){
    const p = bySku[decodeURIComponent(m[1])];
    if(!p) { const e = new Error('Not found'); e.status = 404; throw e; }
    return p;
  }
  if(route.match(/^\/members\/\d+\/records$/)) return {items:DB.records, counts:{total:DB.records.length}};
  if(route.match(/^\/members\/\d+\/vitals$/)) return {series:DB.vitals};
  if(route.match(/^\/members\/\d+\/reminders$/)) return {items:DB.reminders};
  if(route === '/orders' && method === 'GET') return {items:OFFLINE_ORDERS};
  if(route === '/cart' && method === 'GET') return cartPayload();
  if(route === '/cart/items' && method === 'POST'){
    const p = bySku[body.sku];
    if(!p) { const e = new Error('Not found'); e.status = 404; throw e; }
    const qty = (OFFLINE_CART[body.sku]||0) + (body.quantity||1);
    const pol = policyFor(p.schedule, qty);
    if(!pol.allowed){ const e = new Error(pol.message); e.code = pol.code; throw e; }
    OFFLINE_CART[body.sku] = qty;
    return cartPayload();
  }
  if(route.match(/^\/cart\/items\//) && method === 'DELETE'){
    delete OFFLINE_CART[decodeURIComponent(route.split('/').pop())];
    return cartPayload();
  }
  if(route === '/orders' && method === 'POST'){
    const cart = cartPayload();
    if(!cart.items.length){ const e = new Error('Your cart is empty'); throw e; }
    if(cart.requires_prescription){
      const e = new Error('This demo runs without a backend, so prescription medicines cannot be ordered. On the full build a pharmacist verifies the prescription before dispatch.');
      e.code = 'RX_DISPATCH_DISABLED'; throw e;
    }
    const order = {id:'NRG'+Math.random().toString(36).slice(2,10).toUpperCase(),
      status:'CONFIRMED', total:cart.total, items:cart.items,
      created_at:new Date().toISOString(), timeline:[]};
    OFFLINE_ORDERS.unshift(order); OFFLINE_CART = {};
    return order;
  }
  if(route === '/ai/triage' && method === 'POST') return triage(body.message);
  if(route === '/emergency') return {
    primary_helpline:{label:'Ambulance \u00b7 108', number:'108'},
    capabilities:{shares_live_location_with_contacts:false, books_ambulance:false, notifies_hospital:false},
    notice:'This offline demo cannot alert anyone. In an emergency, call 108.',
    contacts:[]};

  const err = new Error('Not available in the offline demo'); err.status = 501; throw err;
}
"""

OFFLINE_BANNER = r"""
<div id="offline-banner" style="position:fixed;bottom:0;left:0;right:0;z-index:9999;
  background:#0a4d42;color:#d6f4ee;font:500 12.5px/1.5 Inter,system-ui,sans-serif;
  padding:9px 16px;display:flex;gap:10px;align-items:center;justify-content:center;
  box-shadow:0 -2px 12px rgba(8,60,52,.2)">
  <span style="width:7px;height:7px;border-radius:50%;background:#f5a623;flex:0 0 auto"></span>
  <span>Offline demo &mdash; sample data, nothing is saved. Prescription ordering, payments and consultations need the backend.</span>
  <button onclick="document.getElementById('offline-banner').remove()"
    style="background:none;border:none;color:#7fe0cf;cursor:pointer;font-size:16px;line-height:1;padding:0 4px">&times;</button>
</div>
"""


def build(api_html: str, data: dict, vendor_dir: Path | None = None) -> str:
    out = api_html

    # 1. Strip the network client and its boot login; keep everything after.
    start = out.index("/* ============================== API CLIENT")
    end = out.index("async function boot(){")
    header = out[start:end]

    # Keep the accent/store/decorate helpers, drop only the fetch client.
    keep_from = header.index("/* Accent colours are presentation only")
    helpers = header[keep_from:]

    shim = (OFFLINE_SHIM
            .replace("__CRISIS_PATTERNS__", json.dumps(CRISIS_PATTERNS))
            .replace("__EMERGENCY_PATTERNS__", json.dumps(EMERGENCY_PATTERNS)))

    out = out[:start] + shim + "\n" + helpers + out[end:]

    # 2. Embed the data before the app script runs.
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # never let a string close the tag
    anchor = '<script crossorigin src="https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js"></script>'
    out = out.replace(anchor,
                      f"<script>window.__NIROG_DATA__ = {payload};</script>\n" + anchor, 1)

    # 3. Inline React, ReactDOM and htm so the file needs no network at all.
    #    Without this the page still opens from file:// but silently fails
    #    offline, which is a worse failure than not opening.
    if vendor_dir and vendor_dir.exists():
        cdn_scripts = re.findall(
            r'<script crossorigin src="https://cdnjs\.cloudflare\.com[^"]*"></script>\n?', out)
        vendor_files = ["react.production.min.js", "react-dom.production.min.js",
                        "htm.umd.js"]
        bundle_parts = []
        for name in vendor_files:
            path = vendor_dir / name
            if not path.exists():
                raise SystemExit(f"missing vendor file: {path}")
            bundle_parts.append(f"/* {name} */\n" + path.read_text(encoding="utf-8"))
        bundle = "<script>\n" + "\n".join(bundle_parts) + "\n</script>\n"
        for tag in cdn_scripts:
            out = out.replace(tag, "", 1)
        # Insert immediately before the embedded data + app script. Anchoring
        # on the data block is stable; anchoring on the shim's leading
        # whitespace is not.
        anchor = "<script>window.__NIROG_DATA__"
        if anchor not in out:
            raise SystemExit("data anchor missing — cannot place vendor bundle")
        out = out.replace(anchor, bundle + anchor, 1)
        if "react.production.min.js" not in out:
            raise SystemExit("vendor bundle failed to inline")
        print(f"  inlined {len(cdn_scripts)} vendor scripts ({len(bundle):,} bytes)")

    # 4. Google Fonts need the network. Drop the request and let the CSS fall
    #    back to the system stack already declared in --display / --body.
    out = re.sub(r'<link rel="preconnect"[^>]*>\n?', "", out)
    out = re.sub(r'<link href="https://fonts\.googleapis\.com[^"]*"[^>]*>\n?', "", out)

    # 5. Offline banner, so nobody mistakes the demo for the product.
    out = out.replace('<div id="root"></div>', '<div id="root"></div>\n' + OFFLINE_BANNER, 1)
    out = out.replace("<title>Nirog", "<title>Nirog (offline demo)", 1)

    return out


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "frontend/nirog.html")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "frontend/nirog-standalone.html")
    print(f"building standalone: {src} -> {dst}")
    data = export_data()
    print(f"  embedded {len(data['products'])} products, {len(data['doctors'])} doctors, "
          f"{len(data['labs'])} lab tests, {len(data['records'])} records")
    vendor = Path(__file__).parent / "vendor"
    dst.write_text(build(src.read_text(encoding="utf-8"), data, vendor), encoding="utf-8")
    print(f"  wrote {dst.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
