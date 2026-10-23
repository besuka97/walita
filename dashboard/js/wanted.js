// ---------- Gesuchte Wagen ----------
// Nicht ausgemusterte Fuhrpark-Nummern ohne Fahrt, groß zum Abgleich am Bahnsteig.
// Gefahren zählt über alle Fahrten (DATA), unabhängig von der Heimatregion.
(function(){
  const WANTED_KEY="trwl-wanted-hidden";
  const body=document.getElementById("wantedBody");
  const chipsEl=document.getElementById("wantedClasses");
  const findEl=document.getElementById("wantedFind");
  const summaryEl=document.getElementById("wantedSummary");
  if(!body) return;

  function normKey(n){
    const s=String(n==null?"":n).trim();
    return /^\d+$/.test(s)?String(+s):s;
  }
  function loadHidden(){
    try { return new Set(JSON.parse(localStorage.getItem(WANTED_KEY)||"[]")); }
    catch(e){ return new Set(); }
  }
  function saveHidden(){
    try { localStorage.setItem(WANTED_KEY, JSON.stringify([...hidden].sort())); } catch(e){}
  }
  let hidden=loadHidden();

  const types=(DATA.vehicleRoster&&DATA.vehicleRoster.types)||{};
  const ridden=new Map();
  (DATA.vehicles||[]).forEach(r=>{
    const loc=r.locClass||"";
    if(!ridden.has(loc)) ridden.set(loc,new Set());
    ridden.get(loc).add(normKey(r.vehicleNumber));
  });
  const classes=Object.keys(types)
    .sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}))
    .map(name=>{
      const active=(types[name]||[]).filter(v=>!v.withdrawn);
      const seen=ridden.get(name)||new Set();
      const missing=active.filter(v=>!seen.has(normKey(v.number))).map(v=>String(v.number));
      return {name, total:active.length, missing};
    })
    .filter(c=>c.total>0);

  function renderChips(){
    chipsEl.innerHTML=classes.map(c=>{
      const on=!hidden.has(c.name);
      return `<button type="button" class="wanted-chip${on?" on":""}" data-cls="${esc(c.name)}"
        aria-pressed="${on}">${esc(c.name)} <span>${c.missing.length}</span></button>`;
    }).join("");
  }
  function render(){
    if(!classes.length){
      body.innerHTML='<p class="muted">Kein Fuhrpark hinterlegt. Nummern je Baureihe im Tag-Editor unter Fahrzeuge → Fuhrpark anlegen und das Dashboard neu bauen.</p>';
      summaryEl.textContent="";
      return;
    }
    const q=normKey(findEl.value).toLowerCase();
    const shown=classes.filter(c=>!hidden.has(c.name));
    let nMissing=0;
    const html=shown.map(c=>{
      const nums=q?c.missing.filter(n=>normKey(n).toLowerCase().includes(q)):c.missing;
      nMissing+=nums.length;
      const done=c.total-c.missing.length;
      const head=`<h3 class="wanted-head">${esc(c.name)}
        <span class="muted">${c.missing.length} von ${c.total} fehlen · ${done} gefahren</span></h3>`;
      let tiles;
      if(!c.missing.length) tiles='<p class="wanted-done">Alle gefahren ✓</p>';
      else if(!nums.length) tiles=`<p class="muted">Keine fehlende Nummer passt zu „${esc(findEl.value.trim())}“ – schon gefahren oder nicht im Fuhrpark.</p>`;
      else tiles='<div class="wanted-grid">'+nums.map(n=>`<span class="wanted-num">${esc(n)}</span>`).join("")+'</div>';
      return `<section class="wanted-cls">${head}${tiles}</section>`;
    }).join("");
    body.innerHTML=html||'<p class="muted">Keine Baureihe ausgewählt.</p>';
    summaryEl.textContent=shown.length?nMissing+" gesucht":"";
  }

  chipsEl.addEventListener("click",e=>{
    const btn=e.target.closest("[data-cls]");
    if(!btn) return;
    const name=btn.dataset.cls;
    if(hidden.has(name)) hidden.delete(name); else hidden.add(name);
    saveHidden();
    renderChips();
    render();
  });
  findEl.addEventListener("input",render);
  renderChips();
  render();
})();
