"""JS injected by build_full_catalog.py: grounded AI, generic upload, doctor browser."""

# ---------------------------------------------------------------------------
# Grounded offline assistant
# ---------------------------------------------------------------------------
AI_JS = r"""
/* ===================== GROUNDED ASSISTANT ==============================
   The offline assistant answers only from data actually present in this
   build: the 254,000-product catalogue index, the doctor directory and the
   lab-test list. It routes a question to one of those sources and says so.

   It does NOT diagnose, does not name a medicine for a symptom, and does not
   invent clinical facts. Where the honest answer is "a doctor needs to look
   at this", that is the answer — a health assistant that guesses is worse
   than one that refers.
   ==================================================================== */

const AI_STOP = new Set(['what','whats','is','the','a','an','of','for','to','in','on','my','me','i',
  'can','you','tell','about','give','show','and','or','how','do','does','with','have','has','get',
  'best','good','which','who','should','take','need','please','from','it','its','are','am','be']);

function aiTokens(q){
  return q.toLowerCase().replace(/[^a-z0-9\s.+-]/g,' ').split(/\s+/)
          .filter(w => w.length > 2 && !AI_STOP.has(w));
}

function aiFindMedicine(q){
  /* Score every plausible phrase and take the best, rather than the first.
     "how much dose of paracetamol" offers "much" (a prefix of the brand
     Muchek) before it offers "paracetamol"; first-wins picked the wrong drug
     entirely, which on a pharmacy screen is not a cosmetic bug. */
  const toks = aiTokens(q);
  let best = null;
  for(let len = Math.min(toks.length, 4); len >= 1; len--){
    for(let i = 0; i + len <= toks.length; i++){
      const phrase = toks.slice(i, i+len).join(' ');
      if(phrase.length < 4) continue;
      const hit = searchCatalog(phrase, {limit:1});
      if(!hit.rows.length) continue;
      const row = hit.rows[0];
      const name = CAT.namesLc.slice(CAT.charOff[row], CAT.charOff[row+1]);
      const salt = CAT.saltLc[CAT.salt[row]];
      const wordStart = (hay) => {
        const at = hay.indexOf(phrase);
        if(at < 0) return false;
        const after = hay[at + phrase.length];
        return (at === 0 || /[\s\-(/]/.test(hay[at-1]))
            && (after === undefined || /[\s\-)/0-9]/.test(after));
      };
      let weight = 0;
      if(name === phrase) weight = 5;                             // the whole brand name
      else if(wordStart(salt)) weight = 4;                        // a molecule name
      else if(len > 1 && name.indexOf(phrase) >= 0) weight = 3;   // multi-word inside the brand
      else if(phrase.length >= 5 && wordStart(name)) weight = 3;  // whole word in the brand
      else if(phrase.length >= 6 && name.indexOf(phrase) === 0) weight = 1;  // long prefix
      if(!weight) continue;
      const score = weight * 10 + phrase.length;
      if(!best || score > best.score) best = {row: row, phrase: phrase, score: score};
    }
  }
  return best;
}

function aiSpecialty(q){
  const low = ' ' + q.toLowerCase() + ' ';
  let best = null;
  for(const sp of SPECS){
    if(!sp.keywords) continue;
    for(const kw of sp.keywords){
      if(low.indexOf(kw) >= 0){
        const score = kw.length;
        if(!best || score > best.score) best = {sp: sp, kw: kw, score: score};
      }
    }
  }
  return best;
}

function aiLabTest(q){
  const low = q.toLowerCase();
  let best = null;
  for(const t of LABS){
    const n = t.name.toLowerCase();
    const bare = n.replace(/[^a-z0-9 ]/g,' ');
    const words = bare.split(/\s+/).filter(w=>w.length>2);
    let hit = t.id.length > 3 && low.indexOf(t.id.toLowerCase()) >= 0;
    if(!hit) for(const w of words) if(w.length > 3 && low.indexOf(w) >= 0){ hit = true; break; }
    if(hit){ const score = n.length; if(!best || score < best.score) best = {t:t, score:score}; }
  }
  return best ? best.t : null;
}

const inr0 = (n) => '\u20b9' + Number(n).toLocaleString('en-IN', {maximumFractionDigits:2});


function aiLabAnswer(test){
  const lines = [
    `**${test.name}**`,
    test.why || '',
    `${test.params} parameter${test.params===1?'':'s'} \u00b7 ${test.sample} sample \u00b7 report in ${test.time}`,
    `${inr0(test.price)}${test.mrp>test.price?` (MRP ${inr0(test.mrp)})`:''}`,
    test.fasting ? 'Fasting required \u2014 8 to 10 hours, water is allowed.' : 'No fasting needed.',
    '',
    'I can tell you what a test measures and how to prepare for it. Reading your result is a doctor\u2019s job \u2014 a number out of range means different things depending on the rest of your picture.',
  ].filter(Boolean);
  return {level:'INFO', source:'labs', message: lines.join('\n'),
          actions:[{type:'navigate', label:'Book ' + test.name, route:'labs', primary:true}]};
}

function aiDoctorAnswer(sp, explicit){
  const pool = DOCTORS.filter(d => d.specId === sp.sp.id);
  const pick = pool.slice().sort((a,b) => (b.online-a.online) || (b.rating-a.rating)).slice(0,3);
  const lines = [
    `For that, the right specialist is usually a **${sp.sp.name}**.`,
    sp.sp.about,
    '',
    `${pool.length} listed on Nirog. A few available now:`,
  ];
  pick.forEach(d => lines.push(
    `\u2022 ${d.name} \u2014 ${d.qual}, ${d.exp} yrs \u00b7 ${inr0(d.fee)}${d.online?' \u00b7 online now':''}`));
  if(!explicit){
    lines.push('');
    lines.push('I am matching your words to a specialty, not diagnosing you. If symptoms are severe, sudden or getting worse, treat that as a reason to be seen today rather than to read more.');
  }
  return {level:'INFO', source:'doctors', message: lines.join('\n'),
          actions:[{type:'navigate', label:'See all ' + sp.sp.name + 's', route:'doctors',
                    specId:sp.sp.id, primary:true}]};
}

function aiAnswer(q){
  const low = q.toLowerCase();

  /* ---- 1. safety first, before anything else runs ---- */
  if(CRISIS_RE.some(r => r.test(q))) return triage(q);
  if(EMERGENCY_RE.some(r => r.test(q))) return triage(q);

  /* ---- 2. work out what is being asked BEFORE looking anything up ----
     Fixed ordering was the bug: "doctor for my child fever" matched a syrup
     called Childryl, and "do I fast for a lipid profile" matched a brand
     called Acondec-Fast. Explicit intent has to win over a name collision. */
  const wantsDoctor = /(doctor|doctr|specialist|consult|appointment|physician|surgeon|refer|see a |kis doctor|which doctor)/.test(low);
  const wantsLab    = /(test|lab |report|profile|panel|fasting|fast for|measure|sample|scan|x-?ray|ultrasound|blood work|checkup|check-up)/.test(low);
  const wantsMed    = /(medicine|tablet|capsule|syrup|injection|drug|brand|substitut|alternat|cheaper|generic|composition|salt|contains|what is in|whats in|price|cost|mrp|dose|dosage|side effect|prescription|otc)/.test(low);
  const symptomish  = /(i have|i am|i've|my |suffering|since|feeling|problem|pain|ache|hurts|itch|swell)/.test(low);

  const asksSubstitute = /(substitut|alternat|cheaper|generic|same salt|instead of|replace)/.test(low);
  const asksPrice = /(price|cost|mrp|rate|kitna|how much)/.test(low);

  const sp = aiSpecialty(q);
  const test = aiLabTest(q);

  /* Explicit doctor intent, or a symptom description with no medicine
     vocabulary in it, goes to a specialist rather than the catalogue. */
  if(sp && (wantsDoctor || (symptomish && !wantsMed))) return aiDoctorAnswer(sp, wantsDoctor);
  if(test && wantsLab && !wantsMed) return aiLabAnswer(test);

  /* ---- 3. a specific medicine ---- */
  const med = aiFindMedicine(q);
  if(med && !(wantsDoctor && sp) && !(wantsLab && test)){
    const p = hydrate(med.row, true);
    const subs = substitutesForRow(med.row);
    const cheapest = subs.best_saving;
    const lines = [];

    lines.push(`**${p.name}** \u2014 ${p.mfr || 'manufacturer not listed'}`);
    lines.push(`Composition: ${p.salt}`);
    lines.push(`${p.form} \u00b7 ${p.pack} \u00b7 ${inr0(p.price)}${p.discontinued ? ' \u00b7 discontinued' : ''}`);
    lines.push(p.rx
      ? `Schedule ${p.schedule} \u2014 sold only against a prescription.`
      : `Classified over-the-counter in this catalogue.`);

    if(asksSubstitute || asksPrice || cheapest){
      if(subs.exact_total > 1){
        lines.push('');
        lines.push(`${subs.exact_total.toLocaleString('en-IN')} brands share this exact composition. Cheapest few:`);
        subs.exact.slice(0,4).forEach(x => lines.push(`\u2022 ${x.name} \u2014 ${inr0(x.price)} (${x.mfr})`));
        if(cheapest) lines.push(`Switching to ${cheapest.name} saves ${inr0(cheapest.saving)}, about ${cheapest.saving_percent}%.`);
      } else {
        lines.push('No other brand in this catalogue carries the identical composition and strength.');
      }
    }

    const c = contentFor(p.salt);
    if(/(use|used for|kis liye|what.*for|kaam|purpose|indicat)/.test(low)){
      lines.push('');
      lines.push(c ? `Commonly used for: ${c.uses.join(', ')}.`
                   : 'This catalogue holds no reviewed clinical description for this product, so I will not guess at what it treats. Your prescription or the pack insert is the reliable source, and a pharmacist can read it with you.');
    }
    if(/(side effect|reaction|safe|risk)/.test(low)){
      lines.push('');
      lines.push(c ? `Reported effects include: ${c.side.join(', ')}. General information, not a full safety profile.`
                   : 'I do not hold a reviewed side-effect list for this product. Ask the prescribing doctor or a pharmacist.');
    }
    if(/(dose|dosage|how much|kitni|how many|times a day)/.test(low)){
      lines.push('');
      lines.push('I will not suggest a dose. Dosing depends on age, weight, kidney and liver function and what else you take \u2014 follow the prescription.');
    }

    return {level:'INFO', source:'catalogue', message: lines.join('\n'),
            actions:[{type:'open_med', label:'Open ' + p.name, sku:p.id, primary:true}]
                    .concat(cheapest ? [{type:'open_med', label:'See ' + cheapest.name + ' (' + inr0(cheapest.price) + ')', sku:cheapest.id}] : [])};
  }

  /* ---- 4. lab test or specialty, whichever matched ---- */
  if(test) return aiLabAnswer(test);
  if(sp) return aiDoctorAnswer(sp, wantsDoctor);

  /* ---- 5. general health question with no clear anchor ---- */
  if(/(symptom|pain|feel|fever|hurt|ache|problem|suffering|since|days)/.test(low)){
    return {level:'ROUTINE', source:'triage',
      message:"I can look things up \u2014 what a medicine contains, which brands share it and what they cost, what a lab test measures, and which specialist handles a given problem. What I will not do is work out what is wrong with you from a description, because that needs an examination and a history.\n\nTell me a medicine name, a test name, or what you would like a doctor for, and I will pull it up. If this has been going on a while or is getting worse, book a consultation rather than waiting on an answer here.",
      actions:[{type:'navigate', label:'Consult a General Physician', route:'doctors', specId:'gp', primary:true},
               {type:'navigate', label:'Browse lab tests', route:'labs'}]};
  }

  /* ---- 6. nothing matched ---- */
  return {level:'INFO', source:'none',
    message:"I could not match that to a medicine, a lab test or a specialty in this build.\n\nTry a brand or salt name (\u201cwhat is in Zerodol-P\u201d, \u201ccheaper substitute for Telma 40\u201d), a test (\u201cwhat does HbA1c measure\u201d, \u201cdo I fast for a lipid profile\u201d), or a specialty question (\u201cwhich doctor for kidney stones\u201d).",
    actions:[{type:'navigate', label:'Search medicines', route:'pharmacy'},
             {type:'navigate', label:'Find a doctor', route:'doctors'}]};
}
"""

