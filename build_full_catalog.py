"""Rebuild the standalone demo so it carries the WHOLE catalogue, searchably.

    python build_full_catalog.py

The bug this fixes
------------------
`boot()` called `/catalog/products?size=48`, put those 48 rows in `MEDS`, and
`Pharmacy()` filtered that array. So the search box only ever searched 48
medicines. With the 27 seeded products that looked fine; with 254,000 it means
almost every search returns "No medicines found".

Two things had to change. The search box now queries an index instead of
filtering a preloaded page, and the page carries the whole catalogue instead of
a sample of it.

Fitting 254,000 products in one HTML file
-----------------------------------------
As JSON the catalogue is roughly 180 MB — not deliverable, and the browser
would stall parsing it. But only the brand name is high-cardinality:

    names          249,424 distinct
    compositions    11,823
    manufacturers    7,663
    pack labels      1,935
    dosage forms        25

So everything except the name is dictionary-encoded to a small integer, the
columns are packed into typed arrays, the whole container is gzipped, and the
browser inflates it with DecompressionStream. Roughly 11 MB of columns becomes
a few MB on the wire.

Search and substitution then run against counting-sorted indexes built once at
load: name-substring scan over a single joined string, composition search
through the salt dictionary, and O(1) substitute lookup by composition-key
group. No linear scan over 254,000 objects anywhere.
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import re
import sqlite3
import struct
import sys
from array import array
from pathlib import Path

from demo_directory import build_doctors, build_labs
from demo_components import AI_JS, UPLOAD_JS, DOCTORS_JS, DOCTORS_CSS

ROOT = Path(__file__).parent
DB = ROOT / "backend" / "instance" / "nirog.db"
SRC = ROOT / "frontend" / "nirog-standalone.html"
OUT = ROOT / "frontend" / "nirog-full.html"

# Editorial content keyed by molecule — the imported rows have none, so this is
# matched against the composition text at display time. Everything else shows an
# explicit "not available" rather than an invented description.
CONTENT = {
    "paracetamol": {
        "uses": ["Fever", "Headache", "Body ache", "Mild to moderate pain"],
        "how": "Blocks chemical messengers in the brain that signal pain and raise body temperature.",
        "side": ["Nausea (rare)", "Allergic skin rash (rare)", "Liver strain if overdosed"],
        "directions": "As directed by your doctor. Do not exceed 4g in 24 hours.",
        "storage": "Store below 30\u00b0C, away from direct sunlight.",
    },
    "azithromycin": {
        "uses": ["Bacterial throat infection", "Respiratory infections", "Skin infections", "Typhoid"],
        "how": "Stops bacteria producing essential proteins, halting their growth.",
        "side": ["Nausea", "Stomach upset", "Diarrhoea", "Headache"],
        "directions": "Take exactly as prescribed and complete the full course.",
        "storage": "Store below 25\u00b0C in a dry place.",
    },
    "metformin": {
        "uses": ["Type 2 diabetes", "Blood sugar control"],
        "how": "Lowers glucose production by the liver and improves how the body responds to insulin.",
        "side": ["Nausea", "Metallic taste", "Loose stools (early)", "Reduced appetite"],
        "directions": "Take with or just after meals, as advised by your doctor.",
        "storage": "Store below 30\u00b0C.",
    },
    "amlodipine": {
        "uses": ["High blood pressure", "Angina (chest pain)"],
        "how": "Relaxes blood vessels so the heart does not have to work as hard to pump blood.",
        "side": ["Ankle swelling", "Headache", "Flushing", "Tiredness"],
        "directions": "Take at the same time each day. Do not stop without medical advice.",
        "storage": "Store below 30\u00b0C.",
    },
    "atorvastatin": {
        "uses": ["High cholesterol", "Prevention of heart attack and stroke"],
        "how": "Blocks the liver enzyme that makes cholesterol, lowering LDL in the blood.",
        "side": ["Muscle ache", "Nausea", "Constipation", "Raised liver enzymes"],
        "directions": "Usually taken once daily, often at night. Continue even if you feel well.",
        "storage": "Store below 30\u00b0C.",
    },
    "pantoprazole": {
        "uses": ["Acid reflux", "Gastric and duodenal ulcer", "Heartburn"],
        "how": "Shuts down the acid pumps in the stomach lining, reducing acid production.",
        "side": ["Headache", "Diarrhoea", "Stomach pain", "Dry mouth"],
        "directions": "Take before food, usually in the morning.",
        "storage": "Store below 30\u00b0C.",
    },
    "cetirizine": {
        "uses": ["Allergy", "Runny nose", "Sneezing", "Itching and hives"],
        "how": "Blocks histamine, the chemical the body releases during an allergic reaction.",
        "side": ["Drowsiness", "Dry mouth", "Tiredness"],
        "directions": "Usually taken at night because it can cause drowsiness.",
        "storage": "Store below 30\u00b0C.",
    },
    "amoxycillin": {
        "uses": ["Throat infection", "Chest infection", "Ear infection", "Urinary infection"],
        "how": "Breaks down the cell wall bacteria need to survive.",
        "side": ["Nausea", "Loose stools", "Rash"],
        "directions": "Complete the full course even if you feel better sooner.",
        "storage": "Store below 25\u00b0C.",
    },
}


# ---------------------------------------------------------------------------
# 1. Read the catalogue and build the columnar index
# ---------------------------------------------------------------------------

def build_index() -> bytes:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT p.sku, p.brand_name, p.composition_text, p.pack_label, p.dosage_form,
               p.selling_price_paise, p.mrp_paise, p.drug_schedule, p.category,
               p.composition_key, p.molecule_key, p.is_active, p.is_prescription_required,
               COALESCE(m.name,'') AS mfr
        FROM products p
        LEFT JOIN manufacturers m ON m.id = p.manufacturer_id
        ORDER BY p.brand_name COLLATE NOCASE
    """).fetchall()
    con.close()
    n = len(rows)
    print(f"  {n:,} products")

    dicts: dict[str, list[str]] = {}
    lookup: dict[str, dict[str, int]] = {}

    def intern(field: str, value: str) -> int:
        table = lookup.setdefault(field, {})
        if value not in table:
            table[value] = len(table)
            dicts.setdefault(field, []).append(value)
        return table[value]

    name_buf = io.BytesIO()
    name_off = array("I", [0])
    ids = array("I")
    sku_exceptions: dict[str, str] = {}

    salt = array("H"); pack = array("H"); mfr = array("H")
    ck = array("H"); mk = array("H")
    form = array("B"); sched = array("B"); cat = array("B"); flags = array("B")
    price = array("I"); mrp = array("I")

    for i, r in enumerate(rows):
        name_buf.write(r["brand_name"].encode("utf-8"))
        name_off.append(name_buf.tell())

        sku = r["sku"]
        if sku.startswith("IMD-") and sku[4:].isdigit():
            ids.append(int(sku[4:]))
        else:
            ids.append(0)
            sku_exceptions[str(i)] = sku

        salt.append(intern("salt", r["composition_text"] or ""))
        pack.append(intern("pack", r["pack_label"] or ""))
        mfr.append(intern("mfr", r["mfr"]))
        ck.append(intern("ck", r["composition_key"] or ""))
        mk.append(intern("mk", r["molecule_key"] or ""))
        form.append(intern("form", (r["dosage_form"] or "").title()))
        sched.append(intern("sched", r["drug_schedule"] or "OTC"))
        cat.append(intern("cat", r["category"] or "General"))
        flags.append((1 if r["is_active"] else 0) | (2 if r["is_prescription_required"] else 0))
        price.append(int(r["selling_price_paise"] or 0))
        mrp.append(int(r["mrp_paise"] or 0))

    for field in ("salt", "pack", "mfr", "ck", "mk"):
        size = len(dicts[field])
        print(f"  dict {field:5} {size:>7,}")
        if size > 65535:
            raise SystemExit(f"{field} dictionary exceeds uint16 — widen the column")

    names = name_buf.getvalue()

    # Composition keys that cannot be parsed are excluded from substitution
    # entirely, exactly as the server does — never grouped on a guess.
    unparsed_ck = [i for i, k in enumerate(dicts["ck"]) if k.startswith("unparsed::")]

    columns = [
        ("nameOff", "u32", name_off),
        ("names", "u8", names),
        ("id", "u32", ids),
        ("salt", "u16", salt),
        ("pack", "u16", pack),
        ("mfr", "u16", mfr),
        ("ck", "u16", ck),
        ("mk", "u16", mk),
        ("form", "u8", form),
        ("sched", "u8", sched),
        ("cat", "u8", cat),
        ("flags", "u8", flags),
        ("price", "u32", price),
        ("mrp", "u32", mrp),
    ]

    blob = io.BytesIO()
    meta = {}
    for name, kind, data in columns:
        raw = data if isinstance(data, bytes) else data.tobytes()
        meta[name] = {"off": blob.tell(), "len": len(raw), "type": kind}
        blob.write(raw)
    body = blob.getvalue()

    header = json.dumps({
        "n": n,
        "dicts": {k: dicts[k] for k in ("salt", "pack", "mfr", "form", "sched", "cat")},
        "nCk": len(dicts["ck"]),
        "nMk": len(dicts["mk"]),
        "unparsedCk": unparsed_ck,
        "skuExceptions": sku_exceptions,
        "content": CONTENT,
        "cols": meta,
    }, separators=(",", ":")).encode("utf-8")

    container = struct.pack("<I", len(header)) + header + body
    packed = gzip.compress(container, 9)
    print(f"  raw {len(container)/1e6:.1f} MB -> gzip {len(packed)/1e6:.1f} MB "
          f"-> base64 {len(packed)*4/3/1e6:.1f} MB")
    return packed


