// ---------- Vehicle matrix ----------
(function(){
  let V=[];
  let LC={};
  let vehScoped=false;
  const container=document.getElementById("vehMatrices");
  const groupEl=document.getElementById("vehGroup");
  const lineSortEl=document.getElementById("vehLineSort");
  const opEl=document.getElementById("vehOperator");
  const catEl=document.getElementById("vehCategory");
  const dateFromEl=document.getElementById("vehDateFrom");
  const dateToEl=document.getElementById("vehDateTo");
  const filterEl=document.getElementById("vehFilter");
  const hideUnusedEl=document.getElementById("vehHideUnused");
  const headEl=document.getElementById("vehFilterHead");
  const countEl=document.getElementById("vehCount");
  const modal=document.getElementById("vehModal");
  const modalBody=document.getElementById("vehModalBody");

  // CAT_LABEL/catLabel sind global definiert (siehe oben).
  let sortKey="vehicleNumber", sortAsc=true;
  let cellRegistry=[];

  function fillSelect(el,opts){
    el.innerHTML='<option value="__all__">Alle</option>'+
      opts.map(o=>`<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  }
  function uniq(arr){ return [...new Set(arr)]; }

  function restoreOpt(el, value){
    const ok=[...el.options].some(o=>o.value===value);
    el.value=ok?value:"__all__";
  }
  function reloadVehicles(){
    const src=D();
    V=src.vehicles||[];
    LC=src.lineColors||{};
    const prevOp=vehScoped?opEl.value:null;
    const prevCat=vehScoped?catEl.value:null;
    fillSelect(opEl, uniq(V.map(r=>r.operator)).sort()
      .map(o=>({value:o,label:o||"(ohne Operator)"})));
    fillSelect(catEl, uniq(V.map(r=>r.category)).filter(Boolean).sort()
      .map(c=>({value:c,label:catLabel(c)})));
    if(prevOp!=null) restoreOpt(opEl, prevOp);
    if(prevCat!=null) restoreOpt(catEl, prevCat);
    const kpis=src.kpis||{};
    const vehDates=V.map(r=>(r.date||"").slice(0,10)).filter(Boolean).sort();
    bindDateInput(dateFromEl, kpis.first||vehDates[0], kpis.last||vehDates[vehDates.length-1]);
    bindDateInput(dateToEl, kpis.first||vehDates[0], kpis.last||vehDates[vehDates.length-1]);
    vehScoped=true;
  }

  function cls(k){ return sortKey===k ? (sortAsc?"sorted asc":"sorted") : ""; }

  function selectedShows(){
    return [...document.querySelectorAll('input[name="vehShow"]:checked')].map(el=>el.value);
  }
  function cellText(cr, shows){
    const parts=[];
    if(shows.includes("date")){
      const latest=cr.reduce((a,b)=>a.date>=b.date?a:b);
      parts.push(fmtDate(latest.date));
    }
    if(shows.includes("count")) parts.push("×"+cr.length);
    if(shows.includes("km")){
      const km=Math.round(cr.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10;
      parts.push(km+" km");
    }
    if(shows.includes("segments")){
      const seg=cr.reduce((s,r)=>s+(r.segments||0),0);
      parts.push(seg+" Seg.");
    }
    return parts.join(" · ");
  }

  function rowCmp(a,b){
    let x,y;
    if(sortKey==="first"){ x=a.first; y=b.first; }
    else if(sortKey==="last"){ x=a.last; y=b.last; }
    else {
      const nx=parseFloat(a.number), ny=parseFloat(b.number);
      if(!isNaN(nx)&&!isNaN(ny)) return sortAsc? nx-ny : ny-nx;
      x=a.number; y=b.number;
    }
    const r=String(x).localeCompare(String(y),undefined,{numeric:true});
    return sortAsc? r : -r;
  }

  /** Ganzzahlige Wagennummer oder NaN (nur reine Ziffernketten). */
  function vehInt(n){
    const s=String(n==null?"":n).trim();
    return /^\d+$/.test(s) ? +s : NaN;
  }
  function rosterTypes(){
    const box=DATA.vehicleRoster;
    return (box&&box.types)||{};
  }
  function rosterList(locClass){
    const list=rosterTypes()[locClass];
    return Array.isArray(list)&&list.length?list:null;
  }
  /** Reine Ziffernketten ohne führende Nullen, sonst der Text. */
  function normKey(n){
    const s=String(n==null?"":n).trim();
    return /^\d+$/.test(s)?String(+s):s;
  }
  function fmtIsoDay(iso){
    const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso||""));
    return m?m[3]+"."+m[2]+"."+m[1]:"";
  }
  function rosterHit(list, number){
    if(!list) return null;
    const key=normKey(number);
    for(let i=0;i<list.length;i++){
      if(normKey(list[i].number)===key) return list[i];
    }
    return null;
  }
  function applyRoster(row){
    row.withdrawn=false;
    row.withdrawnOn="";
    const hit=rosterHit(rosterList(row.locClass), row.number);
    if(!hit||!hit.withdrawn) return;
    row.withdrawn=true;
    row.withdrawnOn=hit.withdrawnOn||"";
  }
  function coverageFor(locClass, rides){
    const list=rosterList(locClass);
    if(!list) return null;
    const active=list.filter(v=>!v.withdrawn);
    if(!active.length) return null;
    const ridden=new Set();
    (rides||[]).forEach(r=>{
      if((r.locClass||"")!==locClass) return;
      ridden.add(normKey(r.vehicleNumber));
    });
    const hit=active.filter(v=>ridden.has(normKey(v.number))).length;
    const total=active.length;
    return {hit, total, pct:Math.round(100*hit/total)};
  }
  /**
   * Goldrand: ohne Fuhrpark benachbarte Zeilen mit Nummern ±1.
   * Mit Fuhrpark zählen nur gefahrene, nicht ausgemusterte Nummern.
   * Eine Lücke, die nur aus ausgemusterten Nummern besteht, verbindet sie;
   * die ausgemusterte Zeile bekommt den durchgehenden Strich.
   */
  function markStreaks(rows){
    rows.forEach(r=>{ r.streak=""; });
    const rosterInGroup=rows.some(r=>rosterList(r.locClass));
    if(!rosterInGroup){
      const nums=rows.map(r=>vehInt(r.number));
      let i=0;
      while(i<rows.length){
        if(isNaN(nums[i])){ i++; continue; }
        let j=i+1;
        let step=null;
        while(j<rows.length && !isNaN(nums[j])){
          const d=nums[j]-nums[j-1];
          if(d!==1 && d!==-1) break;
          if(step===null) step=d;
          else if(d!==step) break;
          j++;
        }
        if(j-i>=2){
          rows[i].streak="veh-streak veh-streak-start";
          for(let k=i+1;k<j-1;k++) rows[k].streak="veh-streak veh-streak-mid";
          rows[j-1].streak="veh-streak veh-streak-end";
        }
        i=j>i ? j : i+1;
      }
      return;
    }
    const members=[];
    rows.forEach((row,idx)=>{
      const n=vehInt(row.number);
      if(row.withdrawn||!(row.count>0)||isNaN(n)) return;
      members.push({idx, n, row});
    });
    const activeCache=new Map();
    const withdrawnCache=new Map();
    function intsFor(locClass){
      if(activeCache.has(locClass)) return;
      const active=new Set();
      const withdrawn=new Set();
      (rosterList(locClass)||[]).forEach(v=>{
        const n=vehInt(v.number);
        if(isNaN(n)) return;
        if(v.withdrawn) withdrawn.add(n);
        else active.add(n);
      });
      rows.forEach(r=>{
        if((r.locClass||"")!==locClass||r.withdrawn) return;
        const n=vehInt(r.number);
        if(!isNaN(n)) active.add(n);
      });
      activeCache.set(locClass, active);
      withdrawnCache.set(locClass, withdrawn);
    }
    function gapOnlyWithdrawn(a, b, locClass){
      intsFor(locClass);
      const active=activeCache.get(locClass);
      const withdrawn=withdrawnCache.get(locClass);
      const lo=Math.min(a,b), hi=Math.max(a,b);
      if(hi===lo) return false;
      for(let n=lo+1;n<hi;n++){
        if(active.has(n)||!withdrawn.has(n)) return false;
      }
      return true;
    }
    function connects(a, b, step){
      const d=b.n-a.n;
      if(!d) return null;
      const dir=d>0?1:-1;
      if(step!==null && dir!==step) return null;
      const locA=a.row.locClass||"";
      const locB=b.row.locClass||"";
      if(!rosterList(locA) && !rosterList(locB)){
        if(b.idx!==a.idx+1||(d!==1 && d!==-1)) return null;
        return dir;
      }
      if(locA!==locB||!gapOnlyWithdrawn(a.n, b.n, locA)) return null;
      return dir;
    }
    function closeRun(run){
      if(run.length<2) return;
      run[0].row.streak="veh-streak veh-streak-start";
      for(let k=1;k<run.length-1;k++) run[k].row.streak="veh-streak veh-streak-mid";
      run[run.length-1].row.streak="veh-streak veh-streak-end";
      const lo=Math.min(run[0].idx, run[run.length-1].idx);
      const hi=Math.max(run[0].idx, run[run.length-1].idx);
      let nLo=run[0].n, nHi=run[0].n;
      run.forEach(m=>{
        if(m.n<nLo) nLo=m.n;
        if(m.n>nHi) nHi=m.n;
      });
      const loc=run[0].row.locClass||"";
      for(let i=lo+1;i<hi;i++){
        const r=rows[i];
        if(r.streak||!r.withdrawn||(r.locClass||"")!==loc) continue;
        const n=vehInt(r.number);
        if(!isNaN(n) && n>nLo && n<nHi)
          r.streak="veh-streak veh-streak-through";
      }
    }
    let run=[];
    let step=null;
    members.forEach(member=>{
      if(!run.length){ run=[member]; step=null; return; }
      const dir=connects(run[run.length-1], member, step);
      if(dir===null){
        closeRun(run);
        run=[member];
        step=null;
        return;
      }
      if(step===null) step=dir;
      run.push(member);
    });
    closeRun(run);
  }

  function compareLineName(a, b){
    return lineName(a).localeCompare(lineName(b),undefined,{numeric:true})
      || String(a).localeCompare(String(b));
  }
  /** Linienspalten: Name, oder absteigend nach Fahrten in dieser Tabelle. */
  function sortLines(lineSet, recs){
    const lines=[...lineSet];
    if(lineSortEl&&lineSortEl.value==="rides"){
      const counts=new Map();
      recs.forEach(r=>{
        if(!r.line) return;
        counts.set(r.line,(counts.get(r.line)||0)+1);
      });
      lines.sort((a,b)=>(counts.get(b)||0)-(counts.get(a)||0)||compareLineName(a,b));
      return lines;
    }
    lines.sort(compareLineName);
    return lines;
  }
  function lineColLabel(key, lines){
    const name=lineName(key)||"(ohne Linie)";
    const same=lines.filter(l=>lineName(l)===lineName(key));
    if(same.length<2) return name;
    const op=lineOperator(key);
    return op?name+" ("+op+")":name;
  }
  /**
   * Basis für die Linien-Prozente je Baureihe: nicht ausgemusterte Nummern
   * aus dem Fuhrpark, ohne Fuhrpark alle bisher gefundenen Fahrzeuge (ohne Filter).
   */
  function fleetBase(locClass, found){
    const list=rosterList(locClass);
    if(list) return new Set(list.filter(v=>!v.withdrawn).map(v=>normKey(v.number)));
    return found.get(locClass)||new Set();
  }
  /** Gesamt-Zeile: Zeitraum, Fahrten, km und je Linie Anteil der Basis. */
  function groupTotal(recs, lines, found){
    const ds=recs.map(r=>r.date).filter(Boolean).sort();
    const bases=new Map();
    recs.forEach(r=>{
      const loc=r.locClass||"";
      if(!bases.has(loc)) bases.set(loc, fleetBase(loc, found));
    });
    let total=0;
    bases.forEach(b=>{ total+=b.size; });
    const perLine=new Map();
    lines.forEach(l=>{
      const hit=new Set();
      recs.forEach(r=>{
        if(r.line!==l) return;
        const loc=r.locClass||"", key=normKey(r.vehicleNumber);
        if(bases.get(loc).has(key)) hit.add(loc+"|"+key);
      });
      perLine.set(l, total
        ? {hit:hit.size, total, pct:Math.round(100*hit.size/total)}
        : null);
    });
    return {
      first:ds[0]||"", last:ds[ds.length-1]||"", count:recs.length,
      km:Math.round(recs.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10,
      perLine,
    };
  }
  function pctText(p){ return p?p.pct+" %":""; }
  function groupTitle(name, nVeh, nRides, km, coverage){
    let title=name+" · "+nVeh+" "+(nVeh===1?"Fahrzeug":"Fahrzeuge")+
      " · ×"+nRides+" · "+km+" km";
    if(coverage&&coverage.total)
      title+=" · "+coverage.hit+"/"+coverage.total+" ("+coverage.pct+"%)";
    return title;
  }
  function selectText(el){
    const opt=el.selectedOptions&&el.selectedOptions[0];
    return opt?opt.textContent:el.value;
  }
  function dateInRange(date){
    const d=(date||"").slice(0,10);
    const from=dateFromEl.value, to=dateToEl.value;
    if(!from && !to) return true;
    if(!d) return false;
    return (!from || d>=from) && (!to || d<=to);
  }
  function activeFilterHeading(){
    const parts=[];
    function addSel(el, label){
      if(!el||el.value==="__all__") return;
      parts.push(label+" "+selectText(el));
    }
    if(homeActive()) parts.push("Heimatregion");
    addSel(opEl,"Operator");
    addSel(catEl,"Kategorie");
    const from=dateFromEl.value, to=dateToEl.value;
    if(from&&to) parts.push(fmtDate(from)+"–"+fmtDate(to));
    else if(from) parts.push("ab "+fmtDate(from));
    else if(to) parts.push("bis "+fmtDate(to));
    const q=filterEl.value.trim();
    if(q) parts.push("Suche "+q);
    if(hideUnusedEl&&hideUnusedEl.checked) parts.push("ohne unbenutzte");
    if(lineSortEl&&lineSortEl.value==="rides") parts.push("Spalten nach Fahrten");
    const label=parts.length ? parts.join(" · ") : "Alle";
    return "Fahrzeuge · Eingestellte Filter: "+label;
  }
  function buildView(){
    const groupDim=groupEl.value;
    const op=opEl.value, cat=catEl.value;
    const q=filterEl.value.toLowerCase().trim();
    const shows=selectedShows();
    const hasRoster=Object.keys(rosterTypes()).length>0;
    const filtered=V.filter(r=>{
      if(op!=="__all__" && r.operator!==op) return false;
      if(cat!=="__all__" && r.category!==cat) return false;
      if(!dateInRange(r.date)) return false;
      if(q && !((r.vehicleNumber+" "+lineName(r.line)).toLowerCase().includes(q))) return false;
      return true;
    });
    if(!V.length && !hasRoster) return {kind:"empty", shows, filtered};
    if(!filtered.length && !(groupDim==="locClass" && hasRoster))
      return {kind:"none", shows, filtered};
    const buckets=new Map();
    filtered.forEach(r=>{
      const g=groupDim==="locClass" ? (r.locClass||"Unbekannt") : catLabel(r.category);
      if(!buckets.has(g)) buckets.set(g,[]);
      buckets.get(g).push(r);
    });
    if(groupDim==="locClass"){
      Object.keys(rosterTypes()).forEach(name=>{
        if(!buckets.has(name)) buckets.set(name,[]);
      });
    }
    const showClass=groupDim!=="locClass";
    const found=new Map();
    V.forEach(r=>{
      const loc=r.locClass||"";
      if(!found.has(loc)) found.set(loc,new Set());
      found.get(loc).add(normKey(r.vehicleNumber));
    });
    const groups=[];
    let totalVeh=0;
    [...buckets.keys()].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true})).forEach(gname=>{
      const recs=buckets.get(gname);
      const rowMap=new Map();
      const lineSet=new Set();
      function rowKey(loc, num){
        const locName=loc||"";
        return rosterList(locName)?locName+"|"+normKey(num):locName+"|"+num;
      }
      recs.forEach(r=>{
        const key=rowKey(r.locClass, r.vehicleNumber);
        if(!rowMap.has(key)) rowMap.set(key,{
          number:r.vehicleNumber, locClass:r.locClass||"", recs:[],
          withdrawn:false, withdrawnOn:"",
        });
        rowMap.get(key).recs.push(r);
        if(r.line) lineSet.add(r.line);
      });
      if(groupDim==="locClass"){
        (rosterList(gname)||[]).forEach(v=>{
          const key=gname+"|"+normKey(v.number);
          if(rowMap.has(key)) return;
          rowMap.set(key,{
            number:v.number, locClass:gname, recs:[],
            withdrawn:!!v.withdrawn,
            withdrawnOn:v.withdrawn&&v.withdrawnOn?v.withdrawnOn:"",
          });
        });
      }
      const lines=sortLines(lineSet, recs);
      const rowArr=[...rowMap.values()];
      rowArr.forEach(row=>{
        applyRoster(row);
        const ds=row.recs.map(r=>r.date).filter(Boolean).sort();
        row.first=ds[0]||""; row.last=ds[ds.length-1]||"";
        row.count=row.recs.length;
        row.km=Math.round(row.recs.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10;
      });
      let visible=rowArr;
      if(q) visible=rowArr.filter(row=>
        row.recs.length||String(row.number).toLowerCase().includes(q));
      if(hideUnusedEl&&hideUnusedEl.checked)
        visible=visible.filter(row=>row.count>0);
      // Gruppen ohne gefahrenes Fahrzeug im aktuellen Filter entfallen ganz,
      // auch wenn der Fuhrpark Nummern für die Baureihe listet.
      const ridden=visible.filter(row=>row.count>0).length;
      if(!ridden) return;
      visible.sort(rowCmp);
      markStreaks(visible);
      totalVeh+=ridden;
      const groupKm=Math.round(recs.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10;
      const coverage=groupDim==="locClass"?coverageFor(gname, recs):null;
      groups.push({
        name:gname,
        title:groupTitle(gname, ridden, recs.length, groupKm, coverage),
        lines, rows:visible, km:groupKm,
        total:groupTotal(recs, lines, found),
        roster:groupDim==="locClass"&&!!rosterList(gname),
      });
    });
    if(!groups.length) return {kind:"none", shows, filtered};
    return {kind:"ok", shows, showClass, filtered, totalVeh, groups};
  }

  // Eine leere TSV-Zelle ist nur ein Tab. Mehrere hintereinander verzählt eine KI.
  // „—“ ist schon das Zeichen für fehlende Daten (zuerst/zuletzt) und kommt als
  // Zellinhalt sonst nicht vor.
  const TSV_EMPTY="—";
  function vehiclesTsv(view){
    const head=tsvCell(activeFilterHeading());
    const legend="Leere Zelle: — (keine Fahrt auf dieser Linie bzw. keine Angabe). Jede Zelle ist gefüllt.";
    if(!view||view.kind==="empty") return head+"\nKeine Fahrzeug-Tags vorhanden.";
    if(view.kind==="none") return head+"\nKeine Fahrzeuge für die gewählten Filter.";
    return head+"\n"+legend+"\n\n"+view.groups.map(g=>{
      const headers=["Wagen"];
      if(view.showClass) headers.push("Baureihe");
      const showRetired=g.roster||g.rows.some(row=>row.withdrawn);
      if(showRetired) headers.push("Ausgemustert");
      headers.push("zuerst","zuletzt","Fahrten","km");
      g.lines.forEach(l=>headers.push(lineColLabel(l, g.lines)));
      const lines=[g.title, headers.join("\t")];
      g.rows.forEach(row=>{
        const cells=[row.number||TSV_EMPTY];
        if(view.showClass) cells.push(row.locClass||TSV_EMPTY);
        if(showRetired)
          cells.push(row.withdrawn?(fmtIsoDay(row.withdrawnOn)||"ja"):TSV_EMPTY);
        cells.push(
          row.first?fmtDate(row.first):TSV_EMPTY,
          row.last?fmtDate(row.last):TSV_EMPTY,
          row.count, row.km);
        g.lines.forEach(l=>{
          const cr=row.recs.filter(x=>x.line===l);
          const text=cr.length?cellText(cr, view.shows):"";
          cells.push(text||TSV_EMPTY);
        });
        lines.push(cells.map(tsvCell).join("\t"));
      });
      const t=g.total;
      const cells=["Gesamt"];
      if(view.showClass) cells.push(TSV_EMPTY);
      if(showRetired) cells.push(TSV_EMPTY);
      cells.push(t.first?fmtDate(t.first):TSV_EMPTY, t.last?fmtDate(t.last):TSV_EMPTY,
        t.count, t.km);
      g.lines.forEach(l=>{
        const p=t.perLine.get(l);
        cells.push(p?p.pct+" % ("+p.hit+"/"+p.total+")":TSV_EMPTY);
      });
      lines.push(cells.map(tsvCell).join("\t"));
      return lines.join("\n");
    }).join("\n\n");
  }

  function renderMatrices(){
    if(headEl) headEl.textContent=activeFilterHeading();
    const view=buildView();
    cellRegistry=[];
    if(view.kind==="empty"){
      container.innerHTML='<p class="muted">Keine Fahrzeug-Tags vorhanden. Tagge Fahrten in '+
        'Träwelling mit Wagennummer/Baureihe – die Tags erscheinen beim nächsten Export.</p>';
      countEl.textContent="";
      return;
    }
    if(view.kind==="none"){
      container.innerHTML='<p class="muted">Keine Fahrzeuge für die gewählten Filter.</p>';
      countEl.textContent="0 Fahrzeuge";
      return;
    }
    const showClass=view.showClass;
    const shows=view.shows;
    let html="";
    view.groups.forEach(g=>{
      const lines=g.lines;
      const head=`<tr>
        <th class="sticky lbl ${cls('vehicleNumber')}" data-k="vehicleNumber">Wagen</th>
        ${showClass?'<th class="lbl">Baureihe</th>':''}
        <th class="lbl ${cls('first')}" data-k="first">zuerst</th>
        <th class="lbl ${cls('last')}" data-k="last">zuletzt</th>
        ${lines.map(l=>{
          const c=LC[l];
          const st=c?` style="background:${c[0]};color:${c[1]}"`:'';
          return `<th class="lbl lineh"><span class="line-badge"${st}>${esc(lineName(l))}</span></th>`;
        }).join("")}
      </tr>`;
      const body=g.rows.map(row=>{
        const cells=lines.map(l=>{
          const cr=row.recs.filter(x=>x.line===l);
          if(!cr.length) return "<td></td>";
          const ci=cellRegistry.push(cr)-1;
          const c=LC[l];
          const tint=c?` style="--tint:${c[0]}22"`:'';
          return `<td class="has"${tint} data-ci="${ci}">${cellText(cr,shows)}</td>`;
        }).join("");
        const streakCls=row.streak?` ${row.streak}`:"";
        const withdrawnCls=row.withdrawn?" veh-withdrawn":"";
        const dateBit=row.withdrawnOn
          ?` <span class="muted">· ausgem. ${esc(fmtIsoDay(row.withdrawnOn))}</span>`:"";
        const stats=row.count
          ?`<span class="muted veh-stats">×${row.count} · ${row.km} km</span>`:"";
        return `<tr class="veh${withdrawnCls}">
          <td class="sticky lbl${streakCls}">${esc(row.number)}`+
          dateBit+stats+`</td>
          ${showClass?`<td class="lbl">${esc(row.locClass||"—")}</td>`:''}
          <td class="lbl">${row.first?fmtDate(row.first):"—"}</td>
          <td class="lbl">${row.last?fmtDate(row.last):"—"}</td>
          ${cells}</tr>`;
      }).join("");
      const t=g.total;
      const foot=`<tr class="veh-total">
        <td class="sticky lbl">Gesamt <span class="muted veh-stats">×${t.count} · ${t.km} km</span></td>
        ${showClass?'<td class="lbl"></td>':''}
        <td class="lbl">${t.first?fmtDate(t.first):"—"}</td>
        <td class="lbl">${t.last?fmtDate(t.last):"—"}</td>
        ${lines.map(l=>{
          const p=t.perLine.get(l);
          return p?`<td title="${p.hit} von ${p.total} Fahrzeugen">${pctText(p)}</td>`:"<td></td>";
        }).join("")}
      </tr>`;
      html+=`<div class="matrix-wrap"><h3>${esc(g.title)}</h3>`+
        `<div class="matrix-scroll"><table class="matrix"><thead>${head}</thead><tbody>${body}</tbody>`+
        `<tfoot>${foot}</tfoot></table></div></div>`;
    });
    container.innerHTML=html;
    countEl.textContent=view.totalVeh+" Fahrzeuge · "+view.filtered.length+" Fahrten";
  }

  function openModal(ci){
    const recs=(cellRegistry[ci]||[]).slice()
      .sort((a,b)=>String(b.date).localeCompare(String(a.date)));
    if(!recs.length) return;
    const r0=recs[0];
    const head=`<h3>Wagen ${esc(r0.vehicleNumber)}`+
      `${r0.locClass?" · BR "+esc(r0.locClass):""} · Linie ${esc(lineName(r0.line))}</h3>`;
    const body=recs.map(r=>`<div class="ride">
      <div class="d">${fmtDate(r.date)} · ${esc(lineName(r.line))}</div>
      <div>${esc(r.from)} → ${esc(r.to)}</div>
      <div class="muted">${fmtTime(r.depTime)}–${fmtTime(r.arrTime)} · ${r.distanceKm} km · `+
      `${r.points} Punkte${r.operator?" · "+esc(r.operator):""}</div>
    </div>`).join("");
    modalBody.innerHTML=head+body;
    modal.classList.add("open");
  }
  function closeModal(){ modal.classList.remove("open"); }

  container.addEventListener("click",e=>{
    const th=e.target.closest("th[data-k]");
    if(th){ const k=th.dataset.k;
      if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=true; }
      renderMatrices(); return; }
    const td=e.target.closest("td.has");
    if(td) openModal(+td.dataset.ci);
  });
  document.getElementById("vehModalClose").onclick=closeModal;
  modal.addEventListener("click",e=>{ if(e.target===modal) closeModal(); });
  document.addEventListener("keydown",e=>{ if(e.key==="Escape") closeModal(); });
  [groupEl,opEl,catEl,lineSortEl].forEach(el=>{
    if(el) el.onchange=renderMatrices;
  });
  [dateFromEl,dateToEl].forEach(el=>{
    el.onchange=renderMatrices;
    el.oninput=renderMatrices;
  });
  filterEl.oninput=renderMatrices;
  if(hideUnusedEl) hideUnusedEl.onchange=renderMatrices;
  const copyBtn=document.getElementById("vehCopy");
  if(copyBtn) copyBtn.onclick=()=>copyText(vehiclesTsv(buildView()), copyBtn);
  document.querySelectorAll('input[name="vehShow"]').forEach(el=>{
    el.addEventListener("change",()=>{
      if(!selectedShows().length){ el.checked=true; return; }
      renderMatrices();
    });
  });
  reloadVehicles();
  renderMatrices();
  onHomeChange(()=>{ closeModal(); reloadVehicles(); renderMatrices(); });
})();

