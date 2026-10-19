"""Rewire the Nirog prototype from hard-coded mock data to the live API.

The prototype's components are good and are kept as-is. What changes:

  1. A small API client is injected.
  2. The module-level mock arrays become mutable stores filled at boot.
  3. MedDetail fetches real detail + substitutes instead of reading `m.subs`.
  4. The AI assistant calls /api/ai/triage and honours EMERGENCY / CRISIS
     escalations instead of replying from a canned script.
  5. Cart writes go to the server so prescription rules are enforced
     server-side, not just in the UI.

Run: python build_frontend.py <source.html> <output.html>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

API_CLIENT = r"""
/* ============================== API CLIENT ==============================
   All data now comes from the Nirog backend. Nothing clinical is hard-coded
   in the browser any more.
   ====================================================================== */
const API_BASE = (window.NIROG_API_BASE || 'http://127.0.0.1:5000') + '/api';
let AUTH_TOKEN = null;

async function api(path, options={}){
  const headers = Object.assign({}, options.headers || {});
  if(!(options.body instanceof FormData)) headers['Content-Type']='application/json';
  if(AUTH_TOKEN) headers['Authorization'] = 'Bearer ' + AUTH_TOKEN;
  const res = await fetch(API_BASE + path, Object.assign({}, options, {
    headers,
    body: options.body instanceof FormData ? options.body
        : (options.body ? JSON.stringify(options.body) : undefined),
  }));
  let payload = null;
  try { payload = await res.json(); } catch(e) { payload = null; }
  if(!res.ok){
    const err = new Error((payload && payload.message) || (payload && payload.error) || ('HTTP '+res.status));
    err.status = res.status; err.code = payload && payload.error; err.payload = payload;
    throw err;
  }
  return payload;
}

/* Accent colours are presentation only, so they stay client-side and are
   assigned deterministically rather than stored per product. */
const ACCENTS = ['teal','coral','blue','green','rose','violet','amber'];
const accentFor = (key='') => {
  let h = 0; for(let i=0;i<key.length;i++) h = (h*31 + key.charCodeAt(i)) >>> 0;
  return ACCENTS[h % ACCENTS.length];
};

/* Mutable stores, filled by boot(). Components read these exactly as they
   read the old constants. */
let MEDS=[], SPECS=[], DOCTORS=[], LABS=[], PACKAGES=[], RECORDS=[],
    TRENDS={}, FAMILY=[], REMINDERS=[], ORDERS=[];

const OFFERS = [
  {t:'Flat 25% off medicines', d:'On your first order above \u20b9499', code:'NIROG25', bg:'linear-gradient(120deg,#0e7c6b,#13a08a)'},
  {t:'Free doctor consult', d:'First video consult on us', code:'FREEDOC', bg:'linear-gradient(120deg,#ff6b5a,#f24e3b)'},
  {t:'Full body test \u20b91499', d:'92 parameters \u00b7 Home sample', code:'FULLBODY', bg:'linear-gradient(120deg,#7c5cff,#5a3ff0)'},
];

const decorate = (p) => Object.assign({}, p, {color: accentFor(p.id||p.name||'')});

const TREND_META = {
  hba1c:{label:'HbA1c', good:'<5.7', color:'amber', dir:'down'},
  bp_sys:{label:'Blood Pressure (Systolic)', good:'<120', color:'rose', dir:'down'},
  weight:{label:'Weight', good:'68\u201372', color:'teal', dir:'down'},
};