# ---------------------------------------------------------------------------
# 2. The browser-side index runtime
# ---------------------------------------------------------------------------

RUNTIME = r"""
/* ===================== FULL CATALOGUE INDEX =============================
   254,000 products in one file. Everything but the brand name is a small
   integer into a dictionary; the columns are typed arrays inflated from a
   gzip blob. Search and substitution run off counting-sorted indexes built
   once at load, so nothing here scans a quarter of a million objects.
   ==================================================================== */
const CAT = {ready:false};

async function inflateIndex(){
  if(typeof DecompressionStream !== 'function')
    throw new Error('This browser cannot decompress the catalogue. Please use a current version of Chrome, Edge, Firefox or Safari.');
  const b64 = window.__NIROG_INDEX__;
  const bin = new Uint8Array(b64.length * 3 / 4 | 0);
  {  // decode base64 without blowing the call stack on a multi-megabyte string
    const s = atob(b64);
    for(let i = 0; i < s.length; i++) bin[i] = s.charCodeAt(i);
  }
  const ds = new DecompressionStream('gzip');
  const buf = await new Response(new Blob([bin]).stream().pipeThrough(ds)).arrayBuffer();
  return buf;
}

function countingSort(keys, nKeys, n){
  /* Returns [order, start] so rows sharing a key are contiguous:
     rows for key k are order[start[k] .. start[k+1]]. */
  const start = new Uint32Array(nKeys + 1);
  for(let i = 0; i < n; i++) start[keys[i] + 1]++;
  for(let k = 0; k < nKeys; k++) start[k + 1] += start[k];
  const cursor = start.slice(0, nKeys);
  const order = new Uint32Array(n);
  for(let i = 0; i < n; i++) order[cursor[keys[i]]++] = i;
  return [order, start];
}

async function initCatalog(){
  const buf = await inflateIndex();
  const bytes = new Uint8Array(buf);
  const hdrLen = new DataView(buf).getUint32(0, true);
  const H = JSON.parse(new TextDecoder().decode(bytes.subarray(4, 4 + hdrLen)));
  const base = 4 + hdrLen;
  const TYPES = {u8:Uint8Array, u16:Uint16Array, u32:Uint32Array};
  const col = (name) => {
    const c = H.cols[name];
    return new TYPES[c.type](buf.slice(base + c.off, base + c.off + c.len));
  };

  CAT.n = H.n;
  CAT.d = H.dicts;
  CAT.content = H.content;
  CAT.skuEx = H.skuExceptions;
  CAT.nameOff = col('nameOff');
  CAT.id = col('id');
  CAT.salt = col('salt'); CAT.pack = col('pack'); CAT.mfr = col('mfr');
  CAT.ck = col('ck'); CAT.mk = col('mk');
  CAT.form = col('form'); CAT.sched = col('sched'); CAT.cat = col('cat');
  CAT.flags = col('flags'); CAT.price = col('price'); CAT.mrp = col('mrp');

  const nameBytes = col('names');
  CAT.names = new TextDecoder().decode(nameBytes);
  CAT.namesLc = CAT.names.toLowerCase();

  /* Offsets above are byte offsets into UTF-8; the JS string is UTF-16, so
     they do not line up wherever a name contains a non-ASCII character.
     Rebuild character offsets once rather than decoding per row. */
  const charOff = new Uint32Array(CAT.n + 1);
  {
    let bi = 0, ci = 0, r = 0;
    charOff[0] = 0;
    const target = CAT.nameOff;
    while(r < CAT.n){
      const end = target[r + 1];
      while(bi < end){
        const b = nameBytes[bi];
        bi += b < 0x80 ? 1 : (b < 0xE0 ? 2 : (b < 0xF0 ? 3 : 4));
        ci += b < 0xF0 ? 1 : 2;   // astral chars are two UTF-16 units
      }
      charOff[++r] = ci;
    }
    CAT.charOff = charOff;
  }

  CAT.unparsedCk = new Set(H.unparsedCk);
  [CAT.ckOrder, CAT.ckStart] = countingSort(CAT.ck, H.nCk, CAT.n);
  [CAT.mkOrder, CAT.mkStart] = countingSort(CAT.mk, H.nMk, CAT.n);
  [CAT.saltOrder, CAT.saltStart] = countingSort(CAT.salt, CAT.d.salt.length, CAT.n);
  [CAT.catOrder, CAT.catStart] = countingSort(CAT.cat, CAT.d.cat.length, CAT.n);

  CAT.saltLc = CAT.d.salt.map(s => s.toLowerCase());
  CAT.rowBySku = null;   // built lazily, only if a sku lookup misses
  CAT.ready = true;
  return CAT;
}

function rowAtChar(pos){
  const a = CAT.charOff;
  let lo = 0, hi = CAT.n - 1;
  while(lo <= hi){
    const mid = (lo + hi) >> 1;
    if(a[mid] > pos) hi = mid - 1;
    else if(a[mid + 1] <= pos) lo = mid + 1;
    else return mid;
  }
  return -1;
}

function skuOf(r){
  const ex = CAT.skuEx[r];
  return ex !== undefined ? ex : 'IMD-' + CAT.id[r];
}

function rowOfSku(sku){
  if(!CAT.rowBySku){
    CAT.rowBySku = new Map();
    for(let r = 0; r < CAT.n; r++) CAT.rowBySku.set(skuOf(r), r);
  }
  const r = CAT.rowBySku.get(sku);
  return r === undefined ? -1 : r;
}

function contentFor(saltText){
  const low = saltText.toLowerCase();
  for(const key in CAT.content) if(low.indexOf(key) >= 0) return CAT.content[key];
  return null;
}

/* Hydrate one row into the shape every React component already expects. */
function hydrate(r, full){
  const name = CAT.names.slice(CAT.charOff[r], CAT.charOff[r + 1]);
  const salt = CAT.d.salt[CAT.salt[r]];
  const price = CAT.price[r] / 100, mrp = CAT.mrp[r] / 100;
  const o = {
    id: skuOf(r), name: name, salt: salt,
    form: CAT.d.form[CAT.form[r]],
    mfr: CAT.d.mfr[CAT.mfr[r]] || null,
    pack: CAT.d.pack[CAT.pack[r]],
    price: price, mrp: mrp,
    discount: mrp > 0 ? Math.round((mrp - price) * 100 / mrp) : 0,
    rx: !!(CAT.flags[r] & 2),
    schedule: CAT.d.sched[CAT.sched[r]],
    cat: CAT.d.cat[CAT.cat[r]],
    rating: null, nlem: false,
    discontinued: !(CAT.flags[r] & 1),
    _row: r,
  };
  if(!full) return o;
  const c = contentFor(salt);
  o.uses = c ? c.uses : [];
  o.how = c ? c.how : null;
  o.side = c ? c.side : [];
  o.directions = c ? c.directions : null;
  o.storage = c ? c.storage : null;
  o.content = {
    source: c ? 'Editorial content keyed to the molecule \u2014 not brand-specific, not clinically reviewed.'
              : 'No reviewed description is on file for this product. Catalogue data from indian_medicine_data.csv.',
    reviewed_by: null, reviewed_at: null,
  };
  o.ingredients = salt.split(/\s*\+\s*/).map(part => {
    const m = part.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
    return m ? {molecule: m[1].trim(), strength: m[2], unit: null}
             : {molecule: part.trim(), strength: null, unit: null};
  });
  return o;
}

/* --------------------------------- search ------------------------------ */
function searchCatalog(query, opts){
  opts = opts || {};
  const limit = opts.limit || 60;
  const catFilter = (opts.cat && opts.cat !== 'All') ? CAT.d.cat.indexOf(opts.cat) : -1;
  const q = (query || '').trim().toLowerCase();

  const seen = new Set();
  const hits = [];
  const push = (r, rank) => {
    if(seen.has(r)) return;
    if(catFilter >= 0 && CAT.cat[r] !== catFilter) return;
    seen.add(r);
    /* Discontinued packs stay searchable but sink below everything live —
       hiding them outright makes a real brand look like a typo. */
    hits.push([r, rank + ((CAT.flags[r] & 1) ? 0 : 10), CAT.price[r]]);
  };

  if(!q){
    // No query: show the chosen category, cheapest first, so the grid is never empty.
    if(catFilter >= 0){
      const s = CAT.catStart[catFilter], e = CAT.catStart[catFilter + 1];
      for(let i = s; i < e && hits.length < limit * 6; i++) push(CAT.catOrder[i], 4);
    } else {
      for(let r = 0; r < CAT.n && hits.length < limit * 6; r += 37) push(r, 4);
    }
    hits.sort((a, b) => a[2] - b[2]);
    return {rows: hits.slice(0, limit).map(h => h[0]), total: hits.length, truncated: true};
  }

  // 1. brand name — one native scan over the joined name string
  const scanCap = limit * 40;
  let i = CAT.namesLc.indexOf(q);
  while(i >= 0 && hits.length < scanCap){
    const r = rowAtChar(i);
    if(r >= 0){
      const a = CAT.charOff[r], b = CAT.charOff[r + 1];
      let rank;
      if(i === a && b - a === q.length) rank = 0;          // exact brand name
      else if(i === a) rank = 1;                            // brand starts with it
      else if(/[\s\-(/]/.test(CAT.namesLc[i - 1])) rank = 2;  // starts a word
      else rank = 3;                                       // mid-word
      push(r, rank);
    }
    i = CAT.namesLc.indexOf(q, i + 1);
  }

  // 2. composition — search the 11,823-entry salt dictionary, then expand
  if(hits.length < limit * 3){
    for(let s = 0; s < CAT.saltLc.length && hits.length < scanCap; s++){
      if(CAT.saltLc[s].indexOf(q) < 0) continue;
      const a = CAT.saltStart[s], b = CAT.saltStart[s + 1];
      for(let k = a; k < b && hits.length < scanCap; k++) push(CAT.saltOrder[k], 5);
    }
  }

  // 3. manufacturer, only if the query still looks unanswered
  if(hits.length < 8){
    for(let m = 0; m < CAT.d.mfr.length && hits.length < scanCap; m++){
      if(CAT.d.mfr[m].toLowerCase().indexOf(q) < 0) continue;
      for(let r = 0; r < CAT.n && hits.length < scanCap; r++)
        if(CAT.mfr[r] === m) push(r, 6);
    }
  }

  // Exact brand prefix first, then partial, then salt matches; cheapest within each.
  /* Within a tier prefer the shorter name — "Dolo 650" should beat
     "Dolopee Tablet" for the query "dolo" — then the cheaper pack. */
  const nameLen = (r) => CAT.charOff[r + 1] - CAT.charOff[r];
  hits.sort((a, b) => a[1] - b[1] || nameLen(a[0]) - nameLen(b[0]) || a[2] - b[2]);
  return {rows: hits.slice(0, limit).map(h => h[0]), total: hits.length,
          truncated: hits.length >= scanCap};
}

/* ------------------------------ substitution ---------------------------- */
function substitutesForRow(r){
  const ckId = CAT.ck[r], mkId = CAT.mk[r];
  const src = hydrate(r, true);
  if(CAT.unparsedCk.has(ckId))
    return {source: src, exact: [], related: [], best_saving: null, unparsed: true};

  const collect = (order, start, id, exclude) => {
    const out = [];
    for(let k = start[id]; k < start[id + 1]; k++){
      const row = order[k];
      if(row === r) continue;
      if(exclude && CAT.ck[row] === ckId) continue;
      if(!(CAT.flags[row] & 1)) continue;
      out.push(row);
    }
    return out;
  };

  /* Sort the row ids by price and cut to the display limit BEFORE hydrating.
     Some composition groups hold thousands of brands; building objects for all
     of them to then throw most away was the slowest thing on this screen. */
  const decorateSub = (rows, cap) => {
    rows.sort((a, b) => CAT.price[a] - CAT.price[b]);
    return rows.slice(0, cap).map(row => {
      const p = hydrate(row, false);
      const saving = Math.max(0, src.price - p.price);
      p.saving = +saving.toFixed(2);
      p.saving_percent = src.price ? Math.round(saving * 100 / src.price) : 0;
      return p;
    });
  };

  const exactRows = collect(CAT.ckOrder, CAT.ckStart, ckId, false);
  const relatedRows = collect(CAT.mkOrder, CAT.mkStart, mkId, true);
  const exact = decorateSub(exactRows, 10);
  const related = decorateSub(relatedRows, 6);
  const cheaper = exact.filter(p => p.saving > 0);
  return {source: src, exact: exact, related: related,
          exact_total: exactRows.length, related_total: relatedRows.length,
          best_saving: cheaper.length ? cheaper[0] : null, unparsed: false};
}
"""


