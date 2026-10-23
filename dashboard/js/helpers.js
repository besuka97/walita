// ---------- Helpers ----------
function fmtDate(iso){ if(!iso) return ""; const d=new Date(iso);
  return isNaN(d)? iso : d.toLocaleDateString("de-DE",{year:"numeric",month:"2-digit",day:"2-digit"}); }
function fmtTime(iso){ if(!iso) return "—"; const d=new Date(iso);
  return isNaN(d)? "—" : d.toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"}); }
function fmtDateTime(iso){ if(!iso) return ""; const d=new Date(iso);
  return isNaN(d)? iso : d.toLocaleString("de-DE",{year:"numeric",month:"2-digit",day:"2-digit",
    hour:"2-digit",minute:"2-digit"}); }
function fmtDuration(min){ const h=Math.floor(min/60), m=min%60;
  return h? h+" h "+m+" min" : m+" min"; }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"']/g,c=>(
  {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

function isoToday(){
  const d=new Date();
  const m=String(d.getMonth()+1).padStart(2,"0");
  const day=String(d.getDate()).padStart(2,"0");
  return d.getFullYear()+"-"+m+"-"+day;
}
function datePickerMax(last){
  const t=isoToday();
  return (last && last>t)?last:t;
}
function bindDateInput(el, first, last){
  if(!el) return;
  if(first) el.min=first;
  el.max=datePickerMax(last);
}
function attachDateClears(){
  if(typeof document==="undefined") return;
  const proto=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
  document.querySelectorAll('input[type="date"]').forEach(input=>{
    if(input.dataset.dateClear) return;
    input.dataset.dateClear="1";
    const btn=document.createElement("button");
    btn.type="button";
    btn.className="date-clear";
    btn.setAttribute("aria-label", "Datum löschen");
    btn.textContent="×";
    input.insertAdjacentElement("afterend", btn);
    function sync(){ btn.disabled=!proto.get.call(input); }
    Object.defineProperty(input, "value", {
      configurable:true,
      get(){ return proto.get.call(input); },
      set(v){ proto.set.call(input, v); sync(); },
    });
    input.addEventListener("input", sync);
    input.addEventListener("change", sync);
    btn.addEventListener("click", ()=>{
      if(!proto.get.call(input)) return;
      proto.set.call(input, "");
      sync();
      input.dispatchEvent(new Event("input", {bubbles:true}));
      input.dispatchEvent(new Event("change", {bubbles:true}));
    });
    sync();
  });
}
attachDateClears();

// Interner Linien-Schlüssel = Name + \\x1f + Operator; Anzeige nur der Name.
const LINE_SEP="\x1f";
const lineName=k=>{
  if(k==null||k==="") return "";
  const s=String(k), i=s.indexOf(LINE_SEP);
  return i<0 ? s : s.slice(0,i);
};
const lineOperator=k=>{
  if(k==null||k==="") return "";
  const s=String(k), i=s.indexOf(LINE_SEP);
  return i<0 ? "" : s.slice(i+1);
};

// Produktkategorie -> Label (global, von Karte und Fahrzeugen genutzt).
const CAT_LABEL={suburban:"S-Bahn",regional:"Regional",regionalExp:"Regional-Express",
  nationalExpress:"Fernverkehr",national:"Fernverkehr",tram:"Tram",subway:"U-Bahn",
  bus:"Bus",ferry:"Fähre"};
const catLabel=c=>CAT_LABEL[c]||c||"Unbekannt";

function tsvCell(s){ return String(s??"").replace(/[\t\n\r]+/g," ").trim(); }
function copyFallback(text){
  const ta=document.createElement("textarea");
  ta.value=text; ta.setAttribute("readonly","");
  ta.style.cssText="position:fixed;left:-9999px";
  document.body.appendChild(ta); ta.select();
  let ok=false;
  try{ ok=document.execCommand("copy"); }catch(e){}
  document.body.removeChild(ta);
  return ok;
}
function copyText(text, btn){
  function feedback(ok){
    if(!btn) return;
    const old=btn.textContent;
    btn.textContent=ok?"Kopiert":"Kopieren fehlgeschlagen";
    setTimeout(()=>{ btn.textContent=old; }, ok?1500:2000);
  }
  if(navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(text).then(()=>feedback(true))
      .catch(()=>feedback(copyFallback(text)));
    return;
  }
  feedback(copyFallback(text));
}

const TRIP_TSV_BASE=["Datum","Linie","Operator","Baureihe","Wagen","Von","Nach",
  "Ab","An","Zwischenhalte","km","Min"];

function tripStopKey(s){
  if(s && s.id!=null && s.id!=="") return String(s.id);
  return "n:"+((s && s.name)||"");
}
function tripPath(t){
  const stops=t.stopovers||[];
  const out=[];
  const n=stops.length;
  if(!n){
    if(t.from) out.push({key:"n:"+t.from, name:t.from});
    if(t.to && (!t.from || t.to!==t.from)) out.push({key:"n:"+t.to, name:t.to});
    return out;
  }
  stops.forEach((s,i)=>{
    if(i!==0 && i!==n-1 && s.cancelled) return;
    const key=tripStopKey(s);
    if(out.length && out[out.length-1].key===key) return;
    out.push({key, name:s.name||""});
  });
  return out;
}
function tripFirstAB(path, aKey, bKey){
  let iA=-1;
  for(let i=0;i<path.length;i++){
    if(iA<0 && path[i].key===aKey) iA=i;
    else if(iA>=0 && path[i].key===bKey) return [iA, i];
  }
  return null;
}
function computeTripRoutes(list){
  const paths=list.map(tripPath);
  const nbr=new Map(), od=new Set();
  function addUndirected(a,b){
    if(a===b) return;
    if(!nbr.has(a)) nbr.set(a,new Set());
    if(!nbr.has(b)) nbr.set(b,new Set());
    nbr.get(a).add(b);
    nbr.get(b).add(a);
  }
  paths.forEach(p=>{
    if(!p.length) return;
    od.add(p[0].key);
    od.add(p[p.length-1].key);
    for(let i=0;i<p.length-1;i++) addUndirected(p[i].key, p[i+1].key);
  });
  const anchors=new Set(od);
  paths.forEach(p=>p.forEach(s=>{
    if((nbr.get(s.key)||new Set()).size>2) anchors.add(s.key);
  }));
  function setsBetween(aKey, bKey){
    const sets=[];
    paths.forEach(p=>{
      const pair=tripFirstAB(p, aKey, bKey);
      if(!pair) return;
      const mid=new Set();
      for(let i=pair[0]+1;i<pair[1];i++) mid.add(p[i].key);
      sets.push(mid);
    });
    return sets;
  }
  const out=new Map();
  list.forEach((t,idx)=>{
    const id=t._i!=null?t._i:idx;
    const p=paths[idx];
    if(p.length<=1){
      out.set(id, p.map(s=>s.name).filter(Boolean).join(" → "));
      return;
    }
    const forced=[];
    p.forEach((s,i)=>{
      if(i===0 || i===p.length-1 || anchors.has(s.key)) forced.push(i);
    });
    const keep=new Set(forced);
    for(let f=0;f<forced.length-1;f++){
      const iA=forced[f], iB=forced[f+1];
      if(iB<=iA+1) continue;
      const sets=setsBetween(p[iA].key, p[iB].key);
      if(sets.length<2) continue;
      const sigs=new Set(sets.map(s=>[...s].sort().join("\0")));
      if(sigs.size<2) continue;
      let inter=null;
      sets.forEach(s=>{
        if(inter==null){ inter=new Set(s); return; }
        [...inter].forEach(k=>{ if(!s.has(k)) inter.delete(k); });
      });
      const uniqueIdx=[];
      for(let i=iA+1;i<iB;i++){
        if(!inter.has(p[i].key)) uniqueIdx.push(i);
      }
      if(!uniqueIdx.length) continue;
      keep.add(uniqueIdx[Math.floor((uniqueIdx.length-1)/2)]);
    }
    const parts=[];
    let lastKey=null;
    p.forEach((s,i)=>{
      if(!keep.has(i) || s.key===lastKey) return;
      lastKey=s.key;
      if(s.name) parts.push(s.name);
    });
    out.set(id, parts.join(" → "));
  });
  return out;
}
function tripTimeText(iso){
  if(!iso) return "";
  const s=fmtTime(iso);
  return s==="—"?"":s;
}
function tripDelayText(d){
  if(d==null) return "";
  if(d>0) return "+"+d;
  return String(d);
}
function minutesBetween(startIso, endIso){
  if(!startIso||!endIso) return null;
  const a=new Date(startIso), b=new Date(endIso);
  if(isNaN(a)||isNaN(b)) return null;
  const m=Math.round((b-a)/60000);
  return m>=0?m:null;
}
/** Ohne Verspätung die Planzeit (Abfahrt Plan → Ankunft Plan), sonst die Istdauer. */
function tripShownDuration(t, delayOn){
  if(!t) return null;
  if(!delayOn) return minutesBetween(t.depPlanned, t.arrPlanned);
  const real=minutesBetween(t.depReal, t.arrReal);
  if(real!=null) return real;
  return t.durationMin==null?null:t.durationMin;
}
function lageLabel(planned){ return planned?"Planlage":"Istlage"; }
function tripsTsv(list, opts){
  opts=opts||{};
  const routeOn=!!opts.route;
  const delayOn=!!opts.delay;
  const mode=opts.timeMode==="real"?"real":"planned";
  const planTimes=mode==="planned";
  const planMin=!delayOn;
  const headers=TRIP_TSV_BASE.slice();
  if(routeOn) headers.splice(10, 0, "Laufweg");
  if(opts.number) headers.unshift("#");
  if(delayOn) headers.push("Versp.");
  const ab=headers.indexOf("Ab");
  const an=headers.indexOf("An");
  const min=headers.indexOf("Min");
  if(ab>=0) headers[ab]="Ab ("+lageLabel(planTimes)+")";
  if(an>=0) headers[an]="An ("+lageLabel(planTimes)+")";
  if(min>=0) headers[min]="Min ("+lageLabel(planMin)+")";
  const routes=routeOn?computeTripRoutes(list||[]):null;
  const lines=[headers.join("\t")];
  (list||[]).forEach((t,i)=>{
    const dep=planTimes?(t.depPlanned||t.depReal):(t.depReal||t.depPlanned);
    const arr=planTimes?(t.arrPlanned||t.arrReal):(t.arrReal||t.arrPlanned);
    const dur=tripShownDuration(t, delayOn);
    const row=[fmtDate(t.date), lineName(t.line), t.operator||"", t.locClass||"",
      t.vehicles||"", t.from||"", t.to||"", tripTimeText(dep), tripTimeText(arr),
      t.viaStops];
    if(routeOn){
      const id=t._i!=null?t._i:i;
      row.push((routes&&routes.get(id))||"");
    }
    row.push(t.distanceKm, dur==null?"":dur);
    if(delayOn) row.push(tripDelayText(t.delay));
    if(opts.number) row.unshift(i+1);
    lines.push(row.map(tsvCell).join("\t"));
  });
  return lines.join("\n");
}

