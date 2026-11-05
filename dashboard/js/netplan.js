// ---------- Netzplan-Seite ----------
// Je Attribut ein Dropdown mit Mehrfachauswahl. Jeder gewählte Wert fügt dem
// Plan seine Fahrten hinzu, Von/Bis begrenzen das Ergebnis. Ohne Wahl bleibt der
// Plan leer.
(function(){
  const barEl=document.getElementById("npBar");
  const fromEl=document.getElementById("npFrom");
  const toEl=document.getElementById("npTo");
  const passEl=document.getElementById("npPassLabels");
  const clearEl=document.getElementById("npClear");
  const countEl=document.getElementById("npCount");
  const bodyEl=document.getElementById("npBody");
  const tabEl=document.getElementById("netplan");
  const navEl=document.querySelector('.navlist [data-tab="netplan"]');
  const plan=lineDetail.scopedPlan(bodyEl, passEl);
  const FACETS=[
    {id:"line", title:"Linie"},
    {id:"loc", title:"Baureihe"},
    {id:"veh", title:"Fahrzeug"},
    {id:"cat", title:"Kategorie"},
    {id:"op", title:"Operator"},
    {id:"year", title:"Jahr", desc:true},
  ];
  // Ab hier braucht das Layout mehrere Sekunden und startet erst auf Knopfdruck.
  const ASK_ABOVE=300;
  // Mehrere schnelle Klicks lösen nur einen Layoutlauf aus.
  const DRAW_DELAY_MS=400;
  const FAM_PREFIX="fam:";
  const picked=new Map(FACETS.map(f=>[f.id,new Set()]));
  let rows=[], values=new Map(), stale=false, timer=null;

  // Je Fahrt die Werte, unter denen sie zu finden ist, und je Attribut die
  // Liste der Werte mit Beschriftung und Gruppe.
  function index(){
    const fam=D().locClassFamilies||{};
    const famsOf=new Map();
    for(const name of Object.keys(fam)) for(const c of fam[name]){
      if(!famsOf.has(c)) famsOf.set(c,[]);
      famsOf.get(c).push(name);
    }
    values=new Map(FACETS.map(f=>[f.id,new Map()]));
    const add=(f,key,label,group)=>{
      if(!values.get(f).has(key)) values.get(f).set(key,{key,label,group});
      return key;
    };
    rows=(D().trips||[]).map(t=>{
      const day=statsYmd(t), loc=t.locClass||"", line=t.line||"";
      const nums=String(t.vehicles||"").split(",").map(s=>s.trim()).filter(Boolean);
      const vals={
        op:[add("op", t.operator||"", t.operator||"(ohne Operator)", "")],
        line:[add("line", line, lineName(line)||"(ohne Linie)", lineOperator(line)||"(ohne Operator)")],
        loc:[add("loc", loc, loc||"(ohne Baureihe)", "Baureihen"),
          ...(famsOf.get(loc)||[]).map(n=>add("loc", FAM_PREFIX+n, n, "Familien"))],
        cat:[add("cat", t.category||"", catLabel(t.category||""), "")],
        year:[add("year", day.slice(0,4), day.slice(0,4)||"(ohne Jahr)", "")],
        veh:nums.length
          ? nums.map(n=>add("veh", loc+"\x1f"+n, n, loc||"(ohne Baureihe)"))
          : [add("veh", "", "(ohne Fahrzeug)", "(ohne Fahrzeug)")],
      };
      return {t, day, vals};
    });
    // Werte, die es im aktuellen Datenblock nicht gibt, fallen aus der Wahl.
    for(const [f,set] of picked) for(const v of set) if(!values.get(f).has(v)) set.delete(v);
  }
  function inDates(r){
    return (!fromEl.value || r.day>=fromEl.value) && (!toEl.value || r.day<=toEl.value);
  }
  const anyPicked=()=>[...picked.values()].some(set=>set.size);
  function selected(){
    if(!anyPicked()) return [];
    return rows.filter(r=>inDates(r)
      && FACETS.some(f=>r.vals[f.id].some(v=>picked.get(f.id).has(v))));
  }
  function byLabel(a,b){ return a.label.localeCompare(b.label,"de",{numeric:true}); }
  function listHtml(f, query){
    const q=query.trim().toLocaleLowerCase("de");
    const list=[...values.get(f.id).values()]
      .filter(v=>!q || (v.label+" "+v.group).toLocaleLowerCase("de").includes(q))
      .sort(byLabel);
    if(f.desc) list.reverse();
    const groups=new Map();
    for(const v of list){
      if(!groups.has(v.group)) groups.set(v.group,[]);
      groups.get(v.group).push(v);
    }
    const names=[...groups.keys()].sort((a,b)=>
      (a==="Familien"?-1:b==="Familien"?1:a.localeCompare(b,"de",{numeric:true})));
    return names.map(g=>(g?`<div class="np-sub">${esc(g)}</div>`:"")
      +groups.get(g).map(v=>`<label><input type="checkbox" value="${esc(v.key)}"${picked.get(f.id).has(v.key)?" checked":""}> ${esc(v.label)}</label>`).join("")
    ).join("")||'<span class="muted">Keine Werte</span>';
  }
  function paintButton(f){
    const set=picked.get(f.id), btn=barEl.querySelector(`[data-facet="${f.id}"] .np-open`);
    btn.textContent=!set.size?"keine"
      :set.size===1?values.get(f.id).get([...set][0]).label
      :`${set.size} gewählt`;
  }
  function paintList(f){
    const el=barEl.querySelector(`[data-facet="${f.id}"]`);
    el.querySelector(".np-list").innerHTML=listHtml(f, el.querySelector("input[type=search]").value);
  }
  function closeMenus(){
    barEl.querySelectorAll(".np-menu").forEach(m=>{ m.hidden=true; });
  }
  function draw(){
    clearTimeout(timer);
    stale=false;
    const list=selected();
    const any=anyPicked();
    countEl.textContent=any?`${list.length.toLocaleString("de-DE")} ${list.length===1?"Fahrt":"Fahrten"}`:"";
    if(!list.length){
      plan.clear();
      bodyEl.innerHTML=`<p class="hint">${any?"Keine Fahrt passt zu dieser Auswahl.":"Werte auswählen, die in den Plan sollen."}</p>`;
      return;
    }
    const trips=new Set(list.map(r=>r.t));
    const run=delay=>{
      bodyEl.innerHTML='<p class="hint">Plan wird berechnet …</p>';
      timer=setTimeout(()=>{
        if(tabEl.classList.contains("active")) plan.draw(t=>trips.has(t), "eigene Auswahl");
        else stale=true;
      }, delay);
    };
    if(list.length<=ASK_ABOVE){ run(DRAW_DELAY_MS); return; }
    plan.clear();
    bodyEl.innerHTML=`<p class="hint">Bei so vielen Fahrten blockiert die Berechnung die Seite für bis zu einer Minute.</p>
      <button type="button" class="toolbar-btn np-btn" id="npRun">Plan berechnen</button>`;
    document.getElementById("npRun").onclick=()=>run(30);
  }
  barEl.insertAdjacentHTML("afterbegin", FACETS.map(f=>`<div class="ctl np-multi" data-facet="${f.id}">${f.title}
    <button type="button" class="np-open" aria-haspopup="true"></button>
    <div class="np-menu" hidden><input type="search" placeholder="Suchen …" aria-label="${f.title} suchen"><div class="np-list"></div></div></div>`).join(""));
  const facetOf=el=>FACETS.find(f=>f.id===el.closest("[data-facet]").dataset.facet);
  barEl.addEventListener("click", e=>{
    const b=e.target.closest(".np-open");
    if(!b) return;
    const menu=b.nextElementSibling, opening=menu.hidden;
    closeMenus();
    menu.hidden=!opening;
    if(opening){
      paintList(facetOf(b));
      menu.style.left="0";
      const over=menu.getBoundingClientRect().right-(document.documentElement.clientWidth-16);
      if(over>0) menu.style.left=-over+"px";
      menu.querySelector("input[type=search]").focus();
    }
  });
  barEl.addEventListener("input", e=>{
    if(e.target.matches(".np-menu input[type=search]")) paintList(facetOf(e.target));
  });
  barEl.addEventListener("change", e=>{
    if(!e.target.matches(".np-list input")) return;
    const f=facetOf(e.target), set=picked.get(f.id);
    if(e.target.checked) set.add(e.target.value); else set.delete(e.target.value);
    paintButton(f);
    draw();
  });
  document.addEventListener("click", e=>{ if(!e.target.closest(".np-multi")) closeMenus(); });
  [fromEl,toEl].forEach(el=>el.addEventListener("change", draw));
  passEl.addEventListener("change", draw);
  clearEl.onclick=()=>{
    picked.forEach(set=>set.clear());
    fromEl.value=toEl.value="";
    FACETS.forEach(paintButton);
    closeMenus();
    draw();
  };
  navEl.addEventListener("click", ()=>{
    if(stale) draw();
    else plan.resume();
  });
  function reload(){
    index();
    const k=D().kpis||{};
    bindDateInput(fromEl, k.first, k.last);
    bindDateInput(toEl, k.first, k.last);
    FACETS.forEach(paintButton);
    closeMenus();
  }
  onHomeChange(()=>{
    reload();
    if(tabEl.classList.contains("active")) draw();
    else stale=true;
  });
  reload();
  draw();
})();