# ---------------------------------------------------------------------------
# 3. Patches to the generated standalone
# ---------------------------------------------------------------------------

def patch(html: str, old: str, new: str, label: str) -> str:
    if html.count(old) != 1:
        raise SystemExit(f"patch anchor not unique ({html.count(old)}x): {label}")
    print(f"  patched {label}")
    return html.replace(old, new, 1)


SHIM_PRODUCTS = """  if(route === '/catalog/products'){
    const q = (params.get('q')||'').toLowerCase();
    const cat = params.get('category');
    let items = DB.products;
    if(q) items = items.filter(p =>
      (p.name+' '+p.salt+' '+(p.cat||'')).toLowerCase().indexOf(q) >= 0);
    if(cat && cat !== 'all') items = items.filter(p => p.cat === cat);
    return {items:items, page:1, size:items.length, total:items.length};
  }"""

SHIM_PRODUCTS_NEW = """  if(route === '/catalog/products'){
    const res = searchCatalog(params.get('q')||'', {
      cat: params.get('category') || 'All',
      limit: parseInt(params.get('size')||'60', 10),
    });
    return {items: res.rows.map(r => hydrate(r, false)), page:1,
            size: res.rows.length, total: res.total, truncated: res.truncated};
  }"""

SHIM_CATEGORIES = """  if(route === '/catalog/categories'){
    const counts = {};
    DB.products.forEach(p => { if(p.cat) counts[p.cat] = (counts[p.cat]||0)+1; });
    return {items: Object.keys(counts).sort().map(n => ({name:n, count:counts[n]}))};
  }"""

SHIM_CATEGORIES_NEW = """  if(route === '/catalog/categories'){
    return {items: CAT.d.cat.map((n, i) => ({
      name: n, count: CAT.catStart[i+1] - CAT.catStart[i]
    })).sort((a,b) => b.count - a.count)};
  }"""

SHIM_SUBS = """    const r = substitutesFor(decodeURIComponent(m[1]));
    if(!r) { const e = new Error('Not found'); e.status = 404; throw e; }"""

SHIM_SUBS_NEW = """    const row = rowOfSku(decodeURIComponent(m[1]));
    if(row < 0) { const e = new Error('Not found'); e.status = 404; throw e; }
    const r = substitutesForRow(row);"""

SHIM_DETAIL = """    const p = bySku[decodeURIComponent(m[1])];
    if(!p) { const e = new Error('Not found'); e.status = 404; throw e; }
    return p;"""

SHIM_DETAIL_NEW = """    const row = rowOfSku(decodeURIComponent(m[1]));
    if(row < 0) { const e = new Error('Not found'); e.status = 404; throw e; }
    return hydrate(row, true);"""

SHIM_CART_ADD = """    const p = bySku[body.sku];
    if(!p) { const e = new Error('Not found'); e.status = 404; throw e; }
    const qty = (OFFLINE_CART[body.sku]||0) + (body.quantity||1);"""

SHIM_CART_ADD_NEW = """    const row = rowOfSku(body.sku);
    if(row < 0) { const e = new Error('Not found'); e.status = 404; throw e; }
    const p = hydrate(row, false);
    const qty = (OFFLINE_CART[body.sku]||0) + (body.quantity||1);"""

BYSKU = """const bySku = {}; DB.products.forEach(p => { bySku[p.id] = p; });"""
BYSKU_NEW = """/* Products no longer live in DB.products — they are in the compressed
   catalogue index, reached through rowOfSku()/hydrate(). */"""