async function boot(){
  /* Demo credentials. Replace with the real OTP flow before go-live. */
  try{
    const auth = await api('/auth/login', {method:'POST',
      body:{mobile:'9825000000', password:'nirog-demo-2026'}});
    AUTH_TOKEN = auth.token;
  }catch(e){ console.warn('demo login unavailable', e.message); }

  const [products, specs, doctors, labs, me] = await Promise.all([
    api('/catalog/products?size=48'),
    api('/specialties'),
    api('/doctors'),
    api('/lab-tests'),
    AUTH_TOKEN ? api('/me') : Promise.resolve({members:[]}),
  ]);

  MEDS = (products.items||[]).map(decorate);
  SPECS = (specs.items||[]).map(s=>Object.assign({}, s, {color: s.color||accentFor(s.id)}));
  DOCTORS = (doctors.items||[]).map(decorate);

  const allLabs = (labs.items||[]).map(decorate);
  LABS = allLabs.filter(l=>!l.package);
  PACKAGES = allLabs.filter(l=>l.package).map(p=>Object.assign({},p,{tests:p.params}));

  FAMILY = (me.members||[]).map(m=>Object.assign({}, m, {color: accentFor(m.id)}));

  const selfMember = (me.members||[]).find(m=>m.is_self);
  if(selfMember && AUTH_TOKEN){
    const numericId = String(selfMember.id).replace(/^f/,'');
    const [records, vitals, reminders, orders] = await Promise.all([
      api('/members/'+numericId+'/records').catch(()=>({items:[]})),
      api('/members/'+numericId+'/vitals').catch(()=>({series:{}})),
      api('/members/'+numericId+'/reminders').catch(()=>({items:[]})),
      api('/orders').catch(()=>({items:[]})),
    ]);
    RECORDS = (records.items||[]).map(decorate);
    REMINDERS = (reminders.items||[]).map(r=>Object.assign({}, r, {color: accentFor(String(r.id))}));
    ORDERS = (orders.items||[]).map(o=>({
      id:o.id, items:(o.items[0]?o.items[0].name:'Order') + (o.items.length>1?' + '+(o.items.length-1)+' more':''),
      status:o.status.replace(/_/g,' ').toLowerCase().replace(/^./,c=>c.toUpperCase()),
      eta:'', prog: o.status==='DELIVERED'?100:(o.status==='OUT_FOR_DELIVERY'?75:35),
      color: accentFor(o.id),
    }));
    TRENDS = {};
    Object.keys(vitals.series||{}).forEach(key=>{
      const meta = TREND_META[key] || {label:key, good:'', color:'teal', dir:'down'};
      const series = vitals.series[key];
      TRENDS[key] = Object.assign({}, meta, {unit: series.unit, data: series.data});
    });
  }
}
"""

MED_DETAIL_LOADER = r"""
/* Fetches full detail + server-computed substitutes for one product. */
function useMedDetail(sku){
  const [state,setState]=useState({loading:true, detail:null, subs:null, error:null});
  useEffect(()=>{
    let live=true;
    setState({loading:true, detail:null, subs:null, error:null});
    Promise.all([
      api('/catalog/products/'+encodeURIComponent(sku)),
      api('/catalog/products/'+encodeURIComponent(sku)+'/substitutes'),
    ]).then(([detail,subs])=>{ if(live) setState({loading:false, detail, subs, error:null}); })
      .catch(err=>{ if(live) setState({loading:false, detail:null, subs:null, error:err.message}); });
    return ()=>{ live=false; };
  },[sku]);
  return state;
}
"""


def patch(source: str) -> str:
    out = source

    # 1. Inject the API client just after the htm binding.
    anchor = "const $ = (id)=>document.getElementById(id);"
    if anchor not in out:
        raise SystemExit("anchor for API client not found")
    out = out.replace(anchor, anchor + "\n" + API_CLIENT + MED_DETAIL_LOADER, 1)

    # 2. Remove the mock data block (MEDS .. ORDERS) wholesale.
    start = out.index("const MEDS = [")
    end = out.index("/* ============================== SHARED COMPONENTS")
    removed = out[start:end]
    out = out[:start] + "/* Mock data removed — all data now served by the API. */\n\n" + out[end:]
    print(f"  removed {removed.count(chr(10))} lines of mock data")

    # 3. MedDetail: fetch real data instead of reading m.subs.
    old_head = """function MedDetail({m}){
  const s=useStore();
  const qty=s.cart[m.id]?.qty||0;
  const cheapest=[...m.subs].sort((a,b)=>a.price-b.price)[0];
  const facts=["""
    new_head = """function MedDetail({m}){
  const s=useStore();
  const qty=s.cart[m.id]?.qty||0;
  const {loading,detail,subs,error}=useMedDetail(m.id);
  if(loading) return html`<div class="page-enter"><div class="card card-pad">Loading medicine information\u2026</div></div>`;
  if(error) return html`<div class="page-enter"><div class="card card-pad">
    <h3 class="h-md">Couldn't load this medicine</h3>
    <p class="sub" style=${{marginTop:8}}>${error}</p>
    <button class="btn btn-ghost" style=${{marginTop:14}} onClick=${()=>s.go('pharmacy')}>Back to pharmacy</button></div></div>`;
  m = Object.assign({}, m, detail, {color:m.color});
  /* Only EXACT-tier matches are offered as a swap. RELATED items differ in
     strength or form and are shown separately, never as one-tap substitutes. */
  const exact = (subs && subs.exact) || [];
  const related = (subs && subs.related) || [];
  const cheapest = (subs && subs.best_saving) || null;
  const facts=["""
    if old_head not in out:
        raise SystemExit("MedDetail head not found")
    out = out.replace(old_head, new_head, 1)

    # 4. Replace the substitutes rendering block.
    subs_start = out.index('<div class="card card-pad" style=${{background:\'linear-gradient(160deg,#fff,var(--mint-2))\'}}>')
    subs_end = out.index("    </div>\n\n    <div class=\"card card-pad\" style=${{marginTop:18,background:'var(--amber-soft)'", subs_start)
    new_subs = r"""<div class="card card-pad" style=${{background:'linear-gradient(160deg,#fff,var(--mint-2))'}}>
        <div class="row between center"><h3 class="h-md">Cheaper substitutes</h3><${Tag} kind="green">Same salt</${Tag}></div>
        <p class="sub" style=${{margin:'6px 0 16px'}}>Same active ingredient at the same strength, verified against composition — not brand name.</p>
        ${exact.length===0?html`<p class="sub">No same-salt substitute is listed for this pack right now.</p>`:null}
        ${exact.map((sub,i)=>{
          const isBest = cheapest && sub.id===cheapest.id && sub.saving>0;
          return html`<div key=${sub.id} class=${'subst-row'+(isBest?' best':'')} style=${{marginBottom:10}}>
            <div class="subst-ic"><${Icon} name="pill" size=22/></div>
            <div style=${{flex:1,minWidth:0}}>
              <div class="row center" style=${{gap:7}}><span style=${{fontWeight:700,fontSize:14}}>${sub.name}</span>
                ${isBest?html`<${Tag} kind="green">Best price</${Tag}>`:null}
                ${sub.rx?html`<${Tag} kind="coral">Rx</${Tag}>`:null}</div>
              <div class="sub" style=${{fontSize:12}}>${sub.mfr} \u00b7 ${sub.salt}</div>
            </div>
            <div style=${{textAlign:'right'}}>
              <div style=${{fontWeight:800,fontFamily:'var(--display)',fontSize:16}}>${inr(sub.price)}</div>
              ${sub.saving>0?html`<div class="save-pill" style=${{marginTop:4}}>Save ${inr(sub.saving)}</div>`
                :html`<div class="sub" style=${{fontSize:11}}>${inr(sub.mrp)} MRP</div>`}
            </div>
          </div>`;
        })}
        ${cheapest && cheapest.saving>0 ? html`<button class="btn btn-ghost btn-block" style=${{marginTop:6}}
          onClick=${()=>s.addToCart(cheapest)}>Switch to ${cheapest.name} & save ${inr(cheapest.saving)}</button>` : null}
        ${related.length?html`<div style=${{marginTop:18,paddingTop:16,borderTop:'1px solid var(--line)'}}>
          <div class="row between center"><h4 style=${{fontFamily:'var(--display)',fontSize:14,fontWeight:700}}>Related packs</h4>
            <${Tag} kind="amber">Different strength</${Tag}></div>
          <p class="sub" style=${{margin:'6px 0 12px',fontSize:12}}>These contain the same ingredient at a different strength or in a different form. They are <b>not</b> a like-for-like swap \u2014 ask your doctor or pharmacist before switching.</p>
          ${related.map(sub=>html`<div key=${sub.id} class="subst-row" style=${{marginBottom:8,opacity:.9}}>
            <div class="subst-ic"><${Icon} name="pill" size=20/></div>
            <div style=${{flex:1,minWidth:0}}>
              <div style=${{fontWeight:700,fontSize:13.5}}>${sub.name}</div>
              <div class="sub" style=${{fontSize:12}}>${sub.mfr} \u00b7 ${sub.salt}</div>
            </div>
            <div style=${{fontWeight:800,fontFamily:'var(--display)',fontSize:15}}>${inr(sub.price)}</div>
          </div>`)}
        </div>`:null}
      </div>
"""
    out = out[:subs_start] + new_subs + out[subs_end:]

    # 5. Add a server-backed addToCart to the store, alongside the local one.
    old_store = """    setQty:(m,q)=>{ setCart(c=>{ const n={...c}; if(q<=0) delete n[m.id]; else n[m.id]=q; return n; }); if(q>0 && !cart[m.id]) toast(m.name+' added to cart'); },"""
    new_store = """    setQty:(m,q)=>{ setCart(c=>{ const n={...c}; if(q<=0) delete n[m.id]; else n[m.id]=q; return n; }); if(q>0 && !cart[m.id]) toast(m.name+' added to cart'); },
    /* Server-backed add. Prescription and quantity rules are enforced by the
       API, so a rejected item is surfaced rather than silently allowed. */
    addToCart: async (m,q=1)=>{
      try{
        await api('/cart/items',{method:'POST',body:{sku:m.id,quantity:q}});
        setCart(c=>({...c,[m.id]:(c[m.id]||0)+q}));
        toast(m.name+' added to cart');
      }catch(err){
        toast(err.message || 'Could not add this item');
      }
    },"""
    if old_store not in out:
        raise SystemExit("store setQty not found")
    out = out.replace(old_store, new_store, 1)

    # 6. Replace the canned symptom engine with the server triage endpoint.
    #    The prototype's version named medicines and doses in its replies,
    #    which the backend explicitly forbids. Client-side triage is removed
    #    entirely rather than trimmed, so there is one screening path only.
    engine_start = out.index("  const engine=(userText)=>{")
    reply_end = out.index("  const send=(preset)=>{", engine_start)
    new_engine = r"""  /* All symptom screening happens server-side. The client renders whatever
     the API returns and never composes health advice of its own. */
  const reply = async (userText)=>{
    setTyping(true);
    try{
      const res = await api('/ai/triage', {method:'POST', body:{message:userText}});
      setTyping(false);
      setMsgs(m=>[...m,{who:'them', t:res.message}]);
      if(res.level==='CRISIS' || res.level==='EMERGENCY'){
        setEscalate({urgent:true, level:res.level, actions:res.actions||[]});
      } else if(res.actions && res.actions.length){
        setEscalate({actions:res.actions});
      }
    }catch(err){
      setTyping(false);
      setMsgs(m=>[...m,{who:'them', t:"I couldn't reach the health service just now. If this feels urgent, please call 108 or see a doctor rather than waiting."}]);
    }
  };
"""
    out = out[:engine_start] + new_engine + out[reply_end:]

    # 7. Boot gate in App().
    old_app = """function App(){
  const [route,setRoute]=useState('home');"""
    new_app = """function App(){
  const [booted,setBooted]=useState(false);
  const [bootError,setBootError]=useState(null);
  useEffect(()=>{ boot().then(()=>setBooted(true)).catch(e=>setBootError(e.message)); },[]);
  const [route,setRoute]=useState('home');"""
    out = out.replace(old_app, new_app, 1)

    old_render = """  let screen;
  if(consult) screen="""
    new_render = """  if(bootError) return html`<div style=${{padding:'60px 24px',maxWidth:560,margin:'0 auto',fontFamily:'var(--body)'}}>
    <h1 class="h-lg">Can't reach the Nirog API</h1>
    <p class="sub" style=${{marginTop:10,lineHeight:1.6}}>${bootError}</p>
    <p class="sub" style=${{marginTop:14,lineHeight:1.6}}>Start the backend with <code>python run.py</code> in <code>backend/</code>, then reload. If it is running on another host, set <code>window.NIROG_API_BASE</code> before this script.</p>
  </div>`;
  if(!booted) return html`<div style=${{display:'grid',placeItems:'center',minHeight:'100vh',fontFamily:'var(--body)',color:'var(--muted)'}}>Loading Nirog\u2026</div>`;

  let screen;
  if(consult) screen="""
    out = out.replace(old_render, new_render, 1)

    # config.js (written by frontend/write_config.py on Netlify) sets
    # window.NIROG_API_BASE, so it must load before the app script.
    tag = '<script src="config.js"></script>'
    if tag not in out:
        if "\n<script>\n" not in out:
            raise SystemExit("anchor for config.js script tag not found")
        out = out.replace("\n<script>\n", f"\n{tag}\n<script>\n", 1)

    return out


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "nirog.html")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "nirog.api.html")
    print(f"patching {src} -> {dst}")
    dst.write_text(patch(src.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"  wrote {len(dst.read_text(encoding='utf-8')):,} bytes")


if __name__ == "__main__":
    main()