# ---------------------------------------------------------------------------
# Generic file upload
# ---------------------------------------------------------------------------
UPLOAD_JS = r"""
/* ===================== FILE UPLOAD =====================================
   One component behind every upload in the app: prescriptions, lab reports,
   health records, insurance claims, consultation attachments. PDF, JPG, PNG,
   WEBP and HEIC, validated for type and size, with image previews.

   It never fakes a submission. Offline there is nowhere to send a file, and
   the panel says so rather than showing a success screen that means nothing.
   ==================================================================== */
const UP_TYPES = ['image/jpeg','image/jpg','image/png','image/webp','image/heic','image/heif','application/pdf'];
const UP_EXT = /\.(jpe?g|png|webp|heic|heif|pdf)$/i;
const UP_MAX_MB = 10;

const UPLOAD_KINDS = {
  prescription: {title:'Upload prescription', icon:'doc',
    blurb:'Schedule H and H1 medicines can only be dispensed against a valid prescription from a registered practitioner.',
    real:'In the full build this goes to a registered pharmacist, who checks the prescriber\u2019s registration number and the patient details before any Schedule H medicine is dispatched.'},
  lab_report: {title:'Upload lab report', icon:'flask',
    blurb:'Add a report from any lab. It will sit alongside your Nirog results so trends can be read together.',
    real:'In the full build the report is parsed, the values are extracted into your record timeline, and the file is stored encrypted.'},
  record: {title:'Add to health records', icon:'folder',
    blurb:'Prescriptions, discharge summaries, scans, vaccination cards \u2014 anything you want kept in one place.',
    real:'In the full build the file is categorised, indexed and stored encrypted, and can be shared with a doctor for a single consultation.'},
  claim: {title:'Upload claim documents', icon:'shield',
    blurb:'Hospital bill, discharge summary, prescriptions and diagnostic reports.',
    real:'In the full build these are submitted to the insurer\u2019s claims API and tracked against a claim number.'},
  consult: {title:'Share a report with the doctor', icon:'stethoscope',
    blurb:'Attach anything the doctor should see before or during the consultation.',
    real:'In the full build the doctor sees the file inside the consultation, and access ends when the consultation does.'},
};

function FileUpload({kind, onClose, onDone}){
  const s=useStore();
  const meta=UPLOAD_KINDS[kind]||UPLOAD_KINDS.record;
  const [files,setFiles]=useState([]);
  const [err,setErr]=useState('');
  const [drag,setDrag]=useState(false);
  const inputRef=useRef(null);

  const accept=(list)=>{
    const picked=Array.from(list||[]);
    if(!picked.length) return;
    const ok=[]; let problem='';
    for(const f of picked){
      if(UP_TYPES.indexOf(f.type)<0 && !UP_EXT.test(f.name)){
        problem=f.name+' is not a PDF, JPG, PNG, WEBP or HEIC file.'; continue;
      }
      if(f.size > UP_MAX_MB*1024*1024){
        problem=f.name+' is larger than '+UP_MAX_MB+' MB.'; continue;
      }
      ok.push({name:f.name, size:f.size, type:f.type, at:new Date().toISOString(),
               url: f.type.indexOf('image/')===0 ? URL.createObjectURL(f) : null,
               blob: f});
    }
    setErr(problem);
    setFiles(prev=>prev.concat(ok).slice(0,8));
  };

  const remove=(i)=>setFiles(prev=>{
    const f=prev[i]; if(f && f.url) URL.revokeObjectURL(f.url);
    return prev.filter((_,k)=>k!==i);
  });

  const kb=(n)=> n>1048576 ? (n/1048576).toFixed(1)+' MB' : Math.max(1,Math.round(n/1024))+' KB';

  return html`<div class="modal">
    <div class="scrim" onClick=${onClose}/>
    <div class="modal-card" style=${{position:'relative',zIndex:1,padding:22,maxWidth:540}}>
      <div class="row between center" style=${{marginBottom:6}}>
        <div class="row center" style=${{gap:11}}>
          <div class="qa-ic" style=${{width:38,height:38,background:'var(--mint)',color:'var(--teal-600,#0d7a6f)'}}>
            <${Icon} name=${meta.icon} size=19/></div>
          <h3 class="h-md" style=${{margin:0}}>${meta.title}</h3>
        </div>
        <button class="icon-btn" onClick=${onClose}><${Icon} name="x" size=18/></button>
      </div>
      <p class="sub" style=${{marginBottom:16}}>${meta.blurb}</p>

      <div class=${'rx-drop'+(drag?' on':'')}
        onClick=${()=>inputRef.current&&inputRef.current.click()}
        onDragOver=${e=>{e.preventDefault();setDrag(true);}}
        onDragLeave=${()=>setDrag(false)}
        onDrop=${e=>{e.preventDefault();setDrag(false);accept(e.dataTransfer.files);}}>
        <div class="qa-ic" style=${{background:'var(--teal-50)',color:'var(--teal-500)',margin:'0 auto 10px'}}>
          <${Icon} name="upload" size=24/></div>
        <div style=${{fontWeight:700}}>Tap to choose, or drag files here</div>
        <div class="sub" style=${{fontSize:12,marginTop:4}}>PDF, JPG, PNG, WEBP or HEIC \u00b7 up to ${UP_MAX_MB} MB each \u00b7 max 8 files</div>
        <input ref=${inputRef} type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.webp,.heic,image/*,application/pdf"
          style=${{display:'none'}} onChange=${e=>{accept(e.target.files); e.target.value='';}}/>
      </div>

      ${err?html`<div class="sub" style=${{color:'var(--coral)',marginTop:10,fontSize:12.5}}>${err}</div>`:null}

      ${files.length?html`<div style=${{marginTop:14,maxHeight:220,overflowY:'auto'}}>
        ${files.map((f,i)=>html`<div key=${i} class="subst-row" style=${{marginBottom:8}}>
          ${f.url?html`<img src=${f.url} alt="" style=${{width:44,height:44,borderRadius:10,objectFit:'cover',flexShrink:0}}/>`
                 :html`<div class="subst-ic"><${Icon} name="doc" size=20/></div>`}
          <div style=${{flex:1,minWidth:0}}>
            <div style=${{fontWeight:700,fontSize:13.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>${f.name}</div>
            <div class="sub" style=${{fontSize:12}}>${kb(f.size)} \u00b7 ${f.type||'file'}</div>
          </div>
          <button class="icon-btn" onClick=${()=>remove(i)}><${Icon} name="x" size=16/></button>
        </div>`)}
      </div>`:null}

      <div class="card card-pad" style=${{marginTop:16,background:'var(--amber-soft)',border:'1px solid #ffe2b0',padding:'12px 14px'}}>
        <div class="row" style=${{gap:10,alignItems:'flex-start'}}>
          <${Icon} name="info" size=18 style=${{color:'var(--amber)',flexShrink:0,marginTop:1}}/>
          <div class="sub" style=${{fontSize:12.3,lineHeight:1.6,color:'var(--ink-2)'}}>
            <b style=${{color:'var(--ink)'}}>This demo has no backend.</b> Files stay in this browser tab, are never uploaded, and disappear on reload. ${meta.real}
          </div>
        </div>
      </div>

      <div class="row" style=${{gap:10,marginTop:16}}>
        <button class="btn btn-ghost" style=${{flex:1}} onClick=${onClose}>Cancel</button>
        <button class="btn btn-primary" style=${{flex:1}} disabled=${!files.length}
          onClick=${()=>{ if(onDone) onDone(files);
            s.toast(files.length+' file'+(files.length>1?'s':'')+' added to this session');
            onClose(); }}>
          Add ${files.length?'('+files.length+')':''}
        </button>
      </div>
    </div>
  </div>`;
}
"""