CART_PAYLOAD = """    const p = bySku[sku], qty = OFFLINE_CART[sku];"""
CART_PAYLOAD_NEW = """    const p = hydrate(rowOfSku(sku), false), qty = OFFLINE_CART[sku];"""

BOOT_FETCH = """  const [products, specs, doctors, labs, me] = await Promise.all([
    api('/catalog/products?size=48'),
    api('/specialties'),
    api('/doctors'),
    api('/lab-tests'),
    AUTH_TOKEN ? api('/me') : Promise.resolve({members:[]}),
  ]);

  MEDS = (products.items||[]).map(decorate);"""

BOOT_FETCH_NEW = """  await initCatalog();

  const [specs, doctors, labs, me] = await Promise.all([
    api('/specialties'),
    api('/doctors'),
    api('/lab-tests'),
    AUTH_TOKEN ? api('/me') : Promise.resolve({members:[]}),
  ]);

  /* MEDS is no longer the catalogue — it is only the opening grid. Searching
     goes through searchCatalog(), which covers every product in the index. */
  MEDS = searchCatalog('', {limit:48}).rows.map(r => decorate(hydrate(r, false)));
  CATALOG_SIZE = CAT.n;"""

PHARMACY_OLD_START = "function Pharmacy(){"


PHARMACY_NEW = r"""function Pharmacy(){
  const s=useStore();
  const [cat,setCat]=useState('All');
  const [sort,setSort]=useState('relevance');
  const [rxMode,setRxMode]=useState('all');
  const [res,setRes]=useState(()=>({rows:[],total:0}));
  const [busy,setBusy]=useState(false);
  const catCount=(c)=>{ const i=CAT.d.cat.indexOf(c); return i<0?0:CAT.catStart[i+1]-CAT.catStart[i]; };
  const cats=['All',...CAT.d.cat.slice().sort((a,b)=>catCount(b)-catCount(a))];

  /* Debounced so a fast typist does not run the scan on every keystroke. */
  useEffect(()=>{
    setBusy(true);
    const t=setTimeout(()=>{
      const r=searchCatalog(s.query, {cat:cat, limit:120});
      let rows=r.rows.map(x=>decorate(hydrate(x,false)));
      if(rxMode==='rx') rows=rows.filter(m=>m.rx);
      else if(rxMode==='otc') rows=rows.filter(m=>!m.rx);
      if(sort==='price') rows=rows.slice().sort((a,b)=>a.price-b.price);
      else if(sort==='price_desc') rows=rows.slice().sort((a,b)=>b.price-a.price);
      else if(sort==='name') rows=rows.slice().sort((a,b)=>a.name.localeCompare(b.name));
      setRes({rows:rows.slice(0,60), total:r.total, shown:Math.min(rows.length,60), truncated:r.truncated});
      setBusy(false);
    }, s.query ? 140 : 0);
    return ()=>clearTimeout(t);
  },[s.query,cat,sort,rxMode]);

  const list=res.rows;
  const q=(s.query||'').trim();
  return html`<div class="page-enter">
    <div class="row between center wrap" style=${{gap:12,marginBottom:6}}>
      <div><div class="eyebrow">Online Pharmacy</div><h1 class="h-xl" style=${{marginTop:6}}>Medicines & wellness</h1>
        <p class="sub" style=${{marginTop:6}}>Search all ${CAT.n.toLocaleString('en-IN')} medicines by brand, salt or manufacturer, and see every cheaper brand with the same composition.</p></div>
      <button class="btn btn-outline" onClick=${()=>s.setRxOpen(true)}><${Icon} name="upload" size=17/> Upload prescription</button>
    </div>

    <div class="card card-pad" style=${{background:'linear-gradient(110deg,var(--mint),var(--teal-50))',border:'1px solid var(--teal-100)',marginTop:16}}>
      <div class="row center between wrap" style=${{gap:14}}>
        <div class="row center" style=${{gap:13}}>
          <div class="qa-ic" style=${{background:'#fff',color:'var(--green)'}}><${Icon} name="percent" size=24/></div>
          <div><div style=${{fontWeight:800,fontSize:16,fontFamily:'var(--display)'}}>Same salt, lower price</div>
          <div class="sub">Open any medicine to see every brand with the identical composition, cheapest first.</div></div>
        </div>
        <${Tag} kind="green">${CAT.n.toLocaleString('en-IN')} products indexed</${Tag}>
      </div>
    </div>

    <div class="scroll-x" style=${{marginTop:18}}>
      ${cats.map(c=>html`<button key=${c} class=${'chip'+(cat===c?' on':'')} onClick=${()=>setCat(c)}>
        ${c}${c!=='All'?html` <span style=${{opacity:.6,fontWeight:600}}>${catCount(c).toLocaleString('en-IN')}</span>`:null}</button>`)}
    </div>

    <div class="row between center wrap" style=${{marginTop:16,gap:12}}>
      <div class="sub">
        ${busy?'Searching…'
          :q?`${res.total.toLocaleString('en-IN')}${res.truncated?'+':''} match${res.total===1?'':'es'} for “${q}”${res.shown<res.total?` · showing ${res.shown}`:''}`
            :`A sample of the catalogue — type to search all ${CAT.n.toLocaleString('en-IN')}`}
      </div>
      <div class="sortbar">
        <select value=${rxMode} onChange=${e=>setRxMode(e.target.value)} title="Prescription filter">
          <option value="all">All medicines</option>
          <option value="otc">Over-the-counter only</option>
          <option value="rx">Prescription only</option>
        </select>
        <select value=${sort} onChange=${e=>setSort(e.target.value)} title="Sort">
          <option value="relevance">Best match</option>
          <option value="price">Price: low to high</option>
          <option value="price_desc">Price: high to low</option>
          <option value="name">Name A-Z</option>
        </select>
        ${q?html`<button class="btn btn-ghost btn-sm" onClick=${()=>s.setQuery('')}>Clear</button>`:null}
      </div>
    </div>

    <div class="med-grid" style=${{marginTop:14}}>
      ${list.length?list.map(m=>html`<${MedCard} key=${m.id} m=${m}/>`):html`<div class="empty" style=${{gridColumn:'1/-1'}}>
        <div class="empty-ic"><${Icon} name="search" size=30/></div>
        <div style=${{fontWeight:700}}>${busy?'Searching…':'No medicines found'}</div>
        <p class="sub">${busy?'':'Try a brand name (Crocin), a salt (paracetamol) or a manufacturer (Cipla).'}</p></div>`}
    </div>
  </div>`;
}"""


