// ---------- Map ----------
let map=null, mapBounds=[], mapFitted=false;
(function(){
  // Karte mit gültiger Startansicht initialisieren; fitBounds erst beim ersten
  // Anzeigen des Tabs, sonst rechnet Leaflet auf einem 0-Pixel-Container.
  map=L.map("mapview",{preferCanvas:true,maxZoom:19}).setView([51,10],6);
  // Provider-Kette: schlägt einer fehl (DNS/Referer/Block), wird automatisch der
  // nächste versucht. Esri braucht keinen Referer und keinen API-Key.
  // maxZoom (einheitlich 19) erlaubt das Reinzoomen; über maxNativeZoom hinaus
  // werden die letzten verfügbaren Kacheln hochskaliert statt das Zoomen zu sperren.
  const PROVIDERS=[
    {url:"https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
     opts:{maxZoom:19, maxNativeZoom:16, attribution:"&copy; Esri, HERE, Garmin"}},
    {url:"https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
     // OSM verlangt laut Tile-Policy einen identifizierbaren Referer; "unsafe-url"
     // sendet die volle Seiten-URL mit, damit die Kacheln nicht (rate-)geblockt
     // werden. Wirkt nur, wenn das Dashboard über http(s) ausgeliefert wird –
     // bei file:// unterdrücken Browser den Referer generell.
     opts:{maxZoom:19, maxNativeZoom:19, subdomains:"abc",
       referrerPolicy:"unsafe-url", attribution:"&copy; OpenStreetMap"}},
    {url:"https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
     opts:{maxZoom:19, maxNativeZoom:17, subdomains:"abc", attribution:"&copy; OpenTopoMap (CC-BY-SA)"}},
  ];
  let baseLayer=null, tileErrors=0;
  function useProvider(i){
    if(baseLayer) map.removeLayer(baseLayer);
    const p=PROVIDERS[i]; tileErrors=0;
    baseLayer=L.tileLayer(p.url, Object.assign(
      {referrerPolicy:"strict-origin-when-cross-origin"}, p.opts));
    baseLayer.on("tileerror", ()=>{
      if(++tileErrors>6 && i+1<PROVIDERS.length){ useProvider(i+1); }
    });
    baseLayer.addTo(map);
  }
  useProvider(0);

  // Rohdaten: Kanten/Knoten referenzieren einen Variant-Index (Attribut-Kombi
  // aus DATA.variants) und nur Station-IDs; die Koordinaten liegen einmalig in ST.
  // Fahrzeug-Filter nutzt eigene Buckets (vehEdges/vehNodes), damit Mehrfachwagen
  // bei "Alle" nicht doppelt zählen.
  let ST={}, VAR=[], FAM={}, VEH=[], LCOL={};
  let rawEdges=[], rawNodes=[], rawVehEdges=[], rawVehNodes=[], rawDisc=[], rawDiscVeh=[];
  let rawSeqs=[], rawVehSeqs=[], rawLocSeqs=[];
  const DISC="#64748b";
  const lineEl=document.getElementById("mapLine");
  const locEl=document.getElementById("mapLoc");
  const vehEl=document.getElementById("mapVehicle");
  const catEl=document.getElementById("mapCat");
  const opEl=document.getElementById("mapOperator");
  const yearEl=document.getElementById("mapYear");
  const dateFromEl=document.getElementById("mapDateFrom");
  const dateToEl=document.getElementById("mapDateTo");
  const discEl=document.getElementById("mapDiscovered");
  const countEl=document.getElementById("mapCount");
  const modeEl=document.getElementById("mapMode");

  // Farbskala nach Häufigkeit, Turbo-Spektrum Blau -> Rot.
  function color(t){ // t in [0,1]
    const stops=[[48,105,229],[0,193,212],[38,201,111],[173,220,48],
                 [250,214,40],[247,150,32],[224,52,32]];
    const x=t*(stops.length-1), i=Math.min(Math.floor(x),stops.length-2), f=x-i;
    const a=stops[i], b=stops[i+1];
    return `rgb(${Math.round(a[0]+(b[0]-a[0])*f)},${Math.round(a[1]+(b[1]-a[1])*f)},${Math.round(a[2]+(b[2]-a[2])*f)})`;
  }

  // Filter-Dropdowns aus den Varianten befüllen (nach Label sortiert). `labelFn`
  // erlaubt eine abweichende Anzeige (z.B. Kategorie-Code -> Klartext). Leere
  // Werte (ungetaggte Fahrt) als eigene "(ohne …)"-Option anbieten.
  function fillSelect(el, values, emptyLabel, labelFn, desc){
    const lf = labelFn || (v=>v);
    const set=new Set(values);
    const hasEmpty=emptyLabel && set.has("");
    let nonEmpty=[...set].filter(v=>v!=="")
      .sort((a,b)=>String(lf(a)).localeCompare(String(lf(b)),undefined,{numeric:true}));
    if(desc) nonEmpty.reverse();
    el.innerHTML='<option value="__all__">Alle</option>'
      + nonEmpty.map(v=>`<option value="${esc(v)}">${esc(lf(v))}</option>`).join("")
      + (hasEmpty?`<option value="__none__">${esc(emptyLabel)}</option>`:"");
  }
  function vehLabel(v){
    if(!v) return "";
    const loc=v[0]||"", num=v[1]||"";
    if(loc && num) return loc+" · "+num;
    return num||loc;
  }
  function fillVehicleSelect(){
    const tagged=VEH.map((v,i)=>({i,v})).filter(x=>x.v[0]||x.v[1]);
    tagged.sort((a,b)=>vehLabel(a.v).localeCompare(vehLabel(b.v),undefined,{numeric:true}));
    const hasEmpty=VEH.some(v=>!v[0]&&!v[1]);
    vehEl.innerHTML='<option value="__all__">Alle</option>'
      + tagged.map(x=>`<option value="${x.i}">${esc(vehLabel(x.v))}</option>`).join("")
      + (hasEmpty?'<option value="__none__">(ohne Fahrzeug)</option>':'');
  }
  function fillLocSelect(){
    const set=new Set(VAR.map(v=>v[1]));
    const hasEmpty=set.has("");
    const classes=[...set].filter(v=>v!=="")
      .sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    const families=Object.keys(FAM)
      .sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    let html='<option value="__all__">Alle</option>';
    if(families.length){
      html+='<optgroup label="Familien">'+
        families.map(f=>`<option value="__fam__${esc(f)}">${esc(f)}</option>`).join("")+
        '</optgroup>';
    }
    if(classes.length){
      html+='<optgroup label="Baureihen">'+
        classes.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join("")+
        '</optgroup>';
    }
    if(hasEmpty) html+='<option value="__none__">(ohne Baureihe)</option>';
    locEl.innerHTML=html;
  }
  function fillLineSelect(){
    const set=new Set(VAR.map(v=>v[0]));
    const hasEmpty=set.has("");
    const byOp=new Map();
    [...set].filter(v=>v!=="").forEach(k=>{
      const op=lineOperator(k)||"";
      if(!byOp.has(op)) byOp.set(op,[]);
      byOp.get(op).push(k);
    });
    const ops=[...byOp.keys()].sort((a,b)=>{
      if(!a) return 1;
      if(!b) return -1;
      return a.localeCompare(b,"de");
    });
    let html='<option value="__all__">Alle</option>';
    ops.forEach(op=>{
      const lines=byOp.get(op).sort((a,b)=>
        lineName(a).localeCompare(lineName(b),undefined,{numeric:true}));
      const label=op||"(ohne Operator)";
      html+=`<optgroup label="${esc(label)}">`+
        lines.map(k=>`<option value="${esc(k)}">${esc(lineName(k)||"(ohne Linie)")}</option>`).join("")+
        '</optgroup>';
    });
    if(hasEmpty) html+='<option value="__none__">(ohne Linie)</option>';
    lineEl.innerHTML=html;
  }
  function restoreSelect(el, value){
    if(!el) return;
    const ok=value && [...el.options].some(o=>o.value===value);
    el.value=ok?value:"__all__";
  }
  function restoreVehicle(value, label){
    if(!value || value==="__all__"){ vehEl.value="__all__"; return; }
    if(value==="__none__"){ restoreSelect(vehEl, value); return; }
    const opt=[...vehEl.options].find(o=>o.textContent===label && o.value!=="__all__" && o.value!=="__none__");
    vehEl.value=opt?opt.value:"__all__";
  }
  function applyScope(){
    const src=D();
    ST=src.stations||{};
    VAR=src.variants||[];
    FAM=src.locClassFamilies||{};
    VEH=src.mapVehicles||[];
    LCOL=src.lineColors||{};
    rawEdges=src.edges||[];
    rawNodes=src.nodes||[];
    rawVehEdges=src.vehEdges||[];
    rawVehNodes=src.vehNodes||[];
    rawSeqs=src.edgeSeqs||[];
    rawVehSeqs=src.vehEdgeSeqs||[];
    rawLocSeqs=src.locEdgeSeqs||[];
    rawDisc=src.discoveredEdges||[];
    rawDiscVeh=src.discoveredVehEdges||[];
    const keep={
      line:lineEl.value, loc:locEl.value, cat:catEl.value, op:opEl.value, year:yearEl.value,
      veh:vehEl.value,
      vehLabel:(vehEl.selectedOptions&&vehEl.selectedOptions[0])?vehEl.selectedOptions[0].textContent:"",
    };
    fillLineSelect();
    fillLocSelect();
    fillVehicleSelect();
    fillSelect(catEl, VAR.map(v=>v[2]), "(ohne Kategorie)", catLabel);
    fillSelect(opEl, VAR.map(v=>v[3]), "(ohne Operator)");
    fillSelect(yearEl, VAR.map(v=>(v[4]||"").slice(0,4)), "(ohne Jahr)", null, true);
    restoreSelect(lineEl, keep.line);
    restoreSelect(locEl, keep.loc);
    restoreSelect(catEl, keep.cat);
    restoreSelect(opEl, keep.op);
    restoreSelect(yearEl, keep.year);
    restoreVehicle(keep.veh, keep.vehLabel);
    const kpis=src.kpis||{};
    bindDateInput(dateFromEl, kpis.first, kpis.last);
    bindDateInput(dateToEl, kpis.first, kpis.last);
  }

  // Einzelnen Select-Wert gegen ein Attribut prüfen (__all__/__none__/Wert).
  function sel(val, actual){
    if(val==="__all__") return true;
    if(val==="__none__") return actual==="";
    return actual===val;
  }
  // Baureihen-Filter: exakter Tag, oder alle Mitglieder einer Familie (__fam__).
  function selLoc(val, actual){
    if(val==="__all__") return true;
    if(val==="__none__") return actual==="";
    if(val.startsWith("__fam__")){
      const members=FAM[val.slice(7)]||[];
      return members.indexOf(actual)>=0;
    }
    return actual===val;
  }
  // Aktuelle Filter-Auswahl auf eine Variante (Attribut-Kombi) anwenden.
  // Index 4 ist das Reisedatum (YYYY-MM-DD). Von/Bis ist inklusive; solange
  // keines von beiden gesetzt ist, filtert das Jahr-Dropdown per Präfix.
  function matchesVar(vi){
    const v=VAR[vi]; if(!v) return false;
    return matchesAttrs(v[0], v[1], v[2], v[3], v[4]||"");
  }
  function matchesAttrs(line, loc, cat, op, date){
    const from=dateFromEl.value, to=dateToEl.value;
    const ranged=!!(from||to);
    const yearOk=ranged ? true : sel(yearEl.value, date.slice(0,4));
    const dateOk=(!from || date>=from) && (!to || date<=to);
    return sel(lineEl.value, line) && selLoc(locEl.value, loc)
        && sel(catEl.value, cat) && sel(opEl.value, op) && yearOk && dateOk;
  }
  // Dieselben Filter auf eine Fahrt (trips[]) angewendet, für die Lupe.
  function matchesTrip(t){
    if(!matchesAttrs(t.line||"", t.locClass||"", t.category||"", t.operator||"", statsYmd(t))) return false;
    const want=vehEl.value;
    if(want==="__all__") return true;
    const nums=String(t.vehicles||"").split(",").map(s=>s.trim()).filter(Boolean);
    if(want==="__none__") return !nums.length;
    const v=VEH[+want];
    return !!v && v[0]===(t.locClass||"") && nums.indexOf(v[1])>=0;
  }
  function matchesVeh(vehIdx){
    const want=vehEl.value;
    if(want==="__all__") return true;
    const v=VEH[vehIdx]; if(!v) return false;
    if(want==="__none__") return !v[0]&&!v[1];
    return String(vehIdx)===want;
  }

  // Sichtbare Kanten/Knoten aus den passenden Buckets aggregieren. Skala je
  // Ansicht neu aus den sichtbaren Zählwerten (min..max) bestimmen.
  let vEdges=[], vNodes=[], vDiscEdges=[], vMin=1, vMax=1;
  // Pfadmodi (Fahrzeuge, Linien, Baureihen) gruppieren Kanten nach gid: im
  // Fahrzeugmodus der Index in VEH, sonst der Index in GROUPS (Linienschlüssel
  // bzw. Baureihe).
  let vPaths=[], vJoins=[], vEdgeSlots=new Map(), vGroupColor=new Map(), vGroupCount=0, GROUPS=[];
  function isVehicleMode(){ return modeEl && modeEl.value==="vehicle"; }
  function isLineMode(){ return modeEl && modeEl.value==="line"; }
  function isLocMode(){ return modeEl && modeEl.value==="loc"; }
  function isPathMode(){ return isVehicleMode() || isLineMode() || isLocMode(); }
  // Variante → Gruppenschlüssel im Linien- bzw. Baureihenmodus.
  function groupKeyOf(vi){ return VAR[vi][isLineMode() ? 0 : 1]; }
  // Knick zwischen zwei gerichteten Kanten an der gemeinsamen Station.
  // 0 = geradeaus. Längen in lon/lat, nur der Winkel zählt.
  function turnAngle(inE, outE){
    const ix=inE.b[1]-inE.a[1], iy=inE.b[0]-inE.a[0];
    const ox=outE.b[1]-outE.a[1], oy=outE.b[0]-outE.a[0];
    const il=Math.hypot(ix,iy)||1, ol=Math.hypot(ox,oy)||1;
    const dot=(ix/il)*(ox/ol)+(iy/il)*(oy/ol);
    return Math.acos(Math.max(-1, Math.min(1, dot)));
  }
  // Gerichtete Kanten einer Gruppe zu Pfaden verketten, nur entlang
  // tatsächlich gefahrener Folgen (seqCount > 0). An einer Station setzt die
  // häufigste Folge den Pfad fort, bei Gleichstand der kleinere Knick. Weitere
  // gefahrene Folgen dort werden Abzweig-Kurven (joins). Ohne gefahrene Folge
  // (Ein-/Ausstieg) beginnt ein neuer Pfad ohne Kurve.
  function chainPaths(edges, seqCount){
    edges.forEach(e=>{ e.next=null; e.prev=null; e.joinIn=false; e.joinOut=false; });
    const outs=new Map(), ins=new Map();
    edges.forEach(e=>{
      if(!outs.has(e.aId)) outs.set(e.aId, []);
      outs.get(e.aId).push(e);
      if(!ins.has(e.bId)) ins.set(e.bId, []);
      ins.get(e.bId).push(e);
    });
    const stations=new Set([...outs.keys(), ...ins.keys()]);
    const joins=[];
    stations.forEach(sid=>{
      const incoming=ins.get(sid)||[], outgoing=outs.get(sid)||[];
      const pairs=[];
      incoming.forEach(inn=>{
        outgoing.forEach(out=>{
          if(out.bId===inn.aId) return;
          const cnt=seqCount(inn, out);
          if(cnt>0) pairs.push({inn, out, cnt, ang:turnAngle(inn, out)});
        });
      });
      pairs.sort((p,q)=>q.cnt-p.cnt || p.ang-q.ang
        || (p.out.bId<q.out.bId?-1:p.out.bId>q.out.bId?1:0));
      const usedIn=new Set(), usedOut=new Set();
      pairs.forEach(p=>{
        if(usedIn.has(p.inn) || usedOut.has(p.out)){
          p.inn.joinOut=true;
          p.out.joinIn=true;
          joins.push({inn:p.inn, out:p.out});
          return;
        }
        usedIn.add(p.inn);
        usedOut.add(p.out);
        p.inn.next=p.out;
        p.out.prev=p.inn;
      });
    });
    const seen=new Set(), paths=[];
    function walk(start){
      const path=[];
      let cur=start;
      while(cur && !seen.has(cur)){
        seen.add(cur);
        path.push(cur);
        cur=cur.next;
      }
      if(path.length) paths.push(path);
    }
    edges.forEach(e=>{ if(!e.prev) walk(e); });
    edges.forEach(e=>{ if(!seen.has(e)) walk(e); });
    return {paths, joins};
  }
  function scale(c){ if(vMax===vMin) return 0.5;
    const t=(Math.log(Math.max(c,1))-Math.log(vMin))/(Math.log(vMax)-Math.log(vMin));
    return Math.max(0, Math.min(1, t)); }
  function computeVisible(){
    const pathMode=isPathMode();
    const useVeh=vehEl.value!=="__all__";
    const em=new Map();
    GROUPS=[];
    const gidOf=new Map();
    function addGroupEdge(a, b, gid, cnt){
      const sa=ST[a], sb=ST[b];
      if(a===b || !sa || !sb) return;
      const key=gid+"|"+a+"|"+b, cur=em.get(key);
      if(cur) cur.count+=cnt;
      else em.set(key,{
        a:[sa[0],sa[1]], b:[sb[0],sb[1]], aId:a, bId:b,
        from:sa[2], to:sb[2], count:cnt, gid, key:a+"|"+b,
      });
    }
    if(isVehicleMode()){
      rawVehEdges.forEach(row=>{
        if(!matchesVar(row[2]) || !matchesVeh(row[3])) return;
        addGroupEdge(row[0], row[1], row[3], row[4]);
      });
    } else if(pathMode){
      // Mit Fahrzeugfilter aus den Fahrzeug-Buckets, sonst aus den Kanten,
      // damit Mehrfachtraktion eine Fahrt nicht doppelt zählt.
      (useVeh ? rawVehEdges : rawEdges).forEach(row=>{
        const vi=row[2];
        if(!matchesVar(vi) || (useVeh && !matchesVeh(row[3]))) return;
        const gk=groupKeyOf(vi);
        let gid=gidOf.get(gk);
        if(gid==null){ gid=GROUPS.length; GROUPS.push(gk); gidOf.set(gk, gid); }
        addGroupEdge(row[0], row[1], gid, useVeh ? row[4] : row[3]);
      });
    } else if(useVeh){
      rawVehEdges.forEach(row=>{
        const a=row[0], b=row[1], vi=row[2], vhi=row[3], cnt=row[4];
        if(!matchesVar(vi) || !matchesVeh(vhi)) return;
        const sa=ST[a], sb=ST[b];
        if(!sa||!sb) return;
        const key=a+"|"+b, cur=em.get(key);
        if(cur) cur.count+=cnt;
        else em.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2],count:cnt});
      });
    } else {
      rawEdges.forEach(row=>{
        const a=row[0], b=row[1];
        if(!matchesVar(row[2])) return;
        const sa=ST[a], sb=ST[b];
        if(!sa||!sb) return;
        const key=a+"|"+b, cur=em.get(key);
        if(cur) cur.count+=row[3];
        else em.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2],count:row[3]});
      });
    }
    vEdges=[...em.values()];
    const nm=new Map();
    if(useVeh){
      rawVehNodes.forEach(row=>{
        const sid=row[0], vi=row[1], vhi=row[2], cnt=row[3], used=row[4]||0;
        const held=row[5]||0, passed=row[6]||0;
        if(!matchesVar(vi) || !matchesVeh(vhi)) return;
        const s=ST[sid]; if(!s) return;
        const cur=nm.get(sid);
        if(cur){ cur.count+=cnt; cur.usedCount+=used; cur.heldCount+=held; cur.passCount+=passed; }
        else nm.set(sid,{sid:String(sid),lat:s[0],lon:s[1],name:s[2],count:cnt,usedCount:used,heldCount:held,passCount:passed});
      });
    } else {
      rawNodes.forEach(row=>{
        const sid=row[0];
        if(!matchesVar(row[1])) return;
        const s=ST[sid]; if(!s) return;
        const cnt=row[2], used=row[3]||0, held=row[4]||0, passed=row[5]||0;
        const cur=nm.get(sid);
        if(cur){ cur.count+=cnt; cur.usedCount+=used; cur.heldCount+=held; cur.passCount+=passed; }
        else nm.set(sid,{sid:String(sid),lat:s[0],lon:s[1],name:s[2],count:cnt,usedCount:used,heldCount:held,passCount:passed});
      });
    }
    vNodes=[...nm.values()];
    const cs=vEdges.map(e=>e.count);
    vMax=cs.length?Math.max(...cs):1;
    vMin=cs.length?Math.min(...cs):1;
    // Dünne/seltene zuerst, damit dicke/häufige oben liegen.
    vEdges.sort((p,q)=>p.count-q.count);
    vNodes.sort((p,q)=>p.count-q.count || ((p.usedCount||0)-(q.usedCount||0)));
    // Entdeckt nur, wenn die Variante zum Filter passt und die Kante unter
    // genau diesem Filter nicht schon als befahren in em liegt (Linie A
    // befahren / Linie B nur Laufweg → bei B grau, bei „Alle“ Heatmap).
    vPaths=[];
    vJoins=[];
    vEdgeSlots=new Map();
    vGroupCount=0;
    if(pathMode){
      const byGroup=new Map();
      vEdges.forEach(e=>{
        if(!byGroup.has(e.gid)) byGroup.set(e.gid, []);
        byGroup.get(e.gid).push(e);
        let slots=vEdgeSlots.get(e.key);
        if(!slots){ slots=[]; vEdgeSlots.set(e.key, slots); }
        if(slots.indexOf(e.gid)<0) slots.push(e.gid);
      });
      // Nur Mitfahrer dieser Kante, und nur solche, die der aktuelle Filter
      // noch zeigt. Ausgeblendete Gruppen lassen keine leere Spur zurück.
      vEdgeSlots.forEach(list=>list.sort((a,b)=>
        groupSortKey(a).localeCompare(groupSortKey(b), undefined, {numeric:true})));
      vGroupCount=byGroup.size;
      assignGroupColors([...byGroup.keys()]);
      // Gefahrene Folgen A→S→B je Gruppe unter dem aktuellen Filter.
      const seqs=new Map();
      const addSeq=(gid, a, sid, b, cnt)=>{
        const k=gid+"|"+a+"|"+sid+"|"+b;
        seqs.set(k, (seqs.get(k)||0)+cnt);
      };
      if(isVehicleMode()){
        rawVehSeqs.forEach(row=>{
          if(!matchesVar(row[3]) || !matchesVeh(row[4])) return;
          addSeq(row[4], row[0], row[1], row[2], row[5]);
        });
      } else {
        const rows=useVeh ? rawVehSeqs
          : (isLocMode() ? rawSeqs.concat(rawLocSeqs) : rawSeqs);
        rows.forEach(row=>{
          const vi=row[3];
          if(!matchesVar(vi) || (useVeh && !matchesVeh(row[4]))) return;
          const gid=gidOf.get(groupKeyOf(vi));
          if(gid!=null) addSeq(gid, row[0], row[1], row[2], useVeh ? row[5] : row[4]);
        });
      }
      const seqCount=(inn, out)=>seqs.get(inn.gid+"|"+inn.aId+"|"+inn.bId+"|"+out.bId)||0;
      [...byGroup.keys()].sort((a,b)=>
        groupSortKey(a).localeCompare(groupSortKey(b), undefined, {numeric:true}))
        .forEach(gid=>{
          const chained=chainPaths(byGroup.get(gid), seqCount);
          chained.paths.forEach(edges=>vPaths.push({gid, edges}));
          chained.joins.forEach(j=>vJoins.push({gid, inn:j.inn, out:j.out}));
        });
    }
    vDiscEdges=[];
    if(discEl && discEl.checked){
      const riddenKeys=pathMode ? new Set(vEdges.map(e=>e.key)) : new Set(em.keys());
      const dm=new Map();
      if(useVeh){
        rawDiscVeh.forEach(row=>{
          const a=row[0], b=row[1], vi=row[2], vhi=row[3];
          if(!matchesVar(vi) || !matchesVeh(vhi)) return;
          const key=a+"|"+b;
          if(riddenKeys.has(key)) return;
          const sa=ST[a], sb=ST[b];
          if(!sa||!sb) return;
          if(!dm.has(key))
            dm.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2]});
        });
      } else {
        rawDisc.forEach(row=>{
          const a=row[0], b=row[1];
          if(!matchesVar(row[2])) return;
          const key=a+"|"+b;
          if(riddenKeys.has(key)) return;
          const sa=ST[a], sb=ST[b];
          if(!sa||!sb) return;
          if(!dm.has(key))
            dm.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2]});
        });
      }
      vDiscEdges=[...dm.values()];
    }
  }
  // Gerichtete Kanten "im Rechtsverkehr": jede Richtung entlang der (nach rechts
  // zeigenden) Segment-Normalen versetzt, plus Richtungspfeil in der Mitte. In
  // Layer-Point-Koordinaten gerechnet und bei jedem Zoom neu gezeichnet, damit der
  // Versatz optisch konstant bleibt.
  const ARROW=7, GAP=1.5;
  // Kanten UND Knoten liegen in EINER LayerGroup im Standard-Canvas (kein eigenes
  // Knoten-Pane): ein zweites Canvas darüber würde als oberste Ebene sämtliche
  // Klicks abfangen, bevor sie das Kanten-Canvas darunter erreichen (Leaflet reicht
  // Klicks nicht von einem Canvas an ein anderes weiter) – dann wäre keine Linie
  // klickbar. Die Knoten werden pro Redraw ZULETZT gezeichnet, liegen dadurch
  // optisch oben und gewinnen den Hit-Test nur punktgenau; Klicks auf die Linie
  // dazwischen treffen die Kante.
  const overlay=L.layerGroup().addTo(map);
  const mapEl=map.getContainer();
  function px(latlng){ return map.latLngToLayerPoint(latlng); }
  function ll(pt){ return map.layerPointToLatLng(pt); }
  function edgeGeom(e, weight){
    const pa=px(e.a), pb=px(e.b);
    const dx=pb.x-pa.x, dy=pb.y-pa.y;
    const len=Math.hypot(dx,dy)||1;
    const ux=dx/len, uy=dy/len;
    const nx=-uy, ny=ux;
    const offMag=weight/2 + GAP;
    const off=L.point(nx*offMag, ny*offMag);
    return {oa:pa.add(off), ob:pb.add(off), ux, uy, nx, ny};
  }
  // Fahrzeugmodus: feste Strichstärke. Jede Richtung liegt rechts der Mittellinie
  // (Rechtsverkehr), dazwischen eine feste Lücke. Weitere Fahrzeuge derselben
  // Richtung stapeln nach außen, nicht in die Gegenrichtung. Kurvenradius in
  // Pixeln, damit er beim Zoom konstant bleibt.
  const VEH_WEIGHT=3, CURVE_R=16, VEH_DIR_GAP=10, BADGE_MAX=3, BADGE_INSET=12;
  // Farbring in OKLCH: je Ton die kräftigste Farbe im sRGB-Raum, Helligkeit
  // nahe der Gamut-Spitze (Gelb hell, Blau dunkler), aber begrenzt, damit
  // Grün und Cyan nicht grell werden. Um Gelb steigt die Grenze bis zur
  // Spitze von #ffd500, damit ein sattes Gelb dabei ist; dort beginnt der Ring.
  // Abstände: Helligkeit, Chroma und Tonwinkel bei fester Referenz-Chroma.
  // Die volle OKLab-Strecke würde die sehr chromareichen Töne (Magenta, Pink)
  // überproportional oft vergeben.
  const RING_L_MIN=0.55, RING_L_MAX=0.80, RING_C_REF=0.12;
  const RING_YELLOW=94.46, RING_YELLOW_L=0.883, RING_YELLOW_W=60;
  function ringLMax(h){
    const d=Math.min(Math.abs(h-RING_YELLOW), 360-Math.abs(h-RING_YELLOW));
    if(d>=RING_YELLOW_W) return RING_L_MAX;
    const k=0.5+0.5*Math.cos(Math.PI*d/RING_YELLOW_W);
    return RING_L_MAX+(RING_YELLOW_L-RING_L_MAX)*k;
  }
  function oklchToLinear(L, C, h){
    const a=C*Math.cos(h*Math.PI/180), b=C*Math.sin(h*Math.PI/180);
    const l=Math.pow(L+0.3963377774*a+0.2158037573*b, 3);
    const m=Math.pow(L-0.1055613458*a-0.0638541728*b, 3);
    const s=Math.pow(L-0.0894841775*a-1.2914855480*b, 3);
    return [
      4.0767416621*l-3.3077115913*m+0.2309699292*s,
      -1.2684380046*l+2.6097574011*m-0.3413193965*s,
      -0.0041960863*l-0.7034186147*m+1.7076147010*s,
    ];
  }
  function inGamut(L, C, h){
    return oklchToLinear(L, C, h).every(v=>v>=-1e-4 && v<=1+1e-4);
  }
  function maxChroma(L, h){
    let lo=0, hi=0.4;
    for(let i=0;i<20;i++){
      const mid=(lo+hi)/2;
      if(inGamut(L, mid, h)) lo=mid; else hi=mid;
    }
    return lo;
  }
  function linearToHex(rgb){
    const enc=c=>{
      c=Math.max(0, Math.min(1, c));
      c=c<=0.0031308 ? 12.92*c : 1.055*Math.pow(c, 1/2.4)-0.055;
      return Math.round(c*255).toString(16).padStart(2, "0");
    };
    return "#"+rgb.map(enc).join("");
  }
  let colorRing=null;
  function getColorRing(){
    if(colorRing) return colorRing;
    const pts=[];
    for(let i=0;i<360;i++){
      const h=(RING_YELLOW+i)%360;
      let cuspL=RING_L_MIN, cuspC=0;
      for(let L=0.3;L<=0.98;L+=0.005){
        const c=maxChroma(L, h);
        if(c>cuspC){ cuspC=c; cuspL=L; }
      }
      const L=Math.max(RING_L_MIN, Math.min(ringLMax(h), cuspL));
      const C=maxChroma(L, h)*0.995;
      pts.push({L, C, a:C*Math.cos(h*Math.PI/180), b:C*Math.sin(h*Math.PI/180)});
    }
    const arc=[0], step=RING_C_REF*Math.PI/180;
    for(let i=1;i<=360;i++){
      const p=pts[i-1], q=pts[i%360];
      arc.push(arc[i-1]+Math.hypot(q.L-p.L, q.C-p.C, step));
    }
    colorRing={pts, arc};
    return colorRing;
  }
  // t ∈ [0,1): Anteil am Ring, 0 = Gelb (#ffd500).
  function ringColor(t){
    const {pts, arc}=getColorRing();
    const target=(((t%1)+1)%1)*arc[360];
    let i=1;
    while(i<360 && arc[i]<target) i++;
    const f=(target-arc[i-1])/((arc[i]-arc[i-1])||1);
    const p=pts[i-1], q=pts[i%360];
    const L=p.L+(q.L-p.L)*f, a=p.a+(q.a-p.a)*f, b=p.b+(q.b-p.b)*f;
    const lms=[
      Math.pow(L+0.3963377774*a+0.2158037573*b, 3),
      Math.pow(L-0.1055613458*a-0.0638541728*b, 3),
      Math.pow(L-0.0894841775*a-1.2914855480*b, 3),
    ];
    return linearToHex([
      4.0767416621*lms[0]-3.3077115913*lms[1]+0.2309699292*lms[2],
      -1.2684380046*lms[0]+2.6097574011*lms[1]-0.3413193965*lms[2],
      -0.0041960863*lms[0]-0.7034186147*lms[1]+1.7076147010*lms[2],
    ]);
  }
  function badgeInk(col){
    const m=/^#([0-9a-f]{6})$/i.exec((col||"").trim());
    if(!m) return "#fff";
    const lin=c=>c<=0.04045 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4);
    const ch=k=>lin(parseInt(m[1].slice(k, k+2), 16)/255);
    const Y=0.2126*ch(0)+0.7152*ch(2)+0.0722*ch(4);
    return Y>0.45 ? "#1c1917" : "#fff";
  }
  // Linienmodus: Farbe der Linie aus den Check-ins (inkl. Linienfarben-Patch).
  // Linien ohne Farbe und alle Fahrzeuge bekommen die Palette.
  function assignGroupColors(ids){
    vGroupColor=new Map();
    if(isLineMode()){
      const rest=[];
      ids.forEach(gid=>{
        const c=LCOL[GROUPS[gid]];
        if(c && c[0]) vGroupColor.set(gid, {bg:c[0], fg:c[1]||badgeInk(c[0])});
        else rest.push(gid);
      });
      assignPaletteColors(rest);
    } else assignPaletteColors(ids);
  }
  // Gleichmäßige Palette für genau die Gruppen des aktuellen Filters: jede
  // Gruppe einen eigenen Slot, der Kreis ist damit global gleich verteilt.
  // Welche Gruppe welchen Slot bekommt, entscheidet die Nachbarschaft auf der
  // Karte (nicht Baureihe oder Nummer): gemeinsame Kante (beide Richtungen),
  // gemeinsame Station und regionale Nähe über Rasterzellen mehrerer Größen.
  // Paris und Wien sieht man nie zugleich, also dürfen sie ähnliche Töne haben.
  const PAL_EDGE_W=4, PAL_STOP_W=2, PAL_GAP_SCALE=45, PAL_BUDGET_MS=150;
  const PAL_CELLS=[[3, 1], [12, 0.6], [40, 0.3]];  // [Zellgröße km, Gewicht]
  function paletteWeights(ids){
    const n=ids.length, pos=new Map();
    ids.forEach((gid, i)=>pos.set(gid, i));
    const w=new Map();  // i*n+j (i<j) -> Gewicht
    const add=(i, j, v)=>{
      if(i===j) return;
      const k=i<j ? i*n+j : j*n+i;
      w.set(k, (w.get(k)||0)+v);
    };
    // Mitglieder je Schlüssel; binär (Kante/Station) oder als Kosinus-Überlappung
    // (Zellen), damit lange Läufe kurze nicht erdrücken.
    const groupsBy=keyFns=>{
      const by=new Map(), size=new Array(n).fill(0);
      vEdges.forEach(e=>{
        const i=pos.get(e.gid);
        if(i==null) return;
        keyFns(e).forEach(key=>{
          let s=by.get(key);
          if(!s){ s=new Set(); by.set(key, s); }
          if(!s.has(i)){ s.add(i); size[i]++; }
        });
      });
      return {by, size};
    };
    const pairs=(by, fn)=>by.forEach(set=>{
      const list=[...set];
      for(let a=0;a<list.length;a++)
        for(let b=a+1;b<list.length;b++) fn(list[a], list[b]);
    });
    const binary=(by, v)=>{
      const seen=new Set();
      pairs(by, (i, j)=>{
        const k=i<j ? i*n+j : j*n+i;
        if(seen.has(k)) return;
        seen.add(k);
        add(i, j, v);
      });
    };
    binary(groupsBy(e=>[e.aId<e.bId ? e.aId+"|"+e.bId : e.bId+"|"+e.aId]).by, PAL_EDGE_W);
    binary(groupsBy(e=>[e.aId, e.bId]).by, PAL_STOP_W);
    const cellOf=(p, km)=>{
      const y=p[0]*111.2, x=p[1]*111.2*Math.cos(p[0]*Math.PI/180);
      return Math.floor(x/km)+","+Math.floor(y/km);
    };
    PAL_CELLS.forEach(([km, v])=>{
      const {by, size}=groupsBy(e=>[cellOf(e.a, km), cellOf(e.b, km)]);
      const shared=new Map();
      pairs(by, (i, j)=>{
        const k=i<j ? i*n+j : j*n+i;
        shared.set(k, (shared.get(k)||0)+1);
      });
      shared.forEach((c, k)=>{
        const i=Math.floor(k/n), j=k%n;
        add(i, j, v*c/Math.sqrt(size[i]*size[j]));
      });
    });
    const nb=Array.from({length:n}, ()=>[]);
    w.forEach((v, k)=>{
      const i=Math.floor(k/n), j=k%n;
      nb[i].push([j, v]);
      nb[j].push([i, v]);
    });
    return nb.map(list=>({
      idx:Int32Array.from(list, x=>x[0]),
      w:Float64Array.from(list, x=>x[1]),
    }));
  }
  function assignPaletteColors(ids){
    const n=ids.length;
    if(!n) return;
    if(n===1){
      setPalette(ids[0], 0.62);
      return;
    }
    // Strafe je Slot-Abstand (Ringanteil in Grad): nahe Töne teuer, ab etwa
    // einem Viertel des Rings fast egal.
    const pen=new Float64Array(n);
    for(let d=0;d<n;d++){
      const gap=Math.min(d, n-d)*360/n;
      pen[d]=Math.exp(-gap/PAL_GAP_SCALE);
    }
    const nb=paletteWeights(ids);
    const slot=new Int32Array(n).fill(-1);
    const used=new Uint8Array(n);
    const cost=(i, s)=>{
      const {idx, w}=nb[i];
      let c=0;
      for(let k=0;k<idx.length;k++){
        const t=slot[idx[k]];
        if(t>=0) c+=w[k]*pen[(s-t+n)%n];
      }
      return c;
    };
    // Gierig: stärkste Nachbarschaft zuerst, freier Slot mit kleinster Strafe.
    // Gleichstand: größter Abstand zu allen vergebenen Slots.
    const total=nb.map(x=>x.w.reduce((a, b)=>a+b, 0));
    const order=[...Array(n).keys()].sort((a, b)=>total[b]-total[a] || a-b);
    const far=new Int32Array(n).fill(n);  // Abstand zum nächsten vergebenen Slot
    order.forEach(i=>{
      let best=-1, bestC=Infinity;
      for(let s=0;s<n;s++){
        if(used[s]) continue;
        const c=cost(i, s);
        if(c<bestC-1e-12 || (c<=bestC+1e-12 && far[s]>far[best])){ best=s; bestC=c; }
      }
      slot[i]=best;
      used[best]=1;
      for(let s=0;s<n;s++) far[s]=Math.min(far[s], (s-best+n)%n, (best-s+n)%n);
    });
    // Feinjustierung über die Gesamtverteilung: Slots paarweise tauschen,
    // solange die Summe der Strafen sinkt (Zeitbudget, damit Filtern flüssig
    // bleibt). C[i*n+s] = Strafe, wenn i auf Slot s läge.
    const W=new Float32Array(n*n), C=new Float32Array(n*n);
    nb.forEach(({idx, w}, i)=>{ for(let k=0;k<idx.length;k++) W[i*n+idx[k]]=w[k]; });
    for(let i=0;i<n;i++) for(let s=0;s<n;s++) C[i*n+s]=cost(i, s);
    const deadline=performance.now()+PAL_BUDGET_MS;
    let improved=true;
    while(improved && performance.now()<deadline){
      improved=false;
      for(let i=0;i<n;i++){
        if(!nb[i].idx.length) continue;
        for(let j=i+1;j<n;j++){
          const si=slot[i], sj=slot[j], wij=W[i*n+j];
          // Das Paar i–j behält seinen Abstand; nur der Eigenanteil in C fällt weg.
          const delta=C[i*n+sj]+C[j*n+si]-C[i*n+si]-C[j*n+sj]
            -2*wij*pen[0]+2*wij*pen[(si-sj+n)%n];
          if(delta>=-1e-6) continue;
          slot[i]=sj; slot[j]=si;
          improved=true;
          const touched=new Set([...nb[i].idx, ...nb[j].idx]);
          touched.forEach(k=>{
            const f=W[k*n+i]-W[k*n+j];
            if(!f) return;
            const row=k*n;
            for(let s=0;s<n;s++)
              C[row+s]+=f*(pen[(s-sj+n)%n]-pen[(s-si+n)%n]);
          });
        }
        if(performance.now()>deadline) break;
      }
    }
    ids.forEach((gid, i)=>setPalette(gid, slot[i]/n));
  }
  function setPalette(gid, t){
    const bg=ringColor(t);
    vGroupColor.set(gid, {bg, fg:badgeInk(bg)});
  }
  function groupColor(gid){
    return vGroupColor.get(gid) || {bg:"#64748b", fg:"#fff"};
  }
  function groupBadgeText(gid){
    if(isLineMode()){
      const k=GROUPS[gid]||"";
      return (DATA.lineLabels||{})[k] || lineName(k) || "–";
    }
    if(isLocMode()) return GROUPS[gid] || "–";
    const v=VEH[gid];
    return (v && v[1]) ? v[1] : "–";
  }
  function groupSortKey(gid){
    if(isLineMode()){
      const k=GROUPS[gid]||"";
      return lineName(k)+" "+(lineOperator(k)||"");
    }
    if(isLocMode()) return GROUPS[gid]||"";
    return vehSortKey(gid);
  }
  function vehSortKey(idx){
    const v=VEH[idx]||["",""];
    return (v[0]||"")+" "+(v[1]||"");
  }
  function offsetGeom(e, slot){
    const pa=px(e.a), pb=px(e.b);
    const dx=pb.x-pa.x, dy=pb.y-pa.y;
    const len=Math.hypot(dx,dy)||1;
    const ux=dx/len, uy=dy/len;
    const nx=-uy, ny=ux;
    const step=VEH_WEIGHT+2;
    // slot 0 innen an der Lücke, höhere Slots weiter rechts.
    const offMag=VEH_DIR_GAP/2 + VEH_WEIGHT/2 + slot*step;
    const off=L.point(nx*offMag, ny*offMag);
    return {oa:pa.add(off), ob:pb.add(off), ux, uy, nx, ny, len};
  }
  function lineIntersect(p, ux, uy, q, vx, vy){
    const det=ux*vy-uy*vx;
    if(Math.abs(det)<1e-6) return null;
    const dx=q.x-p.x, dy=q.y-p.y;
    const t=(dx*vy-dy*vx)/det;
    const s=(dx*uy-dy*ux)/det;
    return {x:p.x+ux*t, y:p.y+uy*t, t, s};
  }
  function sampleQuad(p0, c, p1, steps){
    const pts=[];
    for(let i=0;i<=steps;i++){
      const t=i/steps, u=1-t;
      pts.push(L.point(
        u*u*p0.x + 2*u*t*c.x + t*t*p1.x,
        u*u*p0.y + 2*u*t*c.y + t*t*p1.y));
    }
    return pts;
  }
  function curvePoints(g0, g1, r){
    const p0=L.point(g0.ob.x-g0.ux*r, g0.ob.y-g0.uy*r);
    const p2=L.point(g1.oa.x+g1.ux*r, g1.oa.y+g1.uy*r);
    const hit=lineIntersect(p0, g0.ux, g0.uy, p2, g1.ux, g1.uy);
    let c;
    if(!hit || hit.t<=0 || hit.s>=0 || Math.hypot(hit.x-p0.x, hit.y-p0.y)>r*4){
      c=L.point(
        (p0.x+g0.ux*r + p2.x-g1.ux*r)/2,
        (p0.y+g0.uy*r + p2.y-g1.uy*r)/2);
    } else c=L.point(hit.x, hit.y);
    return sampleQuad(p0, c, p2, 8);
  }
  function slotOf(key, gid){
    const list=vEdgeSlots.get(key)||[gid];
    const slot=list.indexOf(gid);
    return slot<0?0:slot;
  }
  function pathPopup(path, edge){
    let head;
    if(isLineMode()){
      const k=GROUPS[path.gid]||"", op=lineOperator(k);
      head="<b>"+esc(lineName(k)||"(ohne Linie)")+"</b><br>"+(op ? esc(op)+"<br>" : "");
    } else if(isLocMode()){
      const loc=GROUPS[path.gid]||"";
      head="<b>"+(loc ? "BR "+esc(loc) : "(ohne Baureihe)")+"</b><br>";
    } else {
      const v=VEH[path.gid]||["",""];
      head="<b>"+esc(v[1]||"–")+"</b><br>"+(v[0] ? ("BR "+esc(v[0])+"<br>") : "");
    }
    const route=esc(path.edges[0].from)+" → "+path.edges.map(e=>esc(e.to)).join(" → ");
    const leg=esc(edge.from)+" → "+esc(edge.to);
    return head+route+"<br>"+leg+": "+edge.count+"× befahren";
  }
  function addArrow(g, col, weight, fromPt, toPt){
    const m=L.point((fromPt.x+toPt.x)/2, (fromPt.y+toPt.y)/2);
    const tip=L.point(m.x+g.ux*ARROW, m.y+g.uy*ARROW);
    const wingL=L.point(tip.x-g.ux*ARROW+g.nx*ARROW*0.6, tip.y-g.uy*ARROW+g.ny*ARROW*0.6);
    const wingR=L.point(tip.x-g.ux*ARROW-g.nx*ARROW*0.6, tip.y-g.uy*ARROW-g.ny*ARROW*0.6);
    L.polyline([ll(wingL),ll(tip),ll(wingR)],
      {color:col, weight:weight, opacity:.9, interactive:false}).addTo(overlay);
  }
  function addBadge(pt, text, col, gid){
    const icon=L.divIcon({
      className:"map-veh-badge",
      html:'<span data-gid="'+gid+'" style="background:'+col.bg+';color:'+col.fg+'">'+esc(text)+"</span>",
      iconSize:[0,0],
      iconAnchor:[0,0],
    });
    L.marker(ll(pt), {icon, interactive:false, keyboard:false}).addTo(overlay);
  }
  let badgeMeasure=null;
  function badgeBox(text){
    if(!badgeMeasure){
      badgeMeasure=document.createElement("canvas").getContext("2d");
      badgeMeasure.font="700 10px system-ui, Segoe UI, Roboto, sans-serif";
    }
    // Polster enthält den Abstand, den zwei Badges mindestens frei lassen.
    return {w:Math.ceil(badgeMeasure.measureText(text).width)+12, h:18};
  }
  function badgeHits(grid, box){
    const cell=64;
    const x0=Math.floor((box.x-box.w/2)/cell), x1=Math.floor((box.x+box.w/2)/cell);
    const y0=Math.floor((box.y-box.h/2)/cell), y1=Math.floor((box.y+box.h/2)/cell);
    const seen=new Set();
    for(let ix=x0; ix<=x1; ix++){
      for(let iy=y0; iy<=y1; iy++){
        const list=grid.get(ix+","+iy);
        if(!list) continue;
        for(let k=0;k<list.length;k++){
          const o=list[k];
          if(seen.has(o)) continue;
          seen.add(o);
          if(Math.abs(box.x-o.x)<(box.w+o.w)/2 && Math.abs(box.y-o.y)<(box.h+o.h)/2)
            return true;
        }
      }
    }
    return false;
  }
  function badgeKeep(grid, box){
    const cell=64;
    const x0=Math.floor((box.x-box.w/2)/cell), x1=Math.floor((box.x+box.w/2)/cell);
    const y0=Math.floor((box.y-box.h/2)/cell), y1=Math.floor((box.y+box.h/2)/cell);
    for(let ix=x0; ix<=x1; ix++){
      for(let iy=y0; iy<=y1; iy++){
        const key=ix+","+iy;
        let list=grid.get(key);
        if(!list){ list=[]; grid.set(key, list); }
        list.push(box);
      }
    }
  }
  // Sichtbarer Ausschnitt in Layer-Pixeln. Kandidaten liegen BADGE_INSET
  // innerhalb (x0..y1); ein Badge muss ganz in den Ausschnitt passen (ox0..oy1).
  function viewRect(){
    const origin=map.containerPointToLayerPoint(L.point(0, 0));
    const size=map.getSize();
    return {
      x0:origin.x+BADGE_INSET, y0:origin.y+BADGE_INSET,
      x1:origin.x+size.x-BADGE_INSET, y1:origin.y+size.y-BADGE_INSET,
      ox0:origin.x, oy0:origin.y, ox1:origin.x+size.x, oy1:origin.y+size.y,
    };
  }
  function clipToView(a, b, v){
    let t0=0, t1=1;
    const dx=b.x-a.x, dy=b.y-a.y;
    function clip(p, q){
      if(Math.abs(p)<1e-8) return q>=0;
      const t=q/p;
      if(p<0){ if(t>t1) return false; if(t>t0) t0=t; }
      else { if(t<t0) return false; if(t<t1) t1=t; }
      return true;
    }
    if(!clip(-dx, a.x-v.x0) || !clip(dx, v.x1-a.x) || !clip(-dy, a.y-v.y0) || !clip(dy, v.y1-a.y))
      return null;
    const ax=a.x+dx*t0, ay=a.y+dy*t0;
    const bx=a.x+dx*t1, by=a.y+dy*t1;
    const len=Math.hypot(bx-ax, by-ay);
    return {a:L.point(ax,ay), ux:len ? (bx-ax)/len : 0, uy:len ? (by-ay)/len : 0, len};
  }
  function inView(p, v){
    return p.x>=v.x0 && p.y>=v.y0 && p.x<=v.x1 && p.y<=v.y1;
  }
  // Dichte Punkte entlang des sichtbaren Pfads in Fahrtrichtung, damit ein
  // Badge bei Überdeckung weiterwandern kann. Ohne sichtbares gerades Stück
  // (nur Kurve) gilt die Kantenmitte.
  function pathSamples(segs, edgeMids, v){
    const step=8;
    const pts=[];
    segs.forEach(seg=>{
      const c=clipToView(seg.a, seg.b, v);
      if(!c) return;
      for(let at=0; at<=c.len+1e-6; at+=step){
        const t=Math.min(at, c.len);
        pts.push({x:c.a.x+c.ux*t, y:c.a.y+c.uy*t});
      }
    });
    if(!pts.length){
      const mid=edgeMids.find(p=>inView(p, v));
      if(mid) pts.push({x:mid.x, y:mid.y});
    }
    return pts;
  }
  // Reihenfolge der Versuche: Mitte des Pfads, dann abwechselnd nach vorn
  // und hinten.
  function fromMiddle(pool){
    const mid=Math.floor((pool.length-1)/2);
    const order=[pool[mid]];
    for(let d=1; order.length<pool.length; d++){
      if(mid+d<pool.length) order.push(pool[mid+d]);
      if(mid-d>=0) order.push(pool[mid-d]);
    }
    return order;
  }
  function minDist2(p, chosen){
    let md=Infinity;
    chosen.forEach(c=>{
      const d=(p.x-c.x)**2+(p.y-c.y)**2;
      if(d<md) md=d;
    });
    return md;
  }
  function drawGroupPaths(){
    const badgeQueue=[];
    const geomOf=new Map();
    vPaths.forEach(path=>path.edges.forEach(e=>
      geomOf.set(e, offsetGeom(e, slotOf(e.key, path.gid)))));
    // Kantenenden mit Fortsetzung oder Abzweig enden vor der Station; die
    // Abzweig-Kurven setzen an diesen gekürzten Enden an.
    const trimOf=new Map();
    vPaths.forEach(path=>{
      const geoms=path.edges.map(e=>geomOf.get(e));
      const n=geoms.length;
      path.edges.forEach((e,i)=>{
        const g=geoms[i];
        let r0=0, r1=0;
        if(i>0) r0=Math.min(CURVE_R, geoms[i-1].len*0.45, g.len*0.45);
        else if(e.joinIn) r0=Math.min(CURVE_R, g.len*0.45);
        if(i<n-1) r1=Math.min(CURVE_R, g.len*0.45, geoms[i+1].len*0.45);
        else if(e.joinOut) r1=Math.min(CURVE_R, g.len*0.45);
        trimOf.set(e, {r0, r1});
      });
    });
    vJoins.forEach(j=>{
      const g0=geomOf.get(j.inn), g1=geomOf.get(j.out);
      const r=Math.min(CURVE_R, g0.len*0.45, g1.len*0.45);
      if(r<1) return;
      const t0=trimOf.get(j.inn), t1=trimOf.get(j.out);
      const pts=[L.point(g0.ob.x-g0.ux*t0.r1, g0.ob.y-g0.uy*t0.r1)]
        .concat(curvePoints(g0, g1, r))
        .concat([L.point(g1.oa.x+g1.ux*t1.r0, g1.oa.y+g1.uy*t1.r0)]);
      L.polyline(pts.map(ll),{
        color:groupColor(j.gid).bg, weight:VEH_WEIGHT, opacity:.9,
        lineCap:"round", smoothFactor:0, interactive:false,
      }).addTo(overlay);
    });
    vPaths.forEach(path=>{
      const pal=groupColor(path.gid), col=pal.bg;
      const geoms=path.edges.map(e=>geomOf.get(e));
      const n=geoms.length;
      const trim=path.edges.map(e=>trimOf.get(e));
      const segs=[];
      const edgeMids=[];
      path.edges.forEach((e,i)=>{
        const g=geoms[i], r0=trim[i].r0, r1=trim[i].r1;
        const a=L.point(g.oa.x+g.ux*r0, g.oa.y+g.uy*r0);
        const b=L.point(g.ob.x-g.ux*r1, g.ob.y-g.uy*r1);
        const straight=Math.hypot(b.x-a.x, b.y-a.y);
        edgeMids.push(L.point((g.oa.x+g.ob.x)/2, (g.oa.y+g.ob.y)/2));
        if(straight>=1){
          L.polyline([ll(a),ll(b)],{
            color:col, weight:VEH_WEIGHT, opacity:.9, lineCap:"round", smoothFactor:0
          }).bindPopup(pathPopup(path, e)).addTo(overlay);
          if(straight>=8) addArrow(g, col, 2, a, b);
          segs.push({a, b, ux:g.ux, uy:g.uy, len:straight});
        }
        if(i<n-1 && r1>=1){
          const pts=curvePoints(g, geoms[i+1], r1).map(ll);
          const curve=L.polyline(pts,{
            color:col, weight:VEH_WEIGHT, opacity:.9, lineCap:"round", smoothFactor:0,
            interactive:straight<1,
          });
          if(straight<1) curve.bindPopup(pathPopup(path, e));
          curve.addTo(overlay);
        }
      });
      if(segs.length || edgeMids.length){
        badgeQueue.push({
          gid:path.gid, text:groupBadgeText(path.gid), col:pal, segs, edgeMids,
        });
      }
    });
    const view=viewRect();
    // Abstand zwischen Badges auf demselben Pfad, damit ihre Zahl der
    // sichtbaren Strecke im Verhältnis zum Ausschnitt folgt.
    const spacing=Math.min(view.x1-view.x0, view.y1-view.y0)/2;
    const byGroup=new Map();
    badgeQueue.forEach(item=>{
      const pool=pathSamples(item.segs, item.edgeMids, view);
      if(!pool.length) return;
      let g=byGroup.get(item.gid);
      if(!g){
        g={text:item.text, col:item.col, gid:item.gid, paths:[], extra:[], count:0};
        g.size=badgeBox(g.text);
        byGroup.set(item.gid, g);
      }
      g.paths.push(pool);
    });
    const grid=new Map();
    function place(g, pick){
      const box={x:pick.x, y:pick.y, w:g.size.w, h:g.size.h};
      if(box.x-box.w/2<view.ox0 || box.x+box.w/2>view.ox1
        || box.y-box.h/2<view.oy0 || box.y+box.h/2>view.oy1) return null;
      if(badgeHits(grid, box)) return null;
      badgeKeep(grid, box);
      addBadge(L.point(pick.x, pick.y), g.text, g.col, g.gid);
      g.count++;
      return pick;
    }
    // Erst bekommt jeder sichtbare Pfad aller Fahrzeuge ein Badge, möglichst
    // in seiner Mitte. Liegt dort schon eines, rückt es entlang des Pfads.
    byGroup.forEach(g=>{
      g.paths.forEach(pool=>{
        for(const p of fromMiddle(pool)){
          const got=place(g, p);
          if(got){ g.extra.push({pool, shown:[got]}); break; }
        }
      });
    });
    // Danach weitere Badges bis BADGE_MAX pro Fahrzeug. Ein weiteres Badge
    // braucht den Abstand zu den Badges seines eigenen Pfads und sitzt dort,
    // wo es am weitesten von ihnen entfernt ist.
    const minD2=spacing*spacing;
    byGroup.forEach(g=>{
      const open=[];
      g.extra.forEach(path=>path.pool.forEach(p=>open.push({p, path})));
      while(g.count<BADGE_MAX){
        let best=-1, bd=-1;
        open.forEach((c,i)=>{
          const d=minDist2(c.p, c.path.shown);
          if(d>=minD2 && d>bd){ bd=d; best=i; }
        });
        if(best<0) break;
        const c=open[best];
        open.splice(best, 1);
        const got=place(g, c.p);
        if(got) c.path.shown.push(got);
      }
    });
  }
  function draw(){
    overlay.clearLayers();
    vDiscEdges.forEach(e=>{
      const weight=2, g=edgeGeom(e, weight);
      L.polyline([ll(g.oa),ll(g.ob)],{
        color:DISC, weight:weight, opacity:.75, dashArray:"6 6"
      }).bindPopup(`<b>${esc(e.from)} → ${esc(e.to)}</b><br>entdeckt (nicht eingecheckt)`)
        .addTo(overlay);
      const m=L.point((g.oa.x+g.ob.x)/2,(g.oa.y+g.ob.y)/2);
      const tip=L.point(m.x+g.ux*ARROW, m.y+g.uy*ARROW);
      const wingL=L.point(tip.x-g.ux*ARROW+g.nx*ARROW*0.6, tip.y-g.uy*ARROW+g.ny*ARROW*0.6);
      const wingR=L.point(tip.x-g.ux*ARROW-g.nx*ARROW*0.6, tip.y-g.uy*ARROW-g.ny*ARROW*0.6);
      L.polyline([ll(wingL),ll(tip),ll(wingR)],
        {color:DISC,weight:2,opacity:.8,interactive:false}).addTo(overlay);
    });
    if(isPathMode()) drawGroupPaths();
    else vEdges.forEach(e=>{
      const t=scale(e.count), col=color(t), weight=3+t*7;
      const g=edgeGeom(e, weight);
      L.polyline([ll(g.oa),ll(g.ob)],{color:col,weight:weight,opacity:.85})
        .bindPopup(`<b>${esc(e.from)} → ${esc(e.to)}</b><br>${e.count}× befahren`)
        .addTo(overlay);
      // Pfeil-Chevron am Mittelpunkt; interactive:false, damit Klicks zur Linie
      // darunter durchgehen und die Kante klickbar bleibt.
      const m=L.point((g.oa.x+g.ob.x)/2,(g.oa.y+g.ob.y)/2);
      const tip=L.point(m.x+g.ux*ARROW, m.y+g.uy*ARROW);
      const wingL=L.point(tip.x-g.ux*ARROW+g.nx*ARROW*0.6, tip.y-g.uy*ARROW+g.ny*ARROW*0.6);
      const wingR=L.point(tip.x-g.ux*ARROW-g.nx*ARROW*0.6, tip.y-g.uy*ARROW-g.ny*ARROW*0.6);
      L.polyline([ll(wingL),ll(tip),ll(wingR)],
        {color:col,weight:2+t*3,opacity:.9,interactive:false}).addTo(overlay);
    });
    // Knoten ZULETZT -> liegen optisch oben, fangen Klicks aber nur punktgenau ab.
    vNodes.forEach(n=>{
      const used=(n.usedCount||0)>0;
      const held=(n.heldCount||0)>0;
      const passed=(n.passCount||0)>0;
      const heldOnly=held && !used;
      const col=isPathMode()
        ? (used?"#1e293b":"#3d5a80")
        : (used?color(scale(n.usedCount)):"#3d5a80");
      const parts=[`${n.count}× befahren`];
      if(used) parts.push(`${n.usedCount}× Ein-/Ausstieg`);
      if(held) parts.push(`${n.heldCount}× gehalten`);
      if(passed) parts.push(`${n.passCount}× physische Durchfahrt`);
      L.circleMarker([n.lat,n.lon],{
        radius:used?5:(heldOnly?4:3),
        color:heldOnly?"#000":(used?"#1e293b":"#33475b"),
        weight:heldOnly?1.5:(used?2:1),
        fillColor:heldOnly?"#fff":col,
        fillOpacity:heldOnly?1:(used?.95:.75), bubblingMouseEvents:false})
        .bindTooltip(`<b>${esc(n.name)}</b><br>`+parts.join("<br>"))
        .on("click", ()=>openLens(n.sid)).addTo(overlay);
    });
  }

  // Lupe an einer Station: Fahrtbeziehungen der Fahrten, die den aktuellen
  // Filtern entsprechen; Arme in geografischer Richtung der Nachbarn.
  function lensRides(){
    const src=D();
    const T=src.trips||[], E=src.statEdges||[], S=src.statStops||[];
    const out=[];
    T.forEach((t,i)=>{ if(matchesTrip(t)) out.push({t, i, day:statsYmd(t), edges:E[i]||[], stops:S[i]||[]}); });
    return out;
  }
  function lensColor(line){
    const c=LCOL[line];
    if(c && c[0]) return c[0];
    if(isLineMode()){ const gid=GROUPS.indexOf(line); if(gid>=0) return groupColor(gid).bg; }
    return "#3d5a80";
  }
  function openLens(sid){
    const s=ST[sid]; if(!s) return;
    const T=D().trips||[];
    const list=lensRides();
    const at=L.latLng(s[0], s[1]);
    const el=lens.open({
      sid, list, parent:mapEl,
      byLine:lineEl.value==="__all__" || lineEl.value==="__none__",
      joins:y=>matchesTrip(T[y]),
      dirs:nb=>{
        const q=ST[nb]; if(!q) return [1,0];
        const a=map.project(at), b=map.project(L.latLng(q[0], q[1]));
        const dx=b.x-a.x, dy=b.y-a.y, l=Math.hypot(dx,dy)||1;
        return [dx/l, dy/l];
      },
      colorOf:m=>lensColor(m.line||(list[0]&&list[0].t.line)||""),
      target:{getBoundingClientRect(){
        const r=mapEl.getBoundingClientRect(), p=map.latLngToContainerPoint(at);
        return {left:r.left+p.x, top:r.top+p.y, width:0, height:0};
      }},
    });
    L.DomEvent.disableClickPropagation(el);
    L.DomEvent.disableScrollPropagation(el);
  }
  function lensOnMap(){ const el=lens.element(); return !!el && mapEl.contains(el); }

  let legendDiv=null;
  function updateLegend(){
    if(!legendDiv) return;
    const roles=`<br>
      <i style="background:#fff;width:10px;height:10px;border-radius:50%;border:1.5px solid #000;box-sizing:border-box"></i> gehalten<br>
      <i style="background:#3d5a80;width:8px;height:8px;border-radius:50%;opacity:.75"></i> nur physische Durchfahrt`;
    let html;
    if(isLocMode()){
      html=`<b>Baureihen</b><br>
        <span class="muted">Farbe = Baureihe<br>Badge = Baureihe<br>Pfeil = Fahrtrichtung<br>großer Punkt = Ein-/Ausstieg</span>`+roles;
    } else if(isLineMode()){
      html=`<b>Linien</b><br>
        <span class="muted">Farbe = Linienfarbe<br>Badge = Linie<br>Pfeil = Fahrtrichtung<br>großer Punkt = Ein-/Ausstieg</span>`+roles;
    } else if(isVehicleMode()){
      html=`<b>Fahrzeuge</b><br>
        <span class="muted">Farbe = Fahrzeug<br>Badge = Nummer<br>Pfeil = Fahrtrichtung<br>großer Punkt = Ein-/Ausstieg</span>`+roles;
    } else {
      html=`<b>Befahrungen</b><br>
        <i style="background:${color(0)}"></i> selten (${vMin}×)<br>
        <i style="background:${color(.5)}"></i> mittel<br>
        <i style="background:${color(1)}"></i> häufig (${vMax}×)<br>
        <span class="muted">Pfeil = Fahrtrichtung<br>großer Punkt = Ein-/Ausstieg</span>`+roles;
    }
    if(discEl && discEl.checked){
      html+=`<br><i style="background:${DISC}"></i> Entdeckte Kanten`;
    }
    legendDiv.innerHTML=html;
  }
  const lg=L.control({position:"bottomright"});
  lg.onAdd=function(){ legendDiv=L.DomUtil.create("div","legend"); updateLegend(); return legendDiv; };
  lg.addTo(map);

  // Alles neu berechnen und zeichnen (Init + bei Filterwechsel). Der erste
  // fitBounds passiert beim ersten Anzeigen des Tabs (siehe nav-Handler) auf
  // Basis der hier gefüllten mapBounds; die Ansicht bleibt beim Filtern erhalten.
  function drawAll(){
    computeVisible();
    mapBounds=[];
    vEdges.forEach(e=>{ mapBounds.push(e.a,e.b); });
    draw();
    updateLegend();
    const disc=(discEl && discEl.checked) ? " · "+vDiscEdges.length+" entdeckt" : "";
    countEl.textContent = isPathMode()
      ? vGroupCount+(isLineMode()?" Linien · ":isLocMode()?" Baureihen · ":" Fahrzeuge · ")+vEdges.length+" Segmente"+disc
      : vEdges.length+" Segmente"+disc;
  }
  applyScope();
  drawAll();
  onHomeChange(()=>{ applyScope(); drawAll(); });
  map.on("moveend", draw);
  // Lupe folgt der Station und den Filtern.
  function refreshLens(){ if(lensOnMap()) openLens(lens.sid()); }
  map.on("move zoom", ()=>{ if(lensOnMap()) lens.place(); });
  map.on("click", ()=>{ if(lensOnMap()) lens.close(); });
  onHomeChange(()=>{ if(lensOnMap()) lens.close(); });
  [modeEl,lineEl,locEl,vehEl,catEl,opEl,yearEl,dateFromEl,dateToEl,discEl].forEach(el=>{
    if(!el) return;
    el.onchange=()=>{ drawAll(); refreshLens(); };
    if(el===dateFromEl||el===dateToEl) el.oninput=el.onchange;
  });
  const FS_ENTER='<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M4 9V4h5v2H6v3H4zm11-5h5v5h-2V6h-3V4zM4 15h2v3h3v2H4v-5zm13 3v-3h2v5h-5v-2h3z"/></svg>';
  const FS_EXIT='<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M9 4H7v3H4v2h5V4zm8 0h-2v5h5V7h-3V4zM4 15v2h3v3h2v-5H4zm11 2h5v-2h-5v5h2v-3z"/></svg>';
  const fsCtrl=L.control({position:"topleft"});
  let fsLink=null;
  fsCtrl.onAdd=function(){
    const wrap=L.DomUtil.create("div", "leaflet-bar leaflet-control leaflet-control-fullscreen");
    fsLink=L.DomUtil.create("a", "map-fs-btn", wrap);
    fsLink.href="#";
    fsLink.role="button";
    fsLink.innerHTML=FS_ENTER;
    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.disableScrollPropagation(wrap);
    L.DomEvent.on(fsLink, "click", function(e){
      L.DomEvent.preventDefault(e);
      L.DomEvent.stop(e);
      if(mapFullscreenOn()) exitMapFullscreen();
      else enterMapFullscreen();
    });
    return wrap;
  };
  fsCtrl.addTo(map);
  function fsElement(){
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }
  function mapFullscreenOn(){
    return fsElement()===mapEl || mapEl.classList.contains("is-fullscreen");
  }
  function syncFsBtn(){
    if(!fsLink) return;
    const on=mapFullscreenOn();
    fsLink.title=on?"Vollbild beenden":"Vollbild";
    fsLink.setAttribute("aria-label", fsLink.title);
    fsLink.setAttribute("aria-pressed", on?"true":"false");
    fsLink.innerHTML=on?FS_EXIT:FS_ENTER;
  }
  function resizeMap(){
    setTimeout(()=>{ map.invalidateSize(); draw(); }, 60);
  }
  function enterMapFullscreen(){
    const req=mapEl.requestFullscreen || mapEl.webkitRequestFullscreen;
    if(!req){
      mapEl.classList.add("is-fullscreen");
      syncFsBtn();
      resizeMap();
      return;
    }
    const pending=req.call(mapEl);
    if(pending && pending.catch) pending.catch(()=>{
      mapEl.classList.add("is-fullscreen");
      syncFsBtn();
      resizeMap();
    });
  }
  function exitMapFullscreen(){
    if(mapEl.classList.contains("is-fullscreen")){
      mapEl.classList.remove("is-fullscreen");
      syncFsBtn();
      resizeMap();
      return;
    }
    const exit=document.exitFullscreen || document.webkitExitFullscreen;
    if(fsElement()===mapEl && exit) exit.call(document);
  }
  syncFsBtn();
  document.addEventListener("fullscreenchange", ()=>{ syncFsBtn(); resizeMap(); });
  document.addEventListener("webkitfullscreenchange", ()=>{ syncFsBtn(); resizeMap(); });
  document.addEventListener("keydown", e=>{
    if(e.key==="Escape" && mapEl.classList.contains("is-fullscreen")) exitMapFullscreen();
  });
  document.querySelectorAll(".navlist [data-tab]").forEach(b=>{
    b.addEventListener("click", ()=>{
      if(b.dataset.tab!=="map") exitMapFullscreen();
    });
  });
})();