# ---------------------------------------------------------------------------
# Doctors browser
# ---------------------------------------------------------------------------
DOCTORS_JS = r"""function Doctors(){
  const s=useStore();
  const [spec,setSpec]=useState(s.docSpec||null);
  const [city,setCity]=useState('All');
  const [mode,setMode]=useState('All');
  const [sort,setSort]=useState('relevance');
  const [max,setMax]=useState(24);

  useEffect(()=>{ if(s.docSpec){ setSpec(s.docSpec); s.setDocSpec(null); } },[s.docSpec]);

  const cities=['All',...Array.from(new Set(DOCTORS.map(d=>d.city).filter(Boolean))).sort()];
  const q=(s.query||'').trim().toLowerCase();

  let list=DOCTORS.filter(d=>{
    if(spec && d.specId!==spec) return false;
    if(city!=='All' && d.city!==city) return false;
    if(mode==='Online now' && !d.online) return false;
    if(mode==='Video' && !d.video) return false;
    if(mode==='In clinic' && !d.clinic) return false;
    if(!q) return true;
    return (d.name+' '+d.spec+' '+d.qual+' '+(d.hospital||'')+' '+(d.city||'')+' '+(d.langs||[]).join(' '))
             .toLowerCase().indexOf(q)>=0;
  });
  if(sort==='fee') list=list.slice().sort((a,b)=>a.fee-b.fee);
  else if(sort==='fee_desc') list=list.slice().sort((a,b)=>b.fee-a.fee);
  else if(sort==='exp') list=list.slice().sort((a,b)=>b.exp-a.exp);
  else if(sort==='rating') list=list.slice().sort((a,b)=>b.rating-a.rating);
  else list=list.slice().sort((a,b)=>(b.online-a.online)||(b.rating-a.rating));

  const shown=list.slice(0,max);

  return html`<div class="page-enter">
    <div class="eyebrow">Consult</div>
    <h1 class="h-xl" style=${{marginTop:6}}>Find a doctor</h1>
    <p class="sub" style=${{marginTop:6,maxWidth:620}}>${DOCTORS.length} doctors across ${SPECS.length} specialties. Video or in-clinic, with fees shown before you book.</p>

    <div class="scroll-x" style=${{marginTop:18}}>
      <button class=${'chip'+(!spec?' on':'')} onClick=${()=>setSpec(null)}>All specialties</button>
      ${SPECS.map(sp=>html`<button key=${sp.id} class=${'chip'+(spec===sp.id?' on':'')} onClick=${()=>setSpec(sp.id)}>
        ${sp.name} <span style=${{opacity:.6,fontWeight:600}}>${sp.count}</span></button>`)}
    </div>

    <div class="row between center wrap" style=${{marginTop:16,gap:12}}>
      <div class="sub">${list.length} doctor${list.length===1?'':'s'}${q?` matching “${s.query}”`:''}${spec?` \u00b7 ${(SPECS.find(x=>x.id===spec)||{}).name}`:''}</div>
      <div class="sortbar">
        <select value=${city} onChange=${e=>setCity(e.target.value)}>
          ${cities.map(c=>html`<option key=${c} value=${c}>${c==='All'?'All cities':c}</option>`)}
        </select>
        <select value=${mode} onChange=${e=>setMode(e.target.value)}>
          <option value="All">Any mode</option>
          <option value="Online now">Online now</option>
          <option value="Video">Video consult</option>
          <option value="In clinic">In clinic</option>
        </select>
        <select value=${sort} onChange=${e=>setSort(e.target.value)}>
          <option value="relevance">Best match</option>
          <option value="fee">Fee: low to high</option>
          <option value="fee_desc">Fee: high to low</option>
          <option value="exp">Most experienced</option>
          <option value="rating">Highest rated</option>
        </select>
        ${q?html`<button class="btn btn-ghost btn-sm" onClick=${()=>s.setQuery('')}>Clear</button>`:null}
      </div>
    </div>

    ${spec?html`<div class="card card-pad" style=${{marginTop:14,background:'var(--mint)',border:'1px solid var(--teal-100)'}}>
      <div class="sub" style=${{fontSize:13}}>${(SPECS.find(x=>x.id===spec)||{}).about}</div></div>`:null}

    <div class="doc-grid" style=${{marginTop:16}}>
      ${shown.length?shown.map(d=>html`<div key=${d.id} class="card doc-card" onClick=${()=>s.openDoc(d)}
          role="button" tabIndex=${0} style=${{cursor:'pointer'}}
          onKeyDown=${e=>{if(e.key==='Enter'){e.preventDefault();s.openDoc(d);}}}>
        <div class="row" style=${{gap:13,alignItems:'flex-start'}}>
          <${Avatar} name=${d.name} online=${d.online}/>
          <div style=${{flex:1,minWidth:0}}>
            <div class="row center" style=${{gap:7,flexWrap:'wrap'}}>
              <span style=${{fontWeight:800,fontSize:14.5}}>${d.name}</span>
              ${d.online?html`<${Tag} kind="green">Online</${Tag}>`:null}
            </div>
            <div class="sub" style=${{fontSize:12.5}}>${d.spec} \u00b7 ${d.exp} yrs</div>
            <div class="sub" style=${{fontSize:11.5,marginTop:2}}>${d.qual}</div>
            <div class="sub" style=${{fontSize:11.5,marginTop:4}}>${d.hospital}</div>
            <div class="row center" style=${{gap:8,marginTop:8,flexWrap:'wrap'}}>
              <${Stars} r=${d.rating}/>
              <span class="sub" style=${{fontSize:11.5}}>${d.reviews.toLocaleString('en-IN')} reviews</span>
            </div>
            <div class="sub" style=${{fontSize:11.5,marginTop:4}}>${(d.langs||[]).join(', ')}</div>
          </div>
          <div style=${{textAlign:'right',flexShrink:0}}>
            <div style=${{fontWeight:800,fontFamily:'var(--display)',fontSize:16}}>${inr(d.fee)}</div>
            <div class="sub" style=${{fontSize:11}}>consult fee</div>
            <div class="sub" style=${{fontSize:11,marginTop:6,color:'var(--teal-600,#0d7a6f)',fontWeight:700}}>${d.next_slot||''}</div>
          </div>
        </div>
      </div>`):html`<div class="empty" style=${{gridColumn:'1/-1'}}>
        <div class="empty-ic"><${Icon} name="stethoscope" size=30/></div>
        <div style=${{fontWeight:700}}>No doctor matches those filters</div>
        <p class="sub">Try a different city, or clear the specialty filter.</p></div>`}
    </div>

    ${list.length>max?html`<button class="btn btn-ghost btn-block" style=${{marginTop:16}}
      onClick=${()=>setMax(m=>m+24)}>Show more (${list.length-max} left)</button>`:null}

    <div class="card card-pad" style=${{marginTop:20,background:'var(--amber-soft)',border:'1px solid #ffe2b0'}}>
      <div class="row" style=${{gap:11,alignItems:'flex-start'}}>
        <${Icon} name="info" size=19 style=${{color:'var(--amber)',flexShrink:0,marginTop:1}}/>
        <div class="sub" style=${{fontSize:12.5,lineHeight:1.6,color:'var(--ink-2)'}}>
          <b style=${{color:'var(--ink)'}}>These doctor profiles are demo data.</b> Names, registration numbers, hospitals and availability are generated for this build and match no real practitioner. A live directory has to be built from NMC-verified registrations with a signed agreement from each doctor.
        </div>
      </div>
    </div>
  </div>`;
}"""

DOCTORS_CSS = """
.doc-grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(340px,1fr))}
.doc-card{padding:16px;transition:.16s}
.doc-card:hover{border-color:var(--teal-500);transform:translateY(-2px);box-shadow:var(--shadow-lg)}
@media(max-width:520px){.doc-grid{grid-template-columns:1fr}}
"""