def replace_function(html: str, start_marker: str, new_body: str, label: str) -> str:
    """Replace a top-level JS function by brace matching from its declaration."""
    i = html.index(start_marker)
    j = html.index("{", i)
    depth, k = 0, j
    while True:
        ch = html[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    print(f"  replaced {label} ({k - i + 1} chars)")
    return html[:i] + new_body + html[k + 1:]


SUB_COUNT_OLD = """        <p class="sub" style=${{margin:'6px 0 16px'}}>Same active ingredient at the same strength, verified against composition — not brand name.</p>"""

SUB_COUNT_NEW = """        <p class="sub" style=${{margin:'6px 0 16px'}}>Same active ingredient at the same strength, matched on composition — not brand name.${subs&&subs.exact_total>exact.length?html` <b>${subs.exact_total.toLocaleString('en-IN')}</b> brands share this composition; the ${exact.length} cheapest are shown.`:null}</p>"""


RX_MODAL = r"""
/* ===================== PRESCRIPTION UPLOAD =============================
   A real file picker with validation and a preview. It deliberately does NOT
   pretend to submit: offline there is no pharmacist, no storage and no order,
   and saying so is more useful than a fake success screen.
   ==================================================================== */
const RX_TYPES = ['image/jpeg','image/png','image/webp','image/heic','application/pdf'];
const RX_MAX_MB = 10;

function PrescriptionUpload({onClose}){
  const s=useStore();
  const [files,setFiles]=useState([]);
  const [err,setErr]=useState('');
  const [drag,setDrag]=useState(false);
  const inputRef=useRef(null);

  const accept=(list)=>{
    const picked=Array.from(list||[]);
    if(!picked.length) return;
    const ok=[]; let problem='';
    for(const f of picked){
      if(RX_TYPES.indexOf(f.type)<0 && !/\.(jpe?g|png|webp|heic|pdf)$/i.test(f.name)){
        problem=f.name+' is not a photo or PDF.'; continue;
      }
      if(f.size > RX_MAX_MB*1024*1024){
        problem=f.name+' is larger than '+RX_MAX_MB+' MB.'; continue;
      }
      ok.push({name:f.name, size:f.size, type:f.type,
               url: f.type.indexOf('image/')===0 ? URL.createObjectURL(f) : null});
    }
    setErr(problem);
    setFiles(prev=>prev.concat(ok).slice(0,5));
  };

  const remove=(i)=>setFiles(prev=>{
    const f=prev[i]; if(f && f.url) URL.revokeObjectURL(f.url);
    return prev.filter((_,k)=>k!==i);
  });

  useEffect(()=>()=>{ files.forEach(f=>f.url&&URL.revokeObjectURL(f.url)); },[]);

  const kb=(n)=> n>1048576 ? (n/1048576).toFixed(1)+' MB' : Math.max(1,Math.round(n/1024))+' KB';

  return html`<div class="modal">
    <div class="scrim" onClick=${onClose}/>
    <div class="modal-card" style=${{position:'relative',zIndex:1,padding:22,maxWidth:520}}>
      <div class="row between center" style=${{marginBottom:6}}>
        <h3 class="h-md">Upload prescription</h3>
        <button class="icon-btn" onClick=${onClose}><${Icon} name="x" size=18/></button>
      </div>
      <p class="sub" style=${{marginBottom:16}}>Schedule H and H1 medicines can only be dispensed against a valid prescription from a registered practitioner. Add a clear photo or a PDF.</p>

      <div class=${'rx-drop'+(drag?' on':'')}
        onClick=${()=>inputRef.current&&inputRef.current.click()}
        onDragOver=${e=>{e.preventDefault();setDrag(true);}}
        onDragLeave=${()=>setDrag(false)}
        onDrop=${e=>{e.preventDefault();setDrag(false);accept(e.dataTransfer.files);}}>
        <div class="qa-ic" style=${{background:'var(--teal-50)',color:'var(--teal-500)',margin:'0 auto 10px'}}>
          <${Icon} name="upload" size=24/></div>
        <div style=${{fontWeight:700}}>Tap to choose, or drag a file here</div>
        <div class="sub" style=${{fontSize:12,marginTop:4}}>JPG, PNG, HEIC or PDF · up to ${RX_MAX_MB} MB · max 5 files</div>
        <input ref=${inputRef} type="file" multiple accept="image/*,application/pdf"
          style=${{display:'none'}} onChange=${e=>{accept(e.target.files); e.target.value='';}}/>
      </div>

      ${err?html`<div class="sub" style=${{color:'var(--coral)',marginTop:10,fontSize:12.5}}>${err}</div>`:null}

      ${files.length?html`<div style=${{marginTop:14}}>
        ${files.map((f,i)=>html`<div key=${i} class="subst-row" style=${{marginBottom:8}}>
          ${f.url?html`<img src=${f.url} alt="" style=${{width:44,height:44,borderRadius:10,objectFit:'cover',flexShrink:0}}/>`
                 :html`<div class="subst-ic"><${Icon} name="doc" size=20/></div>`}
          <div style=${{flex:1,minWidth:0}}>
            <div style=${{fontWeight:700,fontSize:13.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${f.name}</div>
            <div class="sub" style=${{fontSize:12}}>${kb(f.size)}</div>
          </div>
          <button class="icon-btn" onClick=${()=>remove(i)}><${Icon} name="x" size=16/></button>
        </div>`)}
      </div>`:null}

      <div class="card card-pad" style=${{marginTop:16,background:'var(--amber-soft)',border:'1px solid #ffe2b0',padding:'12px 14px'}}>
        <div class="row" style=${{gap:10,alignItems:'flex-start'}}>
          <${Icon} name="info" size=18 style=${{color:'var(--amber)',flexShrink:0,marginTop:1}}/>
          <div class="sub" style=${{fontSize:12.3,lineHeight:1.6,color:'var(--ink-2)'}}>
            <b style=${{color:'var(--ink)'}}>This demo runs with no backend.</b> Your file stays in this browser tab and is never uploaded, stored or seen by anyone. In the full build it goes to a registered pharmacist, who verifies the prescriber's registration number and the patient details before any Schedule H medicine is dispatched.
          </div>
        </div>
      </div>

      <div class="row" style=${{gap:10,marginTop:16}}>
        <button class="btn btn-ghost" style=${{flex:1}} onClick=${onClose}>Cancel</button>
        <button class="btn btn-primary" style=${{flex:1}} disabled=${!files.length}
          onClick=${()=>{ s.toast(files.length+' file'+(files.length>1?'s':'')+' attached to this session \u2014 no pharmacist in the demo'); onClose(); }}>
          Attach ${files.length?'('+files.length+')':''}
        </button>
      </div>
    </div>
  </div>`;
}
"""

RX_CSS = """
.rx-drop{border:2px dashed var(--line);border-radius:var(--r-lg);padding:26px 18px;text-align:center;cursor:pointer;transition:.18s;background:var(--card)}
.rx-drop:hover,.rx-drop.on{border-color:var(--teal-500);background:var(--teal-50)}
.subst-row.tappable{cursor:pointer;transition:.16s}
.subst-row.tappable:hover{border-color:var(--teal-500);background:var(--teal-50);transform:translateY(-1px)}
.subst-row.tappable:focus-visible{outline:2px solid var(--teal-500);outline-offset:2px}
.linkish{background:none;border:none;padding:0;font:inherit;color:var(--teal-600,#0d7a6f);font-weight:700;text-decoration:underline;cursor:pointer}
"""


# --- prescription upload wiring -------------------------------------------
RX_STATE_OLD = """  const [selMed,setSelMed]=useState(null);"""
RX_STATE_NEW = """  const [selMed,setSelMed]=useState(null);
  const [rxOpen,setRxOpen]=useState(false);"""

RX_STORE_OLD = """    openMed:(m)=>{ setSelMed(m); scrollTop(); },"""
RX_STORE_NEW = """    openMed:(m)=>{ setSelMed(m); scrollTop(); },
    rxOpen, setRxOpen,"""

RX_BUTTON_OLD = """      <button class="btn btn-outline" onClick=${()=>s.toast('Upload a prescription to order Rx medicines')}><${Icon} name="upload" size=17/> Upload prescription</button>"""
RX_BUTTON_NEW = """      <button class="btn btn-outline" onClick=${()=>s.setRxOpen(true)}><${Icon} name="upload" size=17/> Upload prescription</button>"""

RX_MOUNT_OLD = """      ${labItem?html`<${LabModal} item=${labItem} close=${()=>setLabItem(null)}/>`:null}"""
RX_MOUNT_NEW = """      ${labItem?html`<${LabModal} item=${labItem} close=${()=>setLabItem(null)}/>`:null}
      ${rxOpen?html`<${PrescriptionUpload} onClose=${()=>setRxOpen(false)}/>`:null}"""

# The Rx notice on a prescription medicine gets its own upload button.
RX_NOTICE_OLD = "          ${m.rx?html`<b style=${{color:'var(--ink)'}}>Prescription required.</b> This medicine is sold only against a valid doctor's prescription. Upload one at checkout — our pharmacist will verify before dispatch. `:html`<b style=${{color:'var(--ink)'}}>Over-the-counter.</b> No prescription needed, but `}"
RX_NOTICE_NEW = '          ${m.rx?html`<b style=${{color:\'var(--ink)\'}}>Prescription required.</b> This medicine is sold only against a valid doctor\'s prescription. A pharmacist verifies it before dispatch. <button class="linkish" onClick=${()=>s.setRxOpen(true)}>Upload a prescription</button>. `:html`<b style=${{color:\'var(--ink)\'}}>Over-the-counter.</b> No prescription needed, but `}'

# Substitute rows become links to that product's own detail page.
SUB_ROW_OLD = """          return html`<div key=${sub.id} class=${'subst-row'+(isBest?' best':'')} style=${{marginBottom:10}}>
            <div class="subst-ic"><${Icon} name="pill" size=22/></div>"""
SUB_ROW_NEW = """          return html`<div key=${sub.id} class=${'subst-row tappable'+(isBest?' best':'')} style=${{marginBottom:10}}
            role="button" tabIndex=${0} title=${'View '+sub.name}
            onClick=${()=>s.openMed(decorate(sub))}
            onKeyDown=${e=>{ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); s.openMed(decorate(sub)); } }}>
            <div class="subst-ic"><${Icon} name="pill" size=22/></div>"""

SUB_ROW_TAIL_OLD = """              ${sub.saving>0?html`<div class="save-pill" style=${{marginTop:4}}>Save ${inr(sub.saving)}</div>`
                :html`<div class="sub" style=${{fontSize:11}}>${inr(sub.mrp)} MRP</div>`}
            </div>
          </div>`;"""
SUB_ROW_TAIL_NEW = """              ${sub.saving>0?html`<div class="save-pill" style=${{marginTop:4}}>Save ${inr(sub.saving)}</div>`
                :html`<div class="sub" style=${{fontSize:11}}>${inr(sub.mrp)} MRP</div>`}
            </div>
            <${Icon} name="chevR" size=17 style=${{color:'var(--faint)',flexShrink:0,marginLeft:2}}/>
          </div>`;"""

REL_ROW_OLD = r"""          ${related.map(sub=>html`<div key=${sub.id} class="subst-row" style=${{marginBottom:8,opacity:.9}}>
            <div class="subst-ic"><${Icon} name="pill" size=20/></div>
            <div style=${{flex:1,minWidth:0}}>
              <div style=${{fontWeight:700,fontSize:13.5}}>${sub.name}</div>
              <div class="sub" style=${{fontSize:12}}>${sub.mfr} \u00b7 ${sub.salt}</div>
            </div>
            <div style=${{fontWeight:800,fontFamily:'var(--display)',fontSize:15}}>${inr(sub.price)}</div>
          </div>`)}"""
REL_ROW_NEW = r"""          ${related.map(sub=>html`<div key=${sub.id} class="subst-row tappable" style=${{marginBottom:8}}
            role="button" tabIndex=${0} title=${'View '+sub.name}
            onClick=${()=>s.openMed(decorate(sub))}
            onKeyDown=${e=>{ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); s.openMed(decorate(sub)); } }}>
            <div class="subst-ic"><${Icon} name="pill" size=20/></div>
            <div style=${{flex:1,minWidth:0}}>
              <div style=${{fontWeight:700,fontSize:13.5}}>${sub.name}</div>
              <div class="sub" style=${{fontSize:12}}>${sub.mfr} \u00b7 ${sub.salt}</div>
            </div>
            <div style=${{fontWeight:800,fontFamily:'var(--display)',fontSize:15}}>${inr(sub.price)}</div>
            <${Icon} name="chevR" size=16 style=${{color:'var(--faint)',flexShrink:0,marginLeft:2}}/>
          </div>`)}"""

SUB_COUNT2_OLD = """<b>${subs.exact_total.toLocaleString('en-IN')}</b> brands share this composition; the ${exact.length} cheapest are shown."""
SUB_COUNT2_NEW = r"""<b>${subs.exact_total.toLocaleString('en-IN')}</b> brands share this composition \u2014 the ${exact.length} cheapest are shown. Tap any one to open it."""


SUGGEST_JS = r"""
/* ===================== SEARCH SUGGESTIONS ==============================
   Type-ahead over the same index the pharmacy page uses. Suggestions are
   grouped: the composition you are probably after first, then the brands.
   ==================================================================== */
function suggestFor(q, n){
  q = (q||'').trim().toLowerCase();
  if(q.length < 2) return {salts:[], brands:[]};

  /* Collect every dictionary match first, THEN rank. Capping the scan before
     sorting was picking whichever salts happened to be interned earliest —
     "para" returned Paracetamol (250mg) and never the far commoner 500mg. */
  const salts = [];
  for(let i = 0; i < CAT.saltLc.length; i++){
    const pos = CAT.saltLc[i].indexOf(q);
    if(pos < 0) continue;
    const count = CAT.saltStart[i+1] - CAT.saltStart[i];
    /* Prefix of the whole composition beats prefix of a later salt in a
       combination, which beats a match inside a word ("met" in Artemether). */
    const prev = pos > 0 ? CAT.saltLc[i][pos-1] : '';
    const rank = pos === 0 ? 0 : (/[\s+(]/.test(prev) ? 1 : 2);
    salts.push({id:i, text:CAT.d.salt[i], count:count, rank:rank});
    if(salts.length > 400) break;
  }
  salts.sort((a,b)=>a.rank-b.rank || b.count-a.count);

  const brands = searchCatalog(q, {limit:n||6}).rows.map(r=>hydrate(r,false));
  return {salts:salts.slice(0,3), brands:brands};
}

function SearchSuggest(){
  const s=useStore();
  const [sug,setSug]=useState({salts:[],brands:[]});
  const [open,setOpen]=useState(false);
  const [hi,setHi]=useState(-1);
  const boxRef=useRef(null);

  useEffect(()=>{
    if(!CAT.ready || !s.query || s.query.trim().length<2){ setSug({salts:[],brands:[]}); return; }
    const t=setTimeout(()=>{ setSug(suggestFor(s.query,6)); setHi(-1); },120);
    return ()=>clearTimeout(t);
  },[s.query]);

  useEffect(()=>{
    const away=(e)=>{ if(boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown',away);
    return ()=>document.removeEventListener('mousedown',away);
  },[]);

  const rows=[...sug.salts.map(x=>({kind:'salt',x})),...sug.brands.map(x=>({kind:'brand',x}))];
  const choose=(r)=>{
    setOpen(false);
    if(r.kind==='salt') s.searchAll(r.x.text);
    else s.openMed(decorate(r.x));
  };
  const onKey=(e)=>{
    if(!open || !rows.length) return;
    if(e.key==='ArrowDown'){ e.preventDefault(); setHi(h=>Math.min(h+1,rows.length-1)); }
    else if(e.key==='ArrowUp'){ e.preventDefault(); setHi(h=>Math.max(h-1,-1)); }
    else if(e.key==='Enter'){
      e.preventDefault();
      if(hi>=0) choose(rows[hi]);
      else { setOpen(false); s.searchAll(s.query); }
    }
    else if(e.key==='Escape'){ setOpen(false); }
  };

  return html`<div class="searchwrap" ref=${boxRef}>
    <div class="searchbar">
      <${Icon} name="search" size=19 style=${{color:'var(--faint)'}}/>
      <input placeholder="Search medicines, salts, brands…" value=${s.query} onKeyDown=${onKey}
        onFocus=${()=>setOpen(true)}
        onInput=${e=>{ s.setQuery(e.target.value); setOpen(true); }}/>
      ${s.query?html`<button class="sb-clear" title="Clear" onClick=${()=>{s.setQuery('');setOpen(false);}}>
        <${Icon} name="x" size=15/></button>`:null}
    </div>
    ${open && rows.length ? html`<div class="suggest">
      ${sug.salts.length?html`<div class="suggest-h">Composition</div>`:null}
      ${sug.salts.map((x,i)=>html`<div key=${'s'+x.id} class=${'suggest-row'+(hi===i?' on':'')}
          onMouseEnter=${()=>setHi(i)} onClick=${()=>choose({kind:'salt',x})}>
        <div class="suggest-ic"><${Icon} name="flask" size=16/></div>
        <div style=${{flex:1,minWidth:0}}>
          <div class="suggest-t">${x.text}</div>
          <div class="suggest-s">${x.count.toLocaleString('en-IN')} brand${x.count===1?'':'s'} available</div>
        </div>
        <${Icon} name="chevR" size=15 style=${{color:'var(--faint)'}}/>
      </div>`)}
      ${sug.brands.length?html`<div class="suggest-h">Medicines</div>`:null}
      ${sug.brands.map((x,i)=>{
        const k=sug.salts.length+i;
        return html`<div key=${x.id} class=${'suggest-row'+(hi===k?' on':'')}
            onMouseEnter=${()=>setHi(k)} onClick=${()=>choose({kind:'brand',x})}>
          <div class="suggest-ic"><${Icon} name="pill" size=16/></div>
          <div style=${{flex:1,minWidth:0}}>
            <div class="suggest-t">${x.name}${x.discontinued?html` <span class="disc-chip">Discontinued</span>`:null}</div>
            <div class="suggest-s">${x.salt}</div>
          </div>
          <div style=${{textAlign:'right',flexShrink:0}}>
            <div style=${{fontWeight:800,fontSize:13.5,fontFamily:'var(--display)'}}>${inr(x.price)}</div>
            <div class="suggest-s">${x.pack}</div>
          </div>
        </div>`;
      })}
      <div class="suggest-foot" onClick=${()=>{setOpen(false); s.searchAll(s.query);}}>
        See all results for “${s.query}” <${Icon} name="arrowR" size=14/>
      </div>
    </div>`:null}
  </div>`;
}
"""

SUGGEST_CSS = """
.searchwrap{flex:1;max-width:520px;position:relative}
.searchwrap .searchbar{max-width:none;width:100%}
.sb-clear{background:none;border:none;cursor:pointer;color:var(--faint);display:flex;padding:2px}
.sb-clear:hover{color:var(--ink)}
.suggest{position:absolute;top:calc(100% + 8px);left:0;right:0;background:var(--card);border:1px solid var(--line);border-radius:var(--r-lg);box-shadow:var(--shadow-lg);z-index:80;overflow:hidden;max-height:70vh;overflow-y:auto;animation:popIn .16s ease}
.suggest-h{font-size:11px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);padding:11px 16px 5px}
.suggest-row{display:flex;align-items:center;gap:11px;padding:9px 16px;cursor:pointer}
.suggest-row.on{background:var(--teal-50)}
.suggest-ic{width:32px;height:32px;border-radius:9px;background:var(--mint);color:var(--teal-600,#0d7a6f);display:grid;place-items:center;flex-shrink:0}
.suggest-t{font-weight:700;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.suggest-s{font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.suggest-foot{padding:11px 16px;border-top:1px solid var(--line);font-size:12.5px;font-weight:700;color:var(--teal-600,#0d7a6f);cursor:pointer;display:flex;align-items:center;gap:6px;justify-content:center}
.suggest-foot:hover{background:var(--teal-50)}
.disc-chip{font-size:10px;font-weight:700;background:#f1f1f1;color:#777;border-radius:5px;padding:1px 5px;vertical-align:middle}
.sortbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.sortbar select{border:1.5px solid var(--line);border-radius:var(--r-pill);padding:8px 13px;font:inherit;font-size:13px;background:var(--card);color:var(--ink);cursor:pointer}
.savebar{display:flex;align-items:center;gap:13px;background:linear-gradient(110deg,#eafaf1,#dff5ea);border:1px solid #b9e6cd;border-radius:var(--r-lg);padding:14px 16px;margin-top:16px;cursor:pointer;transition:.16s}
.savebar:hover{border-color:var(--green);transform:translateY(-1px)}
"""

TOPBAR_OLD = """    <div class="searchbar">
      <${Icon} name="search" size=19 style=${{color:'var(--faint)'}}/>
      <input placeholder="Search medicines, doctors, lab tests…" value=${s.query}
        onInput=${e=>{s.setQuery(e.target.value); if(e.target.value && s.route!=='pharmacy') s.go('pharmacy');}}/>
    </div>"""
TOPBAR_NEW = """    <${SearchSuggest}/>"""
SAVE_BANNER_OLD = """  const facts=["""
SAVE_BANNER_NEW = """  const saveBanner = cheapest && cheapest.saving>0 ? html`
    <div class="savebar" role="button" tabIndex=${0}
      onClick=${()=>s.openMed(decorate(cheapest))}
      onKeyDown=${e=>{ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); s.openMed(decorate(cheapest)); } }}>
      <div class="qa-ic" style=${{background:'#fff',color:'var(--green)',flexShrink:0}}><${Icon} name="percent" size=22/></div>
      <div style=${{flex:1,minWidth:0}}>
        <div style=${{fontWeight:800,fontSize:15,fontFamily:'var(--display)'}}>Save ${inr(cheapest.saving)} (${cheapest.saving_percent}%) on the same composition</div>
        <div class="sub" style=${{fontSize:12.5}}><b>${cheapest.name}</b> by ${cheapest.mfr} at ${inr(cheapest.price)} — identical salts and strength</div>
      </div>
      <${Icon} name="chevR" size=18 style=${{color:'var(--green)',flexShrink:0}}/>
    </div>` : null;
  const facts=["""
SAVE_MOUNT_OLD = """<button class="btn btn-coral" onClick=${()=>{s.setQty(m,Math.max(1,qty));s.setCartOpen(true);}}>Buy now</button>
        </div>"""
SAVE_MOUNT_NEW = """<button class="btn btn-coral" onClick=${()=>{s.setQty(m,Math.max(1,qty));s.setCartOpen(true);}}>Buy now</button>
        </div>
        ${saveBanner}"""


# --- data swap: bigger doctor directory + lab catalogue ---------------------
def swap_directory(data: dict) -> dict:
    doctors, specialties = build_doctors()
    data["doctors"] = doctors
    data["specialties"] = specialties
    data["labs"] = build_labs()
    print(f"  directory: {len(doctors)} doctors / {len(specialties)} specialties / {len(data['labs'])} lab tests")
    return data


# --- search: Enter and the "see all" footer must navigate -------------------
SEARCH_ENTER_OLD = """    else if(e.key==='Enter'){
      e.preventDefault();
      if(hi>=0) choose(rows[hi]);
      else { setOpen(false); s.searchAll(s.query); }
    }"""
SEARCH_ENTER_NEW = """    else if(e.key==='Enter'){
      e.preventDefault();
      if(hi>=0) choose(rows[hi]);
      else { setOpen(false); s.searchAll(s.query); }
    }"""

SEARCH_FOOT_OLD = """      <div class="suggest-foot" onClick=${()=>{setOpen(false); s.searchAll(s.query);}}>"""
SEARCH_FOOT_NEW = """      <div class="suggest-foot" onClick=${()=>{setOpen(false); s.searchAll(s.query);}}>"""

SEARCH_SALT_OLD = """    if(r.kind==='salt'){ s.setQuery(r.x.text); s.go('pharmacy'); s.setQuery(r.x.text); }"""
SEARCH_SALT_NEW = """    if(r.kind==='salt') s.searchAll(r.x.text);"""

# searchAll keeps the query across the route change; go() clears it otherwise.
STORE_OLD = """    openMed:(m)=>{ setSelMed(m); scrollTop(); },
    rxOpen, setRxOpen,"""
STORE_NEW = """    openMed:(m)=>{ setSelMed(m); scrollTop(); },
    /* Search from any tab: land on the pharmacy WITH the query intact.
       go() deliberately clears the query, so this cannot go through it. */
    searchAll:(q)=>{ setSelMed(null); setSelDoc(null); setConsult(null);
                     setRoute('pharmacy'); setQuery(q||''); scrollTop(); },
    docSpec, setDocSpec,
    upload, setUpload,
    rxOpen, setRxOpen,"""

STATE_OLD = """  const [rxOpen,setRxOpen]=useState(false);"""
STATE_NEW = """  const [rxOpen,setRxOpen]=useState(false);
  const [docSpec,setDocSpec]=useState(null);
  const [upload,setUpload]=useState(null);"""

# --- AI: use the grounded router --------------------------------------------
AI_ROUTE_OLD = """  if(route === '/ai/triage' && method === 'POST') return triage(body.message);"""
AI_ROUTE_NEW = """  if(route === '/ai/triage' && method === 'POST') return aiAnswer(body.message);"""

AI_ACTIONS_OLD = """      if(res.level==='CRISIS' || res.level==='EMERGENCY'){
        setEscalate({urgent:true, level:res.level, actions:res.actions||[]});
      } else if(res.actions && res.actions.length){
        setEscalate({actions:res.actions});
      }"""
AI_ACTIONS_NEW = """      if(res.level==='CRISIS' || res.level==='EMERGENCY'){
        setEscalate({urgent:true, level:res.level, actions:res.actions||[]});
      } else if(res.actions && res.actions.length){
        setEscalate({actions:res.actions});
      }"""

AI_CHIPS_OLD = """  const chips=['I have a fever and body ache','Cough and sore throat','Acidity after meals','Feeling anxious lately','My child has a fever'];"""
AI_CHIPS_NEW = """  const chips=['What is in Zerodol-P?','Cheaper substitute for Telma 40',
    'What does HbA1c measure?','Which doctor for kidney stones?',
    'Do I need to fast for a lipid profile?','Is Augmentin a prescription medicine?'];"""

# --- upload wiring ----------------------------------------------------------
UPLOAD_MOUNT_OLD = """      ${rxOpen?html`<${PrescriptionUpload} onClose=${()=>setRxOpen(false)}/>`:null}"""
UPLOAD_MOUNT_NEW = """      ${rxOpen?html`<${FileUpload} kind="prescription" onClose=${()=>setRxOpen(false)}/>`:null}
      ${upload?html`<${FileUpload} kind=${upload.kind} onClose=${()=>setUpload(null)} onDone=${upload.onDone}/>`:null}"""

# Every remaining upload dead-end becomes a real picker.
UP_RECORDS_OLD = """s.toast('Upload \u2014 AI will read & categorise it')"""
UP_RECORDS_NEW = """s.setUpload({kind:'record'})"""
UP_LABREPORT_OLD = """s.toast('Attach a report')"""
UP_LABREPORT_NEW = """s.setUpload({kind:'lab_report'})"""
UP_CLAIM_OLD = """s.toast('Start a cashless claim')"""
UP_CLAIM_NEW = """s.setUpload({kind:'claim'})"""

# --- doctors: route the AI's specialty pick straight into the filter --------
AI_NAV_OLD = """      if(a.type==='navigate') return s.go(a.route);"""
AI_NAV_NEW = """      if(a.type==='open_med'){ const r=rowOfSku(a.sku); if(r>=0) return s.openMed(decorate(hydrate(r,false))); }
      if(a.type==='navigate'){ if(a.specId) s.setDocSpec(a.specId); return s.go(a.route); }"""


ESC_OLD = '          ${escalate&&escalate.urgent?html`<div class="card" style=${{marginTop:10,padding:16,border:\'1.5px solid var(--coral)\',background:\'var(--coral-soft)\'}}>\n            <div class="row center" style=${{gap:9,marginBottom:9}}><${Icon} name="ambulance" size=20 style=${{color:\'var(--coral-dark)\'}}/>\n              <b style=${{color:\'var(--coral-dark)\'}}>This may need urgent care</b></div>\n            <button class="btn btn-coral btn-block" onClick=${()=>s.go(\'emergency\')}><${Icon} name="ambulance" size=17/> Go to Emergency / SOS</button>\n          </div>`:escalate&&escalate.spec?html`<div class="card" style=${{marginTop:10,padding:16,border:\'1.5px solid var(--teal-200)\'}}>\n            <div class="sub" style=${{marginBottom:10}}>Want a professional opinion? Connect with a verified ${escalate.spec} now.</div>\n            <button class="btn btn-primary btn-block" onClick=${()=>s.go(\'doctors\')}><${Icon} name="video" size=17/> Consult a ${escalate.spec}</button>\n          </div>`:null}'
ESC_NEW = '          ${escalate&&escalate.actions&&escalate.actions.length?html`<div class="card" style=${{marginTop:10,padding:16,\n              border:\'1.5px solid \'+(escalate.urgent?\'var(--coral)\':\'var(--teal-200)\'),\n              background:escalate.urgent?\'var(--coral-soft)\':\'var(--card)\'}}>\n            ${escalate.urgent?html`<div class="row center" style=${{gap:9,marginBottom:10}}>\n              <${Icon} name="ambulance" size=20 style=${{color:\'var(--coral-dark)\'}}/>\n              <b style=${{color:\'var(--coral-dark)\'}}>${escalate.level===\'CRISIS\'?\'Please talk to someone now\':\'This may need urgent care\'}</b></div>`:null}\n            <div style=${{display:\'flex\',flexDirection:\'column\',gap:8}}>\n              ${escalate.actions.map((a,i)=>html`<button key=${i}\n                class=${\'btn btn-block \'+(a.primary?(escalate.urgent?\'btn-coral\':\'btn-primary\'):\'btn-outline\')}\n                onClick=${()=>{\n                  if(a.type===\'call\'||a.type===\'helpline\'){ window.location.href=\'tel:\'+a.value; s.toast(\'Dialling \'+a.value); return; }\n                  if(a.type===\'open_med\'){ const r=rowOfSku(a.sku); if(r>=0) return s.openMed(decorate(hydrate(r,false))); return; }\n                  if(a.type===\'navigate\'){ if(a.specId) s.setDocSpec(a.specId); return s.go(a.route); }\n                  if(a.type===\'consult\'){ if(a.specialty) s.setDocSpec(a.specialty); return s.go(\'doctors\'); }\n                  s.toast(a.label);\n                }}>\n                <${Icon} name=${a.type===\'call\'||a.type===\'helpline\'?\'phone\':(a.type===\'open_med\'?\'pill\':(a.type===\'consult\'?\'video\':\'arrowR\'))} size=16/>\n                ${a.label}${a.value?\' \\u00b7 \'+a.value:\'\'}\n              </button>`)}\n            </div>\n          </div>`:null}'

def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"missing {SRC} — run build_standalone.py first")

    print("building catalogue index")
    packed = build_index()
    b64 = base64.b64encode(packed).decode("ascii")

    html = SRC.read_text(encoding="utf-8")

    # Strip the product array out of the embedded data — it is superseded.
    marker = "window.__NIROG_DATA__"
    i = html.index(marker)
    j = html.index("=", i) + 1
    decoder_start = j
    while html[decoder_start] in " \n\r\t":
        decoder_start += 1
    depth, k = 0, decoder_start
    while True:
        ch = html[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    data = json.loads(html[decoder_start:k + 1])
    kept = len(data.pop("products", []))
    data = swap_directory(data)
    print(f"  dropped {kept:,} inline products from __NIROG_DATA__")
    html = (html[:decoder_start]
            + json.dumps(data, separators=(",", ":"))
            + html[k + 1:])

    # Inject the index payload and runtime just before the shim.
    anchor = "/* ====================== OFFLINE MODE"
    payload = (f'window.__NIROG_INDEX__ = "{b64}";\n'
               f'let CATALOG_SIZE = 0;\n{RUNTIME}\n{anchor}')
    html = patch(html, anchor, payload, "index payload + runtime")

    html = patch(html, BYSKU, BYSKU_NEW, "bySku removal")
    html = patch(html, SHIM_PRODUCTS, SHIM_PRODUCTS_NEW, "/catalog/products")
    html = patch(html, SHIM_CATEGORIES, SHIM_CATEGORIES_NEW, "/catalog/categories")
    html = patch(html, SHIM_SUBS, SHIM_SUBS_NEW, "/substitutes")
    html = patch(html, SHIM_DETAIL, SHIM_DETAIL_NEW, "product detail")
    html = patch(html, SHIM_CART_ADD, SHIM_CART_ADD_NEW, "cart add")
    html = patch(html, CART_PAYLOAD, CART_PAYLOAD_NEW, "cart payload")
    html = patch(html, BOOT_FETCH, BOOT_FETCH_NEW, "boot()")
    html = replace_function(html, PHARMACY_OLD_START, PHARMACY_NEW, "Pharmacy()")

    # The old in-browser substitution helper is dead code now.
    html = patch(html, SUB_COUNT_OLD, SUB_COUNT_NEW, "substitute count")
    html = patch(html, SAVE_BANNER_OLD, SAVE_BANNER_NEW, "savings banner")
    html = patch(html, SAVE_MOUNT_OLD, SAVE_MOUNT_NEW, "savings banner mount")
    html = patch(html, SUB_COUNT2_OLD, SUB_COUNT2_NEW, "substitute count wording")
    html = patch(html, SUB_ROW_OLD, SUB_ROW_NEW, "substitute row clickable")
    html = patch(html, SUB_ROW_TAIL_OLD, SUB_ROW_TAIL_NEW, "substitute row chevron")
    html = patch(html, REL_ROW_OLD, REL_ROW_NEW, "related row clickable")
    html = patch(html, RX_STATE_OLD, RX_STATE_NEW, "rx state")
    html = patch(html, RX_STORE_OLD, RX_STORE_NEW, "rx store keys")
    html = patch(html, RX_NOTICE_OLD, RX_NOTICE_NEW, "rx notice link")
    html = patch(html, RX_MOUNT_OLD, RX_MOUNT_NEW, "rx modal mount")
    html = patch(html, "</style>", RX_CSS + SUGGEST_CSS + DOCTORS_CSS + "</style>", "styles")
    html = patch(html, STATE_OLD, STATE_NEW, "app state")
    html = patch(html, STORE_OLD, STORE_NEW, "store: searchAll/docSpec/upload")
    html = patch(html, AI_ROUTE_OLD, AI_ROUTE_NEW, "AI route")
    html = patch(html, AI_CHIPS_OLD, AI_CHIPS_NEW, "AI starter chips")
    html = patch(html, UPLOAD_MOUNT_OLD, UPLOAD_MOUNT_NEW, "upload mounts")
    html = patch(html, UP_RECORDS_OLD, UP_RECORDS_NEW, "records upload")
    html = patch(html, UP_LABREPORT_OLD, UP_LABREPORT_NEW, "lab report upload")
    html = patch(html, UP_CLAIM_OLD, UP_CLAIM_NEW, "insurance claim upload")
    html = patch(html, ESC_OLD, ESC_NEW, "AI action buttons")
    html = replace_function(html, "function Doctors(){", DOCTORS_JS, "Doctors()")
    html = patch(html, "function Topbar(){", AI_JS + "\n" + UPLOAD_JS + "\nfunction Topbar(){", "AI + upload components")
    html = patch(html, TOPBAR_OLD, TOPBAR_NEW, "topbar search")
    html = patch(html, "function Topbar(){", SUGGEST_JS + "\nfunction Topbar(){", "SearchSuggest component")
    html = replace_function(html, "function substitutesFor(sku){",
                            "/* superseded by substitutesForRow() */", "substitutesFor()")

    OUT.write_text(html, encoding="utf-8")
    print(f"\nwrote {OUT}  {OUT.stat().st_size/1e6:.1f} MB")


if __name__ == "__main__":
    main()
