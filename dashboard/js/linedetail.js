// ---------- Linien-Detailseite ----------
const lineDetail=(function(){
  const backEl=document.getElementById("ldBack");
  const titleEl=document.getElementById("ldTitle");
  const subEl=document.getElementById("ldSub");
  const dateFromEl=document.getElementById("ldDateFrom");
  const dateToEl=document.getElementById("ldDateTo");
  const passLabelsEl=document.getElementById("ldPassLabels");
  const countEl=document.getElementById("ldCount");
  const bodyEl=document.getElementById("ldBody");
  const BIT_BOARDED=1, BIT_ALIGHTED=2, BIT_THROUGH=4, BIT_PASSED=8;
  const WEEKDAYS=["Mo","Di","Mi","Do","Fr","Sa","So"];
  const MONTHS=["Jan","Feb","Mär","Apr","Mai","Jun","Jul","Aug","Sep","Okt","Nov","Dez"];
  // Entweder eine Linie (current = Linien-Key) oder ein Unternehmen
  // (opScope = Operatorname, alle seine Linien).
  let current=null, opScope=null;
  // Layoutverfahren des Netzplans: "deflate" (Luft raus) oder "classic".
  let planEngine="deflate";
  try{ planEngine=localStorage.getItem("trwl-plan-engine")||"deflate"; }catch(e){}
  let tables=[];
  let opColors=new Map();
  // Ersatzfarben für Linien ohne HAFAS-Farbe in der Unternehmensansicht.
  const FALLBACK_COLORS=["#3d5a80","#e07a1f","#2a9d8f","#c1121f","#7b2cbf","#588157",
    "#b5838d","#1d4ed8","#a16207","#0f766e","#be185d","#4d7c0f"];
  function inScope(t){
    return opScope!=null ? lineOperator(t.line)===opScope : t.line===current;
  }

  function fmtN(v){
    if(v==null||v==="") return "—";
    return Number(v).toLocaleString("de-DE");
  }
  function fmtKm(v){ return fmtN(Math.round((Number(v)||0)*10)/10); }
  const stopName=lens.stopName;
  function edgeNames(a,b){
    const n=(D().statEdgeNames||{})[a+"\x1f"+b];
    return n||[stopName(a), stopName(b)];
  }
  function lineColor(){
    if(opScope!=null) return "var(--accent)";
    const c=(D().lineColors||{})[current];
    return c?c[0]:"var(--accent)";
  }
  function opLineColor(key){
    const c=(D().lineColors||{})[key];
    return c?[c[0], c[1]]:(opColors.get(key)||["var(--accent)","#fff"]);
  }
  // Farben der Linien eines Unternehmens: HAFAS-Farbe, sonst Ersatzpalette
  // in Reihenfolge der Fahrtenzahl.
  function assignOpColors(list){
    const n=new Map();
    for(const r of list) n.set(r.t.line, (n.get(r.t.line)||0)+1);
    const keys=[...n.keys()].sort((a,b)=>n.get(b)-n.get(a) || String(a).localeCompare(String(b),"de",{numeric:true}));
    opColors=new Map();
    let k=0;
    for(const key of keys){
      if((D().lineColors||{})[key]) continue;
      opColors.set(key, [FALLBACK_COLORS[k++%FALLBACK_COLORS.length], "#fff"]);
    }
  }
  function opBadgeHtml(key){
    const [bg,fg]=opLineColor(key);
    return `<span class="line-badge" style="background:${bg};color:${fg}">${esc(lineName(key)||"(ohne Linie)")}</span>`;
  }
  function badgeHtml(key){
    const c=(D().lineColors||{})[key];
    const st=c?` style="background:${c[0]};color:${c[1]}"`:"";
    return `<span class="line-badge ld-badge"${st}>${esc(lineName(key)||"(ohne Linie)")}</span>`;
  }

  function rides(){
    const src=D();
    const trips=src.trips||[], edges=src.statEdges||[], stops=src.statStops||[];
    const from=dateFromEl.value||"", to=dateToEl.value||"";
    const out=[];
    trips.forEach((t,i)=>{
      if(!inScope(t)) return;
      const d=statsYmd(t);
      if(from && d<from) return;
      if(to && d>to) return;
      out.push({t, i, day:d, edges:edges[i]||[], stops:stops[i]||[]});
    });
    out.sort((x,y)=>(x.t.date||"")<(y.t.date||"")?-1:(x.t.date||"")>(y.t.date||"")?1:0);
    return out;
  }
  // Gefahrene Folgen a→j→b je Linie, Durchbindungen innerhalb der Linie
  // eingeschlossen. Einträge [a, j, b, Linien-Key].
  function transitions(list){
    const out=[];
    const T=D().trips||[], all=D().statEdges||[];
    for(const r of list){
      const E=r.edges;
      for(let i=0;i+1<E.length;i++){
        if(String(E[i][1])===String(E[i+1][0])) out.push([String(E[i][0]), String(E[i][1]), String(E[i+1][1]), r.t.line]);
      }
      const y=lens.dubiNext(r.i);
      if(y!=null && T[y].line===r.t.line && E.length){
        const e=E[E.length-1], f=all[y][0];
        out.push([String(e[0]), String(e[1]), String(f[1]), r.t.line]);
      }
    }
    return out;
  }
  function vehiclesOf(t){
    return String(t.vehicles||"").split(",").map(s=>s.trim()).filter(Boolean);
  }

  // ---------- Kennzahlen ----------
  function kpiCards(list){
    let km=0, min=0, pts=0, delaySum=0, delayN=0, punctual=0;
    const stations=new Set(), edges=new Set(), locs=new Set(), vehs=new Set(), days=new Set();
    for(const r of list){
      const t=r.t;
      km+=Number(t.distanceKm)||0;
      min+=Number(t.durationMin)||0;
      pts+=Number(t.points)||0;
      if(t.delay!=null){ delayN++; delaySum+=t.delay; if(t.delay<=5) punctual++; }
      for(const st of r.stops) if(st[1]&(BIT_BOARDED|BIT_ALIGHTED|BIT_THROUGH)) stations.add(st[0]);
      for(const e of r.edges) edges.add(e[0]+"\x1f"+e[1]);
      if(t.locClass) locs.add(t.locClass);
      vehiclesOf(t).forEach(v=>vehs.add(v));
      days.add(r.day);
    }
    const first=list.length?list[0].day:"", last=list.length?list[list.length-1].day:"";
    const cards=[
      ["Fahrten", fmtN(list.length)],
      ...(opScope!=null?[["Linien", fmtN(new Set(list.map(r=>r.t.line)).size)]]:[]),
      ["Distanz", fmtKm(km)+" km"],
      ["Reisezeit", fmtDuration(min)],
      ["Punkte", fmtN(pts)],
      ["Ø je Fahrt", list.length?fmtKm(km/list.length)+" km":"—"],
      ["Ø Verspätung", delayN?fmtN(Math.round(delaySum/delayN*10)/10)+" min":"—"],
      ["Pünktlich ≤ 5 min", delayN?fmtN(Math.round(punctual/delayN*1000)/10)+" %":"—"],
      ["Tage", fmtN(days.size)],
      ["Stationen", fmtN(stations.size)],
      ["Kanten", fmtN(edges.size)],
      ["Baureihen", fmtN(locs.size)],
      ["Fahrzeuge", fmtN(vehs.size)],
      ["Erste Fahrt", fmtDate(first)||"—"],
      ["Letzte Fahrt", fmtDate(last)||"—"],
    ];
    return `<div class="cards ld-cards">${cards.map(c=>
      `<div class="card"><div class="v">${esc(c[1])}</div><div class="l">${esc(c[0])}</div></div>`).join("")}</div>`;
  }

  // ---------- Säulendiagramme ----------
  function columns(items, unit){
    const max=Math.max(1, ...items.map(it=>it.n));
    const showN=items.length<=24;
    const cols=items.map(it=>{
      const h=it.n?Math.max(2, Math.round(it.n/max*100)):0;
      const tip=`${it.title}: ${fmtN(it.n)} ${it.n===1?"Fahrt":"Fahrten"}`+(it.km!=null?` · ${fmtKm(it.km)} km`:"");
      const num=showN&&it.n?`<b>${fmtN(it.n)}</b>`:"";
      return `<div class="ld-col" title="${esc(tip)}"><div class="ld-col-bar">${num}<i style="height:${h}%"></i></div><span>${esc(it.label)}</span></div>`;
    }).join("");
    return `<div class="ld-cols${items.length>24?" dense":""}" role="img" aria-label="${esc(unit)}">${cols}</div>`;
  }
  function timeCharts(list){
    if(!list.length) return "";
    const byMonth=new Map(), wd=Array.from({length:7},()=>({n:0,km:0})), hr=Array.from({length:24},()=>({n:0,km:0}));
    for(const r of list){
      const km=Number(r.t.distanceKm)||0;
      const m=r.day.slice(0,7);
      const g=byMonth.get(m)||{n:0,km:0};
      g.n++; g.km+=km; byMonth.set(m,g);
      const dt=new Date(r.t.depPlanned||r.t.date);
      if(isNaN(dt)) continue;
      const w=(dt.getDay()+6)%7;
      wd[w].n++; wd[w].km+=km;
      hr[dt.getHours()].n++; hr[dt.getHours()].km+=km;
    }
    const months=[];
    const first=list[0].day.slice(0,7), last=list[list.length-1].day.slice(0,7);
    let [y,mo]=first.split("-").map(Number);
    const [ly,lm]=last.split("-").map(Number);
    while(y<ly || (y===ly && mo<=lm)){
      const key=y+"-"+String(mo).padStart(2,"0");
      const g=byMonth.get(key)||{n:0,km:0};
      months.push({label:mo===1||!months.length?MONTHS[mo-1]+" "+String(y).slice(2):MONTHS[mo-1],
        title:MONTHS[mo-1]+" "+y, n:g.n, km:g.km});
      mo++; if(mo>12){ mo=1; y++; }
    }
    const wdItems=wd.map((g,i)=>({label:WEEKDAYS[i], title:WEEKDAYS[i], n:g.n, km:g.km}));
    const hrItems=hr.map((g,i)=>({label:i%3===0?String(i):"", title:i+" Uhr", n:g.n, km:g.km}));
    return `<div class="ld-charts">
      <div class="panel ld-chart ld-chart-wide"><h3>Fahrten je Monat</h3>${columns(months,"Fahrten je Monat")}</div>
      <div class="panel ld-chart"><h3>Wochentag</h3>${columns(wdItems,"Fahrten je Wochentag")}</div>
      <div class="panel ld-chart"><h3>Abfahrtsstunde</h3>${columns(hrItems,"Fahrten je Abfahrtsstunde")}</div>
    </div>`;
  }

  // ---------- Sortierbare Tabellen ----------
  function table(title, cols, rows, sortKey, hint){
    const id=tables.length;
    tables.push({cols, rows, sortKey, asc:false});
    const h=hint?`<p class="hint">${esc(hint)}</p>`:"";
    return `<div class="panel ld-table"><h3>${esc(title)} <span class="muted">· ${fmtN(rows.length)}</span></h3>${h}
      <div class="stats-scroll"><table class="stats" data-ld-table="${id}"><thead></thead><tbody></tbody></table></div></div>`;
  }
  function cmp(a,b){
    if(a==null||a==="") return b==null||b===""?0:1;
    if(b==null||b==="") return -1;
    if(typeof a==="number"&&typeof b==="number") return a-b;
    return String(a).localeCompare(String(b),"de",{numeric:true});
  }
  function paintTable(el){
    const tb=tables[+el.dataset.ldTable];
    const col=tb.cols.find(c=>c.k===tb.sortKey);
    const rows=tb.rows.slice().sort((x,y)=>{
      const c=cmp(x[tb.sortKey], y[tb.sortKey]);
      const emptyX=x[tb.sortKey]==null||x[tb.sortKey]==="", emptyY=y[tb.sortKey]==null||y[tb.sortKey]==="";
      if(emptyX||emptyY) return c;
      return tb.asc?c:-c;
    });
    const max=col&&col.bar?Math.max(1,...tb.rows.map(r=>Number(r[col.k])||0)):1;
    el.tHead.innerHTML="<tr>"+tb.cols.map(c=>{
      const cls=[c.lbl?"lbl":""];
      if(c.k===tb.sortKey) cls.push(tb.asc?"sort-asc":"sort-desc");
      return `<th class="${cls.join(" ")}" data-k="${c.k}">${esc(c.label)}</th>`;
    }).join("")+"</tr>";
    el.tBodies[0].innerHTML=rows.map(r=>"<tr>"+tb.cols.map(c=>{
      const v=r[c.k];
      const txt=c.fmt?c.fmt(v,r):(v==null||v===""?"—":typeof v==="number"?fmtN(v):v);
      if(c.bar && c.k===tb.sortKey){
        const w=Math.round((Number(v)||0)/max*100);
        return `<td><div class="barcell"><span class="n">${esc(txt)}</span><div class="bar"><i style="width:${w}%;background:${lineColor()}"></i></div></div></td>`;
      }
      return `<td class="${c.lbl?"lbl":""}">${c.html?txt:esc(txt)}</td>`;
    }).join("")+"</tr>").join("");
    el.tHead.querySelectorAll("th").forEach(th=>th.onclick=()=>{
      const k=th.dataset.k;
      if(tb.sortKey===k) tb.asc=!tb.asc;
      else { tb.sortKey=k; tb.asc=!!(tb.cols.find(c=>c.k===k)||{}).lbl; }
      paintTable(el);
    });
  }

  function lineTable(list){
    const m=new Map();
    for(const r of list){
      const k=r.t.line||"";
      const g=m.get(k)||{key:k, line:lineName(k)||"(ohne Linie)", n:0, km:0, first:"", last:""};
      g.n++; g.km+=Number(r.t.distanceKm)||0;
      if(!g.first) g.first=r.day;
      g.last=r.day;
      m.set(k,g);
    }
    const total=list.length||1;
    const rows=[...m.values()].map(g=>({...g, km:Math.round(g.km*10)/10, pct:Math.round(g.n/total*1000)/10}));
    return table("Linien", [
      {k:"line", label:"Linie", lbl:true, html:true, fmt:(v,r)=>opBadgeHtml(r.key)},
      {k:"n", label:"Fahrten", bar:true},
      {k:"pct", label:"Anteil", fmt:v=>fmtN(v)+" %"},
      {k:"km", label:"km", bar:true},
      {k:"first", label:"Erste", fmt:v=>fmtDate(v)},
      {k:"last", label:"Letzte", fmt:v=>fmtDate(v)},
    ], rows, "n");
  }
  function locTable(list){
    const m=new Map();
    for(const r of list){
      const k=r.t.locClass||"";
      const g=m.get(k)||{loc:k||"(ohne)", n:0, km:0, first:"", last:""};
      g.n++; g.km+=Number(r.t.distanceKm)||0;
      if(!g.first) g.first=r.day;
      g.last=r.day;
      m.set(k,g);
    }
    const total=list.length||1;
    const rows=[...m.values()].map(g=>({...g, km:Math.round(g.km*10)/10, pct:Math.round(g.n/total*1000)/10}));
    return table("Baureihen", [
      {k:"loc", label:"Baureihe", lbl:true},
      {k:"n", label:"Fahrten", bar:true},
      {k:"pct", label:"Anteil", fmt:v=>fmtN(v)+" %"},
      {k:"km", label:"km", bar:true},
      {k:"first", label:"Erste", fmt:v=>fmtDate(v)},
      {k:"last", label:"Letzte", fmt:v=>fmtDate(v)},
    ], rows, "n");
  }
  function vehTable(list){
    const m=new Map();
    for(const r of list){
      for(const v of vehiclesOf(r.t)){
        const k=(r.t.locClass||"")+"\x1f"+v;
        const g=m.get(k)||{veh:v, loc:r.t.locClass||"", n:0, km:0, first:"", last:""};
        g.n++; g.km+=Number(r.t.distanceKm)||0;
        if(!g.first) g.first=r.day;
        g.last=r.day;
        m.set(k,g);
      }
    }
    const rows=[...m.values()].map(g=>({...g, km:Math.round(g.km*10)/10}));
    if(!rows.length) return "";
    return table("Fahrzeuge", [
      {k:"veh", label:"Wagen", lbl:true},
      {k:"loc", label:"Baureihe", lbl:true},
      {k:"n", label:"Fahrten", bar:true},
      {k:"km", label:"km", bar:true},
      {k:"first", label:"Erste", fmt:v=>fmtDate(v)},
      {k:"last", label:"Letzte", fmt:v=>fmtDate(v)},
    ], rows, "n");
  }
  function stationTable(list){
    const m=new Map();
    for(const r of list){
      for(const [sid,bits] of r.stops){
        const g=m.get(sid)||{name:stopName(sid), b:0, a:0, t:0, p:0};
        if(bits&BIT_BOARDED) g.b++;
        if(bits&BIT_ALIGHTED) g.a++;
        if(bits&BIT_THROUGH) g.t++;
        if(bits&BIT_PASSED) g.p++;
        m.set(sid,g);
      }
    }
    const rows=[...m.values()].map(g=>({...g, ba:g.b+g.a}));
    return table("Stationen", [
      {k:"name", label:"Station", lbl:true},
      {k:"ba", label:"Ein + Aus", bar:true},
      {k:"b", label:"Einstiege", bar:true},
      {k:"a", label:"Ausstiege", bar:true},
      {k:"t", label:"Gehalten", bar:true},
      {k:"p", label:"Durchfahren", bar:true},
    ], rows, "ba");
  }
  function relationTable(list){
    const m=new Map();
    for(const r of list){
      const t=r.t;
      const k=t.from+"\x1f"+t.to;
      const g=m.get(k)||{rel:(t.from||"—")+" → "+(t.to||"—"), n:0, km:0, min:0, minN:0, last:""};
      g.n++; g.km+=Number(t.distanceKm)||0;
      if(t.durationMin!=null){ g.min+=t.durationMin; g.minN++; }
      g.last=r.day;
      m.set(k,g);
    }
    const rows=[...m.values()].map(g=>({rel:g.rel, n:g.n, km:Math.round(g.km/g.n*10)/10,
      min:g.minN?Math.round(g.min/g.minN):null, last:g.last}));
    return table("Relationen", [
      {k:"rel", label:"Von → Nach", lbl:true},
      {k:"n", label:"Fahrten", bar:true},
      {k:"km", label:"Ø km"},
      {k:"min", label:"Ø Min"},
      {k:"last", label:"Zuletzt", fmt:v=>fmtDate(v)},
    ], rows, "n");
  }
  function edgeTable(list){
    const m=new Map();
    for(const r of list){
      for(const [a,b] of r.edges){
        const k=a+"\x1f"+b;
        let g=m.get(k);
        if(!g){
          const [na,nb]=edgeNames(a,b);
          g={from:na, to:nb, n:0, first:r.day, last:""};
          m.set(k,g);
        }
        g.n++; g.last=r.day;
      }
    }
    return table("Kanten", [
      {k:"from", label:"Von", lbl:true},
      {k:"to", label:"Nach", lbl:true},
      {k:"n", label:"Fahrten", bar:true},
      {k:"first", label:"Erste", fmt:v=>fmtDate(v)},
      {k:"last", label:"Letzte", fmt:v=>fmtDate(v)},
    ], [...m.values()], "n", "Gerichtet, mit Via-Stationen aus Kanten-Patches.");
  }
  function tripTable(list){
    const rows=list.map(r=>({
      date:r.t.date||"", from:r.t.from, to:r.t.to, loc:r.t.locClass||"",
      veh:r.t.vehicles||"", km:r.t.distanceKm, min:r.t.durationMin, delay:r.t.delay,
    }));
    return table("Fahrten", [
      {k:"date", label:"Datum", lbl:true, fmt:v=>fmtDateTime(v)},
      {k:"from", label:"Von", lbl:true},
      {k:"to", label:"Nach", lbl:true},
      {k:"loc", label:"Baureihe", lbl:true},
      {k:"veh", label:"Wagen", lbl:true},
      {k:"km", label:"km"},
      {k:"min", label:"Min"},
      {k:"delay", label:"Versp.", fmt:v=>v==null?"—":v>0?"+"+v:String(v)},
    ], rows, "date");
  }

  // ---------- Schematischer Netzplan ----------
  // Ungerichteter Graph aus allen befahrenen Kanten; Ketten zwischen
  // Knoten mit Grad ≠ 2 werden per Douglas-Peucker auf Formpunkte reduziert.
  // Diese Schlüsselknoten werden geografisch gestartet, iterativ auf
  // 45°-Richtungen gezogen und danach per lokaler Suche auf dem Raster
  // verbessert; jedes Teilstück ist ein oktilinearer Zug mit höchstens einem
  // Knick, Halte gleichmäßig verteilt.
  const U=34;
  const OP_PITCH=3.4, OP_GAP=1.8;
  // Ab dieser halben Bündelbreite (4 Linien je Richtung) wird ein Halt zum
  // abgerundeten Rechteck statt zum Kreis.
  const RECT_MIN_HALF=OP_GAP/2+4*OP_PITCH;
  const MAX_STRETCH=2.5;
  const DP_STOPS=1;
  const EVEN_W=10;
  const ORDER_MIN=1;
  const ORDER_W=3;
  const STRESS_ADJ=1;
  const ANCHOR_K=0.05;
  const ISLAND_GAP=3;
  const DEFLATE_Q=0.5, DEFLATE_MAX=3, OCT_ROUNDS=6, OCT_KEEP=0.75;
  const DEFLATE_CITY_Q=0.9;
  const PAIR_MIN_KM=0.1;
  // Stauchungsfeld: Umkreis der Nachbarn, Glättung, stärkste Stauchung
  // (Faktor-Untergrenze; kleiner lässt Äste übereinanderfallen).
  const FIELD_R_KM=2, FIELD_H_KM=1, FIELD_MIN=0.25, FIELD_SAMPLES=8;
  const OCT_PERP=1, OCT_LEN=0.1, OCT_ANCHOR=0.2;
  // Mindestabstand zwischen den Außenkanten zweier Haltestellenmarker (px).
  const MARK_GAP=15;
  // Reserve, weil gezeichnet wird auf den gerundeten Zügen.
  const MARK_SLACK=2;
  const PUSH_ROUNDS=40, PUSH_MS=800;

  const FORBIDDEN=1000;
  const MARK_HARD=FORBIDDEN, MARK_W=100;
  function geoOf(sid){
    const st=(D().stations||{})[sid];
    if(!st || st[0]==null || st[1]==null) return null;
    return [Number(st[1]), -Number(st[0])];
  }
  function buildGraph(list){
    const adj=new Map(), edges=new Map(), role=new Map();
    function node(s){ if(!adj.has(s)) adj.set(s,new Set()); return adj.get(s); }
    for(const r of list){
      for(const [sid,bits] of r.stops){
        const k=String(sid);
        const g=role.get(k)||{b:0,a:0,t:0,p:0};
        if(bits&BIT_BOARDED) g.b++;
        if(bits&BIT_ALIGHTED) g.a++;
        if(bits&BIT_THROUGH) g.t++;
        if(bits&BIT_PASSED) g.p++;
        role.set(k,g);
      }
      for(const [a0,b0] of r.edges){
        const a=String(a0), b=String(b0);
        if(a===b) continue;
        node(a).add(b); node(b).add(a);
        const k=a<b?a+"|"+b:b+"|"+a;
        const e=edges.get(k)||{a:a<b?a:b, b:a<b?b:a, ab:0, ba:0};
        if(a===e.a) e.ab++; else e.ba++;
        edges.set(k,e);
      }
    }
    return {adj, edges, role};
  }
  // Länge mit dem Kosinus der mittleren Breite gestaucht, damit Ost-West- und
  // Nord-Süd-Abstände gleich lang wirken (sonst wären Ost-West-Strecken in
  // Mitteleuropa um rund die Hälfte zu lang).
  function fillGeo(adj){
    const known=[...adj.keys()].map(geoOf).filter(Boolean);
    const kx=known.length?Math.cos(-known.reduce((x,g)=>x+g[1],0)/known.length*Math.PI/180):1;
    const geo=new Map();
    for(const s of adj.keys()){ const g=geoOf(s); if(g) geo.set(s,[g[0]*kx, g[1]]); }
    for(let pass=0; pass<20 && geo.size<adj.size; pass++){
      const add=[];
      for(const s of adj.keys()){
        if(geo.has(s)) continue;
        const ps=[...adj.get(s)].map(n=>geo.get(n)).filter(Boolean);
        if(ps.length) add.push([s,[ps.reduce((x,p)=>x+p[0],0)/ps.length, ps.reduce((y,p)=>y+p[1],0)/ps.length]]);
      }
      if(!add.length) break;
      add.forEach(([s,g],i)=>geo.set(s,[g[0]+i*1e-4,g[1]]));
    }
    let i=0;
    for(const s of adj.keys()) if(!geo.has(s)) geo.set(s,[i++*0.01,0]);
    return geo;
  }
  // Halteabstände: reine Durchfahrten zählen nur teilweise, damit Strecken
  // mit vielen Via-Stationen nicht unnötig lang werden.
  const PASS_UNIT=0.4;
  function stopUnit(role, sid){
    const r=role.get(sid);
    return r && (r.b||r.a||r.t) ? 1 : PASS_UNIT;
  }
  // Kumulierte Halteabstände entlang einer Haltefolge.
  function unitsAlong(role, stops){
    const c=[0];
    for(let i=1;i<stops.length;i++) c.push(c[i-1]+(stopUnit(role, stops[i-1])+stopUnit(role, stops[i]))/2);
    return c;
  }
  function chains(adj){
    const key=new Set([...adj.keys()].filter(s=>adj.get(s).size!==2));
    const seen=new Set();
    const ek=(a,b)=>a<b?a+"|"+b:b+"|"+a;
    const out=[];
    function walk(start){
      for(const n of adj.get(start)){
        if(seen.has(ek(start,n))) continue;
        const path=[start];
        let prev=start, cur=n;
        seen.add(ek(prev,cur));
        while(!key.has(cur)){
          path.push(cur);
          const next=[...adj.get(cur)].find(x=>x!==prev && !seen.has(ek(cur,x)));
          if(next==null) break;
          seen.add(ek(cur,next));
          prev=cur; cur=next;
        }
        path.push(cur);
        out.push(path);
      }
    }
    [...key].sort().forEach(walk);
    // Ringe ohne Schlüsselknoten
    for(const s of [...adj.keys()].sort()){
      if([...adj.get(s)].some(n=>!seen.has(ek(s,n)))){ key.add(s); walk(s); }
    }
    return out;
  }
  // Douglas-Peucker mit örtlicher Toleranz: ein Punkt bleibt, wenn er mehr
  // als tol oder mehr als DP_STOPS örtliche Halteabstände von der Sehne
  // abweicht. So behält die Stadt ihre Knicke, auf dem Land werden kleine
  // Schlenker geglättet.
  function douglasPeucker(pts, tol){
    const keep=new Set([0, pts.length-1]);
    const gap=(a,b)=>Math.hypot(pts[b][0]-pts[a][0], pts[b][1]-pts[a][1]);
    const sp=pts.map((_,k)=>{
      const l=k>0?gap(k-1,k):null, r=k<pts.length-1?gap(k,k+1):null;
      return l!=null&&r!=null?(l+r)/2:(l!=null?l:r)||tol;
    });
    function rec(i,j){
      if(j<=i+1) return;
      const [ax,ay]=pts[i], [bx,by]=pts[j];
      const dx=bx-ax, dy=by-ay, L=Math.hypot(dx,dy);
      let best=-1, bi=-1;
      for(let k=i+1;k<j;k++){
        const [px,py]=pts[k];
        const d=L?Math.abs(dx*(ay-py)-dy*(ax-px))/L:Math.hypot(px-ax,py-ay);
        const score=Math.max(d/tol, d/(DP_STOPS*Math.max(sp[k],1e-12)));
        if(score>best){ best=score; bi=k; }
      }
      if(best>1){ keep.add(bi); rec(i,bi); rec(bi,j); }
    }
    rec(0, pts.length-1);
    return keep;
  }
  function segmentsOf(chainList, geo){
    let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
    for(const g of geo.values()){
      minX=Math.min(minX,g[0]); maxX=Math.max(maxX,g[0]);
      minY=Math.min(minY,g[1]); maxY=Math.max(maxY,g[1]);
    }
    const tol=0.06*Math.hypot(maxX-minX, maxY-minY);
    let segs=[];
    for(const path of chainList){
      const keep=douglasPeucker(path.map(s=>geo.get(s)), tol);
      if(path[0]===path[path.length-1] && path.length>3){
        keep.add(Math.round((path.length-1)/3));
        keep.add(Math.round(2*(path.length-1)/3));
      }
      const idx=[...keep].sort((a,b)=>a-b);
      for(let i=0;i<idx.length-1;i++) segs.push(path.slice(idx[i], idx[i+1]+1));
    }
    // Parallele Teilstücke zwischen denselben Knoten in der Mitte teilen.
    for(let round=0; round<3; round++){
      const byPair=new Map();
      segs.forEach(s=>{
        const a=s[0], b=s[s.length-1];
        const k=a<b?a+"|"+b:b+"|"+a;
        if(!byPair.has(k)) byPair.set(k,[]);
        byPair.get(k).push(s);
      });
      let split=false;
      const next=[];
      for(const group of byPair.values()){
        group.sort((x,y)=>x.length-y.length);
        group.forEach((s,i)=>{
          if(i>0 && s.length>2){
            const m=Math.floor((s.length-1)/2) || 1;
            next.push(s.slice(0,m+1), s.slice(m));
            split=true;
          }else next.push(s);
        });
      }
      segs=next;
      if(!split) break;
    }
    return segs;
  }
  // Solllänge je Teilstück in Halteabständen: Sehnenlänge mal dem Maßstab
  // (Median der Halteabstände je Länge über alle Teilstücke), mindestens die
  // Halteabstände, höchstens MAX_STRETCH davon. target.geoLen ist die
  // ungekappte geografische Länge im selben Maßstab.
  function segTargets(segs, geo, segU){
    const info=segs.map((s,si)=>{
      let len=0;
      for(let i=1;i<s.length;i++){ const g=geo.get(s[i]), h=geo.get(s[i-1]); len+=Math.hypot(g[0]-h[0], g[1]-h[1]); }
      const a=geo.get(s[0]), b=geo.get(s[s.length-1]);
      return {len, chord:Math.hypot(b[0]-a[0], b[1]-a[1]), u:segU[si][segU[si].length-1]};
    });
    const ratios=info.filter(x=>x.len>0).map(x=>x.u/x.len).sort((a,b)=>a-b);
    const scale=ratios.length?ratios[Math.floor(ratios.length/2)]:1;
    const geoLen=info.map(x=>x.chord*scale);
    const target=info.map(x=>Math.min(Math.max(x.chord*scale, x.u), x.u*MAX_STRETCH));
    target.geoLen=geoLen;
    target.scale=scale;
    return target;
  }
  // Stress-Einbettung der Knotenpunkte (setzt P, gibt die Lage zurück):
  // verbundene Knoten im Abstand ihrer Solllänge, alle übrigen Paare im
  // geografischen Abstand (Maßstab scale), höchstens aber der Summe der
  // Solllängen entlang des kürzesten Wegs; Gewicht 1/Abstand² (verbundene
  // mal STRESS_ADJ). So bleibt die Lage der Knoten zueinander erhalten, während
  // haltereiche Teilstücke ihre Länge bekommen.
  function stressLayout(keyList, segs, geo, scale, target, P, minSep, pairDist, geoDir=false){
    const n=keyList.length;
    if(n<3) return null;
    const idx=new Map(keyList.map((k,i)=>[k,i]));
    // Kürzeste Wege über die Solllängen (Floyd-Warshall): gestauchte
    // Überlandstücke bleiben so auch für entferntere Paare gestaucht.
    const path=new Float64Array(n*n).fill(Infinity);
    for(let i=0;i<n;i++) path[i*n+i]=0;
    const adj=new Set();
    segs.forEach((s,si)=>{
      const i=idx.get(s[0]), j=idx.get(s[s.length-1]);
      if(i===j) return;
      const d=Math.min(path[i*n+j], target[si]);
      path[i*n+j]=path[j*n+i]=d;
      adj.add(i*n+j); adj.add(j*n+i);
    });
    for(let k=0;k<n;k++) for(let i=0;i<n;i++){
      const ik=path[i*n+k];
      if(ik===Infinity) continue;
      for(let j=0;j<n;j++){ const v=ik+path[k*n+j]; if(v<path[i*n+j]) path[i*n+j]=v; }
    }
    const D=new Float64Array(n*n), W=new Float64Array(n*n);
    for(let i=0;i<n;i++) for(let j=i+1;j<n;j++){
      const k=i*n+j;
      // Paare in verschiedenen Teilnetzen bleiben frei (siehe placeIslands).
      if(path[k]===Infinity) continue;
      let d;
      if(adj.has(k)) d=path[k];
      else{
        const a=geo.get(keyList[i]), b=geo.get(keyList[j]);
        d=Math.min(pairDist?pairDist(keyList[i], keyList[j]):Math.hypot(b[0]-a[0], b[1]-a[1])*scale, path[k]);
        if(minSep) d=Math.max(d, minSep(keyList[i], keyList[j]));
      }
      if(!(d>1e-6)) continue;
      D[k]=D[j*n+i]=d;
      W[k]=W[j*n+i]=(adj.has(k)?STRESS_ADJ:1)/(d*d);
    }
    const X=keyList.map(k=>[...P.get(k)]);
    const deadline=Date.now()+150;
    for(let it=0; it<200 && Date.now()<deadline; it++){
      for(let i=0;i<n;i++){
        let sx=0, sy=0, sw=0;
        const xi=X[i];
        for(let j=0;j<n;j++){
          const w=W[i*n+j];
          if(!w) continue;
          const xj=X[j], d=D[i*n+j];
          let dx=xi[0]-xj[0], dy=xi[1]-xj[1];
          // geoDir: verbundene Knoten in geografischer Richtung, nur die
          // Länge ist frei – so dreht ein ungleicher Maßstab keine Kante.
          if(geoDir && adj.has(i*n+j)){ const a=geo.get(keyList[i]), b=geo.get(keyList[j]); dx=a[0]-b[0]; dy=a[1]-b[1]; }
          const L=Math.hypot(dx,dy)||1e-9;
          sx+=w*(xj[0]+d*dx/L); sy+=w*(xj[1]+d*dy/L); sw+=w;
        }
        if(sw){ xi[0]=sx/sw; xi[1]=sy/sw; }
      }
    }
    keyList.forEach((k,i)=>P.set(k,[...X[i]]));
    return new Map(keyList.map((k,i)=>[k,[...X[i]]]));
  }
  // Teilnetze ohne Verbindung zum Rest rücken in ihrer geografischen
  // Richtung bis auf ISLAND_GAP Rastereinheiten an das schon Platzierte
  // heran (größtes Teilnetz zuerst, es bleibt stehen).
  function placeIslands(keyList, segs, geo, P){
    const parent=new Map(keyList.map(k=>[k,k]));
    const find=k=>{ while(parent.get(k)!==k){ parent.set(k, parent.get(parent.get(k))); k=parent.get(k); } return k; };
    for(const s of segs){ const a=find(s[0]), b=find(s[s.length-1]); if(a!==b) parent.set(a,b); }
    const comps=new Map();
    for(const k of keyList){ const r=find(k); if(!comps.has(r)) comps.set(r,[]); comps.get(r).push(k); }
    if(comps.size<2) return;
    const list=[...comps.values()].sort((a,b)=>b.length-a.length);
    const box=ks=>{
      let x0=Infinity, y0=Infinity, x1=-Infinity, y1=-Infinity;
      for(const k of ks){ const p=P.get(k); x0=Math.min(x0,p[0]); y0=Math.min(y0,p[1]); x1=Math.max(x1,p[0]); y1=Math.max(y1,p[1]); }
      return [x0,y0,x1,y1];
    };
    const centroid=(ks, m)=>{
      let x=0, y=0;
      for(const k of ks){ const p=m.get(k); x+=p[0]; y+=p[1]; }
      return [x/ks.length, y/ks.length];
    };
    const placed=[...list[0]];
    for(const comp of list.slice(1)){
      const gp=centroid(placed, geo), gc=centroid(comp, geo);
      let dx=gc[0]-gp[0], dy=gc[1]-gp[1];
      const L=Math.hypot(dx,dy)||1;
      dx/=L; dy/=L;
      const B=box(placed), cc=centroid(comp, P), pc=centroid(placed, P);
      const rel=comp.map(k=>{ const p=P.get(k); return [k, p[0]-cc[0], p[1]-cc[1]]; });
      const overlaps=(cx,cy)=>{
        for(const [,rx,ry] of rel){
          const x=cx+rx, y=cy+ry;
          if(x>B[0]-ISLAND_GAP && x<B[2]+ISLAND_GAP && y>B[1]-ISLAND_GAP && y<B[3]+ISLAND_GAP) return true;
        }
        return false;
      };
      let t=0;
      while(overlaps(pc[0]+dx*t, pc[1]+dy*t) && t<1e4) t+=0.25;
      for(const [k,rx,ry] of rel) P.set(k,[pc[0]+dx*t+rx, pc[1]+dy*t+ry]);
      placed.push(...comp);
    }
  }
  // Aufs ganzzahlige Raster; belegte Punkte weichen auf die nächste freie Stelle aus.
  function snapToGrid(keyList, P){
    const taken=new Set();
    for(const k of keyList){
      const p=P.get(k);
      const r0=[Math.round(p[0]), Math.round(p[1])];
      let best=null;
      for(let rad=0; !best && rad<20; rad++){
        for(let dx=-rad; dx<=rad && !best; dx++) for(let dy=-rad; dy<=rad && !best; dy++){
          if(Math.max(Math.abs(dx),Math.abs(dy))!==rad) continue;
          const id=(r0[0]+dx)+","+(r0[1]+dy);
          if(!taken.has(id)) best=[r0[0]+dx, r0[1]+dy];
        }
      }
      taken.add(best[0]+","+best[1]);
      P.set(k,best);
    }
  }
  // Verfahren „Luft raus“: Geografie als Grundlage, Kanten so weit
  // verkürzt, wie Halte und Marker es erlauben, danach in 45° gedrückt.
  // 1. Maßstab: DEFLATE_Q-Perzentil des Mindestbedarfs je Länge.
  // 2. Stress-Einbettung der Knotenpunkte: jede Kante ihre geografische
  //    Länge, höchstens DEFLATE_MAX × Mindestbedarf; übrige Paare ihr
  //    geografischer Abstand, höchstens der Weg durchs Netz.
  // 3. Jede Kante auf die nächste 45°-Richtung; die Knotenpunkte werden so
  //    gelöst, dass jede Kante Richtung und Länge hat (schwacher Zug zur
  //    Lage aus 2), mehrmals mit neu gewählten Richtungen; dann Raster.
  // 4. Schlanke Feinoptimierung (Richtung aus 3 streng, Knicke, Winkel,
  //    Lage der Knoten zueinander, Kreuzungen, Abstände, Marker) samt
  //    Platz-Einschieben.
  // city: Variante „Stadt“ – Maßstab aus DEFLATE_CITY_Q, Querplatz für
  // nicht verbundene Knotenpunkte, Richtungen in der Feinoptimierung fest.
  // Stauchungsfeld (Variante „Stadt“): je Halt der Maßstab, den sein Umfeld
  // braucht (Nachbarn im Umkreis FIELD_R_KM: Marker samt Abstand, auf
  // derselben Strecke mindestens ein Halteabstand). Größter Wert = Plan-
  // maßstab S, Faktor je Halt = eigener Bedarf / S, geglättet als Maximum
  // der mit dem Abstand abklingenden Faktoren (FIELD_H_KM). Längen sind die
  // geografische Strecke mal S mal dem mittleren Faktor entlang der Linie:
  // die Innenstadt steht am Anschlag, dünne Gegenden werden gestaucht.
  function compressionField(stops, geo, segs, segU, markOf){
    const ids=[...stops], minGeo=PAIR_MIN_KM/111, R=FIELD_R_KM/111, h=FIELD_H_KM/111;
    const along=new Map();
    segs.forEach((s,si)=>{
      const cu=segU[si];
      for(let i=0;i+1<s.length;i++){
        const a=s[i], b=s[i+1], k=a<b?a+"|"+b:b+"|"+a;
        along.set(k, Math.max(along.get(k)||0, cu[i+1]-cu[i]));
      }
    });
    const need=new Map(ids.map(x=>[x,0]));
    for(let i=0;i<ids.length;i++) for(let j=i+1;j<ids.length;j++){
      const a=ids[i], b=ids[j], ga=geo.get(a), gb=geo.get(b);
      const g=Math.hypot(gb[0]-ga[0], gb[1]-ga[1]);
      if(g<minGeo || g>R) continue;
      const k=a<b?a+"|"+b:b+"|"+a;
      const s=Math.max(markOf(a).r+markOf(b).r+(MARK_GAP+MARK_SLACK)/U, along.get(k)||0)/g;
      if(s>need.get(a)) need.set(a,s);
      if(s>need.get(b)) need.set(b,s);
    }
    let S=0;
    for(const v of need.values()) S=Math.max(S,v);
    if(!S) return null;
    const pts=ids.filter(x=>need.get(x)>0).map(x=>[geo.get(x), need.get(x)/S]);
    const f=(x,y)=>{
      let m=FIELD_MIN;
      for(const [g,fi] of pts){
        if(fi<=m) continue;
        const dx=g[0]-x, dy=g[1]-y, v=fi*Math.exp(-(dx*dx+dy*dy)/(2*h*h));
        if(v>m) m=v;
      }
      return m;
    };
    const len=(a,b)=>{
      const L=Math.hypot(b[0]-a[0], b[1]-a[1]);
      let m=0;
      for(let k=0;k<FIELD_SAMPLES;k++){ const t=(k+0.5)/FIELD_SAMPLES; m+=f(a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t); }
      return S*L*m/FIELD_SAMPLES;
    };
    return {S, f, len};
  }
  // Nächste 45°-Richtung je Kante (Oktant 0–7 von s[0] nach s[Ende]). Mit
  // dedup an keinem Knoten zwei Kanten in dieselbe Richtung: bei Doppelbelegung
  // weicht die Kante mit der größeren Abweichung auf die benachbarte
  // Richtung ihrer Seite aus, wenn die dort an beiden Enden frei ist.
  // prev: Oktanten der vorigen Runde; sie bleiben, solange die Kante nicht
  // mehr als OCT_KEEP Oktanten davon abweicht (sonst kippen Richtungen hin
  // und her).
  function octDirections(segs, P, prev, dedup, turnCounts){
    const step=Math.PI/4;
    const act=segs.map(s=>{ const p=P.get(s[0]), q=P.get(s[s.length-1]); return Math.atan2(q[1]-p[1], q[0]-p[0])/step; });
    const oct=act.map((a,si)=>{
      if(prev){ let d=a-prev[si]; d=((d%8)+12)%8-4; if(Math.abs(d)<=OCT_KEEP) return prev[si]; }
      return ((Math.round(a)%8)+8)%8;
    });
    const out=(si, v)=>v===segs[si][0]?oct[si]:(oct[si]+4)%8;
    const inc=new Map();
    segs.forEach((s,si)=>{
      if(s[0]===s[s.length-1]) return;
      for(const v of [s[0], s[s.length-1]]){ if(!inc.has(v)) inc.set(v,[]); inc.get(v).push(si); }
    });
    if(!dedup) return oct;
    const used=(v, o, skip)=>(inc.get(v)||[]).some(sj=>sj!==skip && out(sj, v)===o);
    for(let it=0; it<3; it++){
      let changed=false;
      for(const [v, list] of inc){
        for(let i=0;i<list.length;i++) for(let j=i+1;j<list.length;j++){
          const si=list[i], sj=list[j];
          if(out(si, v)!==out(sj, v)) continue;
          const dev=sk=>{ let d=act[sk]-oct[sk]; d=((d%8)+12)%8-4; return d; };
          // Durchgehende Strecke bleibt: Fahrten, die über v geradeaus in die
          // gegenüberliegende Kante weiterfahren.
          // Als gerade gilt eine Gegenkante mit mindestens 135° Unterschied
          // in der tatsächlichen Richtung.
          // An beiden Enden der Kante gezählt: läuft sie dort geradeaus
          // weiter, ist sie Teil einer durchgehenden Strecke.
          const actOut=(sk, w)=>w===segs[sk][0]?act[sk]:act[sk]+4;
          const straightAt=(sk, w)=>{
            let c=0;
            for(const sf of inc.get(w)||[]){
              if(sf===sk) continue;
              let d=actOut(sk, w)-actOut(sf, w);
              d=Math.abs(((d%8)+12)%8-4);
              if(d<3) continue;
              c+=(turnCounts&&turnCounts.get(w+"|"+Math.min(sk,sf)+"|"+Math.max(sk,sf)))||0;
            }
            return c;
          };
          // Länge der geraden Fortsetzung ab Ende w (Summe der Kantenlängen):
          // jeweils die befahrene Gegenkante mit den meisten Fahrten, bis
          // keine mehr da ist.
          const runFrom=(sk, w)=>{
            let n=0, cur=sk, at=w;
            const len=sf=>{ const p=P.get(segs[sf][0]), q=P.get(segs[sf][segs[sf].length-1]); return Math.hypot(q[0]-p[0], q[1]-p[1]); };
            const seen=new Set([sk]);
            for(;;){
              let best=-1, bc=0;
              for(const sf of inc.get(at)||[]){
                if(seen.has(sf)) continue;
                let d=actOut(cur, at)-actOut(sf, at);
                d=Math.abs(((d%8)+12)%8-4);
                if(d<3) continue;
                const c=(turnCounts&&turnCounts.get(at+"|"+Math.min(cur,sf)+"|"+Math.max(cur,sf)))||0;
                if(c>bc){ bc=c; best=sf; }
              }
              if(best<0) return n;
              n+=len(best); seen.add(best); cur=best;
              at=segs[best][0]===at?segs[best][segs[best].length-1]:segs[best][0];
            }
          };
          // Zuerst zählt, an wie vielen Enden es überhaupt gerade weitergeht,
          // dann wie weit die Strecke geradeaus durchläuft (Hauptachse), erst
          // dann die Zahl der Fahrten.
          const straight=sk=>{
            const a=segs[sk][0], b=segs[sk][segs[sk].length-1];
            const x=straightAt(sk, a), y=straightAt(sk, b);
            return [(x>0)+(y>0), runFrom(sk, a)+runFrom(sk, b), x+y];
          };
          const cmp=(p, q)=>p[0]-q[0]||p[1]-q[1]||p[2]-q[2];
          // Reihenfolge: die weniger durchgehende (bei Gleichstand die stärker
          // abweichende) Kante zu ihrer Seite, dann zur anderen; danach die
          // andere Kante ebenso.
          const c=cmp(straight(si), straight(sj));
          const first=c?(c<0?si:sj):(Math.abs(dev(si))>=Math.abs(dev(sj))?si:sj), second=first===si?sj:si;
          const tries=[];
          for(const mv of [first, second]){ const s=dev(mv)>=0?1:-1; tries.push([mv, s], [mv, -s]); }
          for(const [mv, step1] of tries){
            const a=segs[mv][0], b=segs[mv][segs[mv].length-1], prev=oct[mv];
            oct[mv]=((prev+step1)%8+8)%8;
            if(used(a, out(mv, a), mv) || used(b, out(mv, b), mv)){ oct[mv]=prev; continue; }
            changed=true;
            break;
          }
        }
      }
      if(!changed) break;
    }
    return oct;
  }
  // Nach dem Einrasten: Knoten, die über waagerechte Teilstücke verbunden
  // sind, auf dieselbe Zeile (Median), über senkrechte auf dieselbe Spalte –
  // sonst braucht ein fast waagerechter Zug einen schrägen Abschnitt. Eine
  // Gruppe wird nur ausgerichtet, wenn dabei kein Rasterpunkt doppelt wird.
  function alignAxes(keyList, segs, octs, P){
    for(const [axis, odd] of [[1, [0,4]], [0, [2,6]]]){
      const parent=new Map(keyList.map(k=>[k,k]));
      const find=k=>{ while(parent.get(k)!==k){ parent.set(k, parent.get(parent.get(k))); k=parent.get(k); } return k; };
      segs.forEach((s,si)=>{
        if(!odd.includes(octs[si])) return;
        const a=find(s[0]), b=find(s[s.length-1]);
        if(a!==b) parent.set(a,b);
      });
      const groups=new Map();
      for(const k of keyList){ const r=find(k); if(!groups.has(r)) groups.set(r,[]); groups.get(r).push(k); }
      for(const g of groups.values()){
        if(g.length<2) continue;
        const vals=g.map(k=>P.get(k)[axis]).sort((a,b)=>a-b);
        const target=vals[Math.floor(vals.length/2)];
        const occupied=new Set(keyList.filter(k=>!g.includes(k)).map(k=>P.get(k).join(",")));
        const moved=g.map(k=>{ const p=[...P.get(k)]; p[axis]=target; return p; });
        const ids=moved.map(p=>p.join(","));
        if(ids.some(id=>occupied.has(id)) || new Set(ids).size<ids.length) continue;
        g.forEach((k,i)=>P.set(k, moved[i]));
      }
    }
  }
  function layoutDeflate(segs, geo, segU, segHalf, markOf, turnPairs, city=false, turnCounts=null){
    const stops=new Set(); segs.forEach(s=>s.forEach(x=>stops.add(x)));
    const keys=new Set();
    segs.forEach(s=>{ keys.add(s[0]); keys.add(s[s.length-1]); });
    const keyList=[...keys];
    const need=segs.map((s,si)=>{
      const u=segU[si][segU[si].length-1];
      return Math.max(u, markOf(s[0]).r+markOf(s[s.length-1]).r+(MARK_GAP+MARK_SLACK)/U);
    });
    const chord=segs.map(s=>{
      const a=geo.get(s[0]), b=geo.get(s[s.length-1]);
      return Math.hypot(b[0]-a[0], b[1]-a[1]);
    });
    const ratios=segs.map((_,si)=>chord[si]>0?need[si]/chord[si]:null).filter(v=>v!=null).sort((a,b)=>a-b);
    const q=city?DEFLATE_CITY_Q:DEFLATE_Q;
    let scale=ratios.length?ratios[Math.min(ratios.length-1, Math.floor(ratios.length*q))]:1;
    const field=city?compressionField(stops, geo, segs, segU, markOf):null;
    if(field) scale=field.S;
    const want=field
      ? segs.map((s,si)=>Math.max(need[si], field.len(geo.get(s[0]), geo.get(s[s.length-1]))))
      : segs.map((_,si)=>Math.min(Math.max(chord[si]*scale, need[si]), need[si]*DEFLATE_MAX));
    // Startlage: Geografie einheitlich im Maßstab (beim Feld mal dem
    // mittleren Faktor der Knotenpunkte); die Stauchung je Gegend macht die
    // Einbettung.
    let s0=scale;
    if(field){
      const fs=keyList.map(k=>{ const g=geo.get(k); return field.f(g[0], g[1]); }).sort((a,b)=>a-b);
      s0=scale*fs[Math.floor(fs.length/2)];
    }
    const P=new Map(keyList.map(k=>{ const g=geo.get(k); return [k,[g[0]*s0, g[1]*s0]]; }));
    const minSep=city?(a,b)=>markOf(a).r+markOf(b).r+(MARK_GAP+MARK_SLACK)/U:null;
    stressLayout(keyList, segs, geo, scale, want, P, minSep, field?(a,b)=>field.len(geo.get(a), geo.get(b)):null, city);
    placeIslands(keyList, segs, geo, P);
    const Q=new Map(keyList.map(k=>[k,[...P.get(k)]]));
    const inc=new Map();
    segs.forEach((s,si)=>{
      if(s[0]===s[s.length-1]) return;
      for(const v of [s[0], s[s.length-1]]){ if(!inc.has(v)) inc.set(v,[]); inc.get(v).push(si); }
    });
    const step=Math.PI/4;
    let dirs=[], octs=null;
    for(let round=0; round<OCT_ROUNDS; round++){
      octs=octDirections(segs, P, city?octs:null, city, turnCounts);
      dirs=octs.map(o=>[Math.cos(o*step), Math.sin(o*step)]);
      // Je Knoten ein kleines lineares Ausgleichsproblem: quer zur
      // gewählten Richtung streng (OCT_PERP), Länge schwach (OCT_LEN), Zug
      // zur Lage nach Schritt 2 (OCT_ANCHOR).
      for(let sweep=0; sweep<60; sweep++){
        for(const k of keyList){
          const q=Q.get(k);
          let a11=OCT_ANCHOR, a12=0, a22=OCT_ANCHOR, r1=OCT_ANCHOR*q[0], r2=OCT_ANCHOR*q[1];
          for(const si of inc.get(k)||[]){
            const s=segs[si], o=P.get(k===s[0]?s[s.length-1]:s[0]), d=dirs[si], n=[-d[1], d[0]];
            const sgn=k===s[0]?-1:1;
            const nd=n[0]*o[0]+n[1]*o[1], dd=d[0]*o[0]+d[1]*o[1]+sgn*want[si];
            a11+=OCT_PERP*n[0]*n[0]+OCT_LEN*d[0]*d[0];
            a12+=OCT_PERP*n[0]*n[1]+OCT_LEN*d[0]*d[1];
            a22+=OCT_PERP*n[1]*n[1]+OCT_LEN*d[1]*d[1];
            r1+=OCT_PERP*n[0]*nd+OCT_LEN*d[0]*dd;
            r2+=OCT_PERP*n[1]*nd+OCT_LEN*d[1]*dd;
          }
          const det=a11*a22-a12*a12;
          if(Math.abs(det)>1e-12) P.set(k,[(a22*r1-a12*r2)/det, (a11*r2-a12*r1)/det]);
        }
      }
    }
    snapToGrid(keyList, P);
    if(city) alignAxes(keyList, segs, octs, P);
    const target=want.slice();
    target.geoLen=want.slice();
    target.scale=scale;
    const angles=dirs.map(d=>Math.atan2(d[1], d[0]));
    const opt=optimizeLayout(segs, P, geo, target, turnPairs, segU, segHalf, markOf, {lean:true, angles, fixDir:city});
    return {K:P, opt};
  }
  function layoutKeys(segs, geo, segU, target){
    const keys=new Set();
    segs.forEach(s=>{ keys.add(s[0]); keys.add(s[s.length-1]); });
    const units=si=>segU[si][segU[si].length-1];
    const ratios=segs.map((s,si)=>{
      const a=geo.get(s[0]), b=geo.get(s[s.length-1]);
      const d=Math.hypot(b[0]-a[0], b[1]-a[1]);
      return d>0?units(si)/d:null;
    }).filter(v=>v!=null).sort((x,y)=>x-y);
    const scale=ratios.length?ratios[Math.floor(ratios.length/2)]:1;
    const P=new Map();
    for(const k of keys){ const g=geo.get(k); P.set(k,[g[0]*scale, g[1]*scale]); }
    const keyList=[...keys];
    const anchor=stressLayout(keyList, segs, geo, scale, target, P);
    const step=Math.PI/4;
    // Auf 45°-Richtungen und Solllängen ziehen, schwach zurück zur Stress-Lage.
    for(let it=0; it<500; it++){
      const alpha=0.25*(1-it/600);
      if(anchor) for(const k of keyList){
        const p=P.get(k), q=anchor.get(k);
        p[0]+=(q[0]-p[0])*ANCHOR_K; p[1]+=(q[1]-p[1])*ANCHOR_K;
      }
      for(let si=0;si<segs.length;si++){
        const s=segs[si], a=s[0], b=s[s.length-1];
        if(a===b) continue;
        const pa=P.get(a), pb=P.get(b);
        const dx=pb[0]-pa[0], dy=pb[1]-pa[1];
        const len=Math.hypot(dx,dy)||1e-6;
        const L=target[si];
        const th=Math.round(Math.atan2(dy,dx)/step)*step;
        const cx=(L*Math.cos(th)-dx)/2*alpha, cy=(L*Math.sin(th)-dy)/2*alpha;
        pa[0]-=cx; pa[1]-=cy; pb[0]+=cx; pb[1]+=cy;
      }
      for(let i=0;i<keyList.length;i++){
        const p=P.get(keyList[i]);
        for(let j=i+1;j<keyList.length;j++){
          const q=P.get(keyList[j]);
          const dx=q[0]-p[0], dy=q[1]-p[1], d=Math.hypot(dx,dy);
          if(d<2){
            const f=(2-d)/2*0.5/(d||1);
            const ux=d?dx:0.7, uy=d?dy:0.7;
            p[0]-=ux*f; p[1]-=uy*f; q[0]+=ux*f; q[1]+=uy*f;
          }
        }
      }
      for(const k of keyList){
        const p=P.get(k);
        for(const s of segs){
          const a=s[0], b=s[s.length-1];
          if(a===k||b===k) continue;
          const pa=P.get(a), pb=P.get(b);
          const dx=pb[0]-pa[0], dy=pb[1]-pa[1], L2=dx*dx+dy*dy;
          if(!L2) continue;
          const t=Math.max(0,Math.min(1,((p[0]-pa[0])*dx+(p[1]-pa[1])*dy)/L2));
          const nx=p[0]-(pa[0]+t*dx), ny=p[1]-(pa[1]+t*dy), d=Math.hypot(nx,ny);
          if(d<1.2 && d>1e-6){
            const f=(1.2-d)*0.3/d;
            p[0]+=nx*f; p[1]+=ny*f;
            pa[0]-=nx*f*0.5; pa[1]-=ny*f*0.5; pb[0]-=nx*f*0.5; pb[1]-=ny*f*0.5;
          }
        }
      }
    }
    placeIslands(keyList, segs, geo, P);
    snapToGrid(keyList, P);
    return {P, scale};
  }
  // Oktilinearer Zug von p nach q; jeder Knick dreht um 45°.
  // Schräge Sehne: 0 = Diagonale zuerst, 1 = Diagonale zuletzt,
  // 2 = gerade–diagonal–gerade, 3 = diagonal–gerade–diagonal.
  // Achsparallele oder diagonale Sehne: 0 = direkt, 4/5 = Ausweichbogen
  // zur einen oder anderen Seite (Anfang und Ende um 45° gedreht).
  function octilinearShapes(p, q){
    const dx=q[0]-p[0], dy=q[1]-p[1], ax=Math.abs(dx), ay=Math.abs(dy), eps=1e-6;
    if(ax<eps && ay<eps) return [0];
    if(ax<eps || ay<eps || Math.abs(ax-ay)<eps) return [0,4,5];
    return [0,1,2,3];
  }
  function octilinear(p, q, shape){
    const dx=q[0]-p[0], dy=q[1]-p[1], ax=Math.abs(dx), ay=Math.abs(dy), eps=1e-6;
    const sx=Math.sign(dx), sy=Math.sign(dy);
    if(ax<eps && ay<eps) return [p,q];
    if(ax<eps || ay<eps){
      if(shape!==4 && shape!==5) return [p,q];
      const L=Math.max(ax,ay), h=Math.min(1, L/3);
      const u=[sx, sy], n=shape===4?[-u[1], u[0]]:[u[1], -u[0]];
      return [p, [p[0]+h*(u[0]+n[0]), p[1]+h*(u[1]+n[1])], [q[0]-h*(u[0]-n[0]), q[1]-h*(u[1]-n[1])], q];
    }
    if(Math.abs(ax-ay)<eps){
      if(shape!==4 && shape!==5) return [p,q];
      const h=Math.min(1, ax/2);
      return shape===4
        ?[p, [p[0]+h*sx, p[1]], [q[0], q[1]-h*sy], q]
        :[p, [p[0], p[1]+h*sy], [q[0]-h*sx, q[1]], q];
    }
    const m=Math.min(ax,ay);
    const diag=[sx*m, sy*m];
    const axis=ax>ay?[dx-diag[0], 0]:[0, dy-diag[1]];
    if(shape===1) return [p,[q[0]-diag[0], q[1]-diag[1]],q];
    if(shape===2){
      const a=[p[0]+axis[0]/2, p[1]+axis[1]/2];
      return [p, a, [a[0]+diag[0], a[1]+diag[1]], q];
    }
    if(shape===3){
      const a=[p[0]+diag[0]/2, p[1]+diag[1]/2];
      return [p, a, [a[0]+axis[0], a[1]+axis[1]], q];
    }
    return [p,[p[0]+diag[0], p[1]+diag[1]],q];
  }
  function segDist(p, a, b){
    const dx=b[0]-a[0], dy=b[1]-a[1], L2=dx*dx+dy*dy;
    const t=L2?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/L2)):0;
    return Math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy);
  }
  function polyDist(p, pts){
    let d=Infinity;
    for(let i=0;i<pts.length-1;i++) d=Math.min(d, segDist(p, pts[i], pts[i+1]));
    return d;
  }
  function orient(a,b,c){ return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]); }
  function properCross(a,b,c,d){
    const e=1e-9;
    const d1=orient(c,d,a), d2=orient(c,d,b), d3=orient(a,b,c), d4=orient(a,b,d);
    return ((d1>e&&d2<-e)||(d1<-e&&d2>e)) && ((d3>e&&d4<-e)||(d3<-e&&d4>e));
  }
  function collinearOverlap(a,b,c,d){
    const ux=b[0]-a[0], uy=b[1]-a[1], L=Math.hypot(ux,uy);
    const vx=d[0]-c[0], vy=d[1]-c[1], M=Math.hypot(vx,vy);
    if(L<1e-9 || M<1e-9) return false;
    if(Math.abs(ux*vy-uy*vx)/(L*M)>1e-6) return false;
    if(Math.abs(orient(a,b,c))/L>0.05) return false;
    const t0=((c[0]-a[0])*ux+(c[1]-a[1])*uy)/L, t1=((d[0]-a[0])*ux+(d[1]-a[1])*uy)/L;
    return Math.min(Math.max(t0,t1),L)-Math.max(Math.min(t0,t1),0)>1e-3;
  }
  // Lokale Suche auf dem Raster (Stott/Rodgers-artig): Kosten aus Mindest-
  // und Überlänge, Knicken, Winkeln an Knoten, Abweichung von der
  // geografischen Richtung, Kreuzungen, Überdeckungen und Abständen.
  // Haltestellenmarker als Halbachsen (Rasterabstände): a entlang der
  // Strecke, b quer dazu. Knotenpunkte haben keine eindeutige Richtung und
  // zählen als Kreis mit Radius r (beim Rechteck die halbe Diagonale).
  function markGap(m1, p1, d1, m2, p2, d2){
    const vx=p2[0]-p1[0], vy=p2[1]-p1[1], L=Math.hypot(vx,vy);
    const ux=L?vx/L:1, uy=L?vy/L:0;
    const h=(m,d)=>d?m.a*Math.abs(ux*d[0]+uy*d[1])+m.b*Math.abs(-ux*d[1]+uy*d[0]):m.r;
    return L-h(m1,d1)-h(m2,d2)-(MARK_GAP+MARK_SLACK)/U;
  }
  function markCost(gap){ return gap<0?MARK_HARD+MARK_W*(-gap):0; }
  // opts.lean: Richtung aus opts.angles statt Geografie und streng
  // gehalten, ohne gleichmäßige Streckung.
  function optimizeLayout(segs, P, geo, target, turnPairs, segU, segHalf, markOf, opts={}){
    const S=segs.map((s,si)=>{
      const a=s[0], b=s[s.length-1];
      const ga=geo.get(a), gb=geo.get(b);
      const gx=gb[0]-ga[0], gy=gb[1]-ga[1];
      let side=0;
      for(const m of s.slice(1,-1)){ const g=geo.get(m); side+=gx*(g[1]-ga[1])-gy*(g[0]-ga[0]); }
      const n=s.length-1, cu=segU[si], u=cu[n];
      const ideal=target[si], geoLen=Math.max(target.geoLen[si], 1e-3);
      return {a, b, n, u, cu, ideal, geoLen, half:segHalf[si], geoAng:opts.angles?opts.angles[si]:Math.atan2(gy,gx), side:Math.sign(side), bend:0,
        mk:s.slice(1,-1).map(markOf), ma:markOf(a), mb:markOf(b)};
    });
    // Geografische Abgangsrichtung eines Teilstücks am Knoten v (zum
    // benachbarten Halt) und ob zwei Teilstücke dort spitzer als 90° liegen.
    const geoOut=(si, v)=>{
      const s=segs[si], nb=s[0]===v?s[1]:s[s.length-2], g=geo.get(v), h=geo.get(nb);
      const L=Math.hypot(h[0]-g[0], h[1]-g[1])||1;
      return [(h[0]-g[0])/L, (h[1]-g[1])/L];
    };
    const geoSharp=(v, i, j)=>{ const a=geoOut(i, v), b=geoOut(j, v); return a[0]*b[0]+a[1]*b[1]>0.1; };
    const inc=new Map();
    S.forEach((s,i)=>{
      for(const v of [s.a,s.b]){
        if(!inc.has(v)) inc.set(v,[]);
        if(!inc.get(v).includes(i)) inc.get(v).push(i);
      }
    });
    function geom(s){
      const p=P.get(s.a), q=P.get(s.b);
      if(!octilinearShapes(p, q).includes(s.bend)) s.bend=0;
      const pts=octilinear(p, q, s.bend);
      s.pts=pts;
      s.bends=pts.length-2;
      s.len=polyLen(pts);
      s.st=[]; s.sd=[];
      for(let i=1;i<s.n;i++){ const at=pointAt(pts, s.len*s.cu[i]/s.u); s.st.push(at.p); s.sd.push(at.d); }
      let x0=Infinity, y0=Infinity, x1=-Infinity, y1=-Infinity;
      for(const t of pts){ x0=Math.min(x0,t[0]); y0=Math.min(y0,t[1]); x1=Math.max(x1,t[0]); y1=Math.max(y1,t[1]); }
      s.box=[x0,y0,x1,y1];
      const f=pts[1], l=pts[pts.length-2];
      const fl=Math.hypot(f[0]-p[0], f[1]-p[1])||1, ll=Math.hypot(l[0]-q[0], l[1]-q[1])||1;
      s.d0=[(f[0]-p[0])/fl, (f[1]-p[1])/fl];
      s.d1=[(l[0]-q[0])/ll, (l[1]-q[1])/ll];
      if(s.bends){
        const o=orient(p, q, pts[1]);
        s.bendSide=Math.sign(o);
      }
    }
    function segCost(s){
      if(s.a===s.b) return 0;
      let c=0;
      if(s.len<s.u-1e-6) c+=40*(s.u-s.len);
      else c+=1.5*Math.abs(s.len-s.ideal)/s.ideal;
      c+=s.bends;
      const p=P.get(s.a), q=P.get(s.b);
      let d=Math.abs(Math.atan2(q[1]-p[1], q[0]-p[0])-s.geoAng);
      if(d>Math.PI) d=2*Math.PI-d;
      // Abweichung von der geografischen Richtung: bis 22,5° frei, bis 45°
      // flach, bis 67,5° steil; darüber ist sie verboten (sonst tauschen
      // Knoten ihre Lage).
      const q8=Math.PI/8;
      // Verfahren „Luft raus“: Richtung aus dem 45°-Schritt streng halten.
      // Feste Richtungen: Drehen ist teurer als ein Markerverstoß, den löst
      // das Platz-Einschieben.
      if(opts.fixDir) c+=d<=q8/4?0:d<=q8/2?1+4*(d-q8/4)/(q8/4):10*FORBIDDEN;
      else if(opts.lean) c+=d<=q8/4?0:d<=2*q8?1+4*(d-q8/4)/(2*q8):FORBIDDEN;
      else c+=d<=2*q8?2*Math.max(0, d-q8)/(2*q8):d<=3*q8?1+8*(d-2*q8)/q8:FORBIDDEN;
      if(s.bends && s.side && s.bendSide!==s.side) c+=0.3;
      // Marker entlang des Teilstücks, Knotenpunkte an beiden Enden.
      const n=s.st.length;
      for(let i=0;i<=n;i++){
        const m1=i?s.mk[i-1]:s.ma, p1=i?s.st[i-1]:p, d1=i?s.sd[i-1]:null;
        const m2=i<n?s.mk[i]:s.mb, p2=i<n?s.st[i]:q, d2=i<n?s.sd[i]:null;
        c+=markCost(markGap(m1, p1, d1, m2, p2, d2));
      }
      return c;
    }
    function nodeCost(v){
      const list=inc.get(v)||[];
      const dirs=list.map(i=>S[i].a===v?S[i].d0:S[i].d1);
      let c=0;
      for(let i=0;i<dirs.length;i++) for(let j=i+1;j<dirs.length;j++){
        const dot=dirs[i][0]*dirs[j][0]+dirs[i][1]*dirs[j][1];
        const ang=Math.acos(Math.max(-1,Math.min(1,dot)));
        if(ang<0.1) c+=100;
        else if(dirs.length===2){
          // Knicke über 90° sind verboten.
          const turn=Math.PI-ang;
          c+=turn<0.1?0:turn<Math.PI/4+0.1?1:turn<Math.PI/2+0.1?3:FORBIDDEN;
        }else if(ang<Math.PI/4+0.1 && !opts.fixDir) c+=3; // bei festen Richtungen stehen die Winkel schon fest
      }
      // Gefahrene Übergänge an Abzweigen ebenso höchstens 90° – außer der
      // Übergang ist schon geografisch spitz (Gleisdreieck).
      for(const [i,j] of (turnPairs&&turnPairs.get(v))||[]){
        if(geoSharp(v, i, j)) continue;
        const di=S[i].a===v?S[i].d0:S[i].d1, dj=S[j].a===v?S[j].d0:S[j].d1;
        if(di[0]*dj[0]+di[1]*dj[1]>0.1) c+=FORBIDDEN;
      }
      return c;
    }
    // Hart unter 0,7 Halteabständen, weich bis 2,5 (Platz für Beschriftungen).
    function clear(d){ return d<0.7?25:d<2.5?2*(2.5-d)/1.8:0; }
    function pairCost(si, sj){
      const bi=si.box, bj=sj.box;
      const r=2.5+si.half+sj.half+MARK_GAP/U;
      if(bi[0]>bj[2]+r || bj[0]>bi[2]+r || bi[1]>bj[3]+r || bj[1]>bi[3]+r) return 0;
      let c=0;
      for(let x=0;x<si.pts.length-1;x++) for(let y=0;y<sj.pts.length-1;y++){
        const a=si.pts[x], b=si.pts[x+1], e=sj.pts[y], f=sj.pts[y+1];
        if(properCross(a,b,e,f)) c+=25;
        else if(collinearOverlap(a,b,e,f)) c+=60;
      }
      // Abstände zählen ab den Bündelrändern. Halte nahe einem gemeinsamen
      // Knoten liegen zwangsläufig dicht am Nachbarstück; dort regeln die
      // Winkelkosten am Knoten den Abstand.
      const hw=si.half+sj.half;
      const shared=[si.a, si.b].filter(v=>v===sj.a || v===sj.b).map(v=>P.get(v));
      const nearShared=t=>shared.some(q=>Math.hypot(t[0]-q[0], t[1]-q[1])<1.5+hw);
      for(const t of si.st) if(!nearShared(t)) c+=clear(polyDist(t, sj.pts)-hw);
      for(const t of sj.st) if(!nearShared(t)) c+=clear(polyDist(t, si.pts)-hw);
      for(const v of [si.a, si.b]) if(v!==sj.a && v!==sj.b) c+=clear(polyDist(P.get(v), sj.pts)-hw);
      for(const v of [sj.a, sj.b]) if(v!==si.a && v!==si.b) c+=clear(polyDist(P.get(v), si.pts)-hw);
      for(const t of si.st) for(const u of sj.st) if(!nearShared(t) && Math.hypot(t[0]-u[0], t[1]-u[1])<0.7+hw) c+=25;
      // Marker beider Teilstücke untereinander und gegen fremde Knotenpunkte.
      for(let x=0;x<si.st.length;x++){
        for(let y=0;y<sj.st.length;y++) c+=markCost(markGap(si.mk[x], si.st[x], si.sd[x], sj.mk[y], sj.st[y], sj.sd[y]));
        if(sj.a!==si.a && sj.a!==si.b) c+=markCost(markGap(si.mk[x], si.st[x], si.sd[x], sj.ma, P.get(sj.a), null));
        if(sj.b!==si.a && sj.b!==si.b) c+=markCost(markGap(si.mk[x], si.st[x], si.sd[x], sj.mb, P.get(sj.b), null));
      }
      for(let y=0;y<sj.st.length;y++){
        if(si.a!==sj.a && si.a!==sj.b) c+=markCost(markGap(sj.mk[y], sj.st[y], sj.sd[y], si.ma, P.get(si.a), null));
        if(si.b!==sj.a && si.b!==sj.b) c+=markCost(markGap(sj.mk[y], sj.st[y], sj.sd[y], si.mb, P.get(si.b), null));
      }
      return c;
    }
    // Benachbarte Teilstücke (gemeinsamer Knoten) sollen gegenüber ihrer
    // geografischen Länge ähnlich stark gestreckt sein: muss eine Strecke
    // wegen vieler Halte länger werden, wachsen die Querstrecken mit und die
    // Gegend behält ihr Seitenverhältnis. Stauchung (gekappte Überlandstücke)
    // wird nicht weitergegeben.
    const stretch=s=>Math.max(0, Math.log(Math.max(s.len, 0.5)/s.geoLen));
    function evenCost(i, set){
      if(opts.lean) return 0;
      let c=0;
      const si=S[i];
      for(const v of [si.a, si.b]) for(const j of inc.get(v)||[]){
        if(j===i || (set.has(j) && j<i)) continue;
        const d=stretch(si)-stretch(S[j]);
        c+=EVEN_W*d*d;
      }
      return c;
    }
    // Lage der Knoten zueinander: liegt ein Paar geografisch auf einer Achse
    // mehr als ORDER_MIN Rastereinheiten auseinander, im Plan aber
    // andersherum, kostet das (mehr, je weiter es geografisch auseinander ist).
    const keyList=[...inc.keys()];
    const gScaled=new Map(keyList.map(k=>{ const g=geo.get(k); return [k,[g[0]*target.scale, g[1]*target.scale]]; }));
    function orderCost(v){
      const gv=gScaled.get(v), pv=P.get(v);
      let c=0;
      for(const w of keyList){
        if(w===v) continue;
        const gw=gScaled.get(w), pw=P.get(w);
        for(let ax=0;ax<2;ax++){
          const g=gw[ax]-gv[ax];
          if(Math.abs(g)<=ORDER_MIN) continue;
          const p=pw[ax]-pv[ax];
          if(p*g<0) c+=ORDER_W*Math.min(1, (Math.abs(g)-ORDER_MIN)/ORDER_MIN);
        }
      }
      return c;
    }
    // Knotenpunkte untereinander (nicht direkt verbundene; verbundene prüft segCost).
    const keyMark=new Map();
    S.forEach(s=>{ keyMark.set(s.a, s.ma); keyMark.set(s.b, s.mb); });
    function keyMarkCost(v){
      let c=0;
      const pv=P.get(v), mv=keyMark.get(v), nb=nbrSet.get(v);
      for(const [w,mw] of keyMark){
        if(w===v || (nb && nb.has(w))) continue;
        c+=markCost(markGap(mv, pv, null, mw, P.get(w), null));
      }
      return c;
    }
    const nbrSet=new Map();
    S.forEach(s=>{
      for(const [x,y] of [[s.a,s.b],[s.b,s.a]]){ if(!nbrSet.has(x)) nbrSet.set(x,new Set()); nbrSet.get(x).add(y); }
    });
    function localCost(set){
      let c=0;
      const nodes=new Set();
      for(const i of set){ c+=segCost(S[i])+evenCost(i, set); nodes.add(S[i].a); nodes.add(S[i].b); }
      for(const v of nodes) c+=nodeCost(v)+orderCost(v)+keyMarkCost(v);
      for(const i of set) for(let j=0;j<S.length;j++){
        if(j===i || (set.has(j) && j<i)) continue;
        c+=pairCost(S[i], S[j]);
      }
      return c;
    }
    S.forEach(s=>{
      geom(s);
      if(s.bends && s.side && s.bendSide!==s.side){ s.bend=1; geom(s); }
    });
    // Je Teilstück die günstigste Zugform wählen.
    function shapePass(){
      let improved=false;
      S.forEach((s,i)=>{
        const shapes=octilinearShapes(P.get(s.a), P.get(s.b));
        if(shapes.length<2) return;
        const C=new Set([i]);
        const orig=s.bend;
        let best=orig, bestC=localCost(C)-1e-6;
        for(const sh of shapes){
          if(sh===orig) continue;
          s.bend=sh; geom(s);
          const c=localCost(C);
          if(c<bestC){ bestC=c; best=sh; }
        }
        s.bend=best; geom(s);
        if(best!==orig) improved=true;
      });
      return improved;
    }
    shapePass();
    const keys=[...inc.keys()];
    const moves=[];
    for(const dist of [1,2,3]) for(const [dx,dy] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]]) moves.push([dx*dist, dy*dist]);
    const wideMoves=[];
    for(let dx=-5;dx<=5;dx++) for(let dy=-5;dy<=5;dy++) if(dx||dy) wideMoves.push([dx,dy]);
    function freeAt(v, p, skip){
      for(const k of keys){
        if(k===v || k===skip) continue;
        const q=P.get(k);
        if(Math.abs(q[0]-p[0])<0.5 && Math.abs(q[1]-p[1])<0.5) return false;
      }
      return true;
    }
    const nbrs=new Map();
    for(const s of S){
      if(s.a===s.b) continue;
      for(const [x,y] of [[s.a,s.b],[s.b,s.a]]){
        if(!nbrs.has(x)) nbrs.set(x,new Set());
        nbrs.get(x).add(y);
      }
    }
    // Feste Richtungen (opts.fixDir) sind eine echte Regel: ein Zug, nach dem
    // eine betroffene Kante weiter als 5,6° von ihrer Richtung abweicht,
    // wird verworfen (statt gegen andere harte Kosten abgewogen).
    const dirOff=s=>{
      const p=P.get(s.a), q=P.get(s.b);
      let d=Math.abs(Math.atan2(q[1]-p[1], q[0]-p[0])-s.geoAng);
      return d>Math.PI?2*Math.PI-d:d;
    };
    const dirBroken=set=>opts.fixDir && [...set].some(i=>S[i].a!==S[i].b && dirOff(S[i])>Math.PI/32);
    function improve(deadline){
    for(let pass=0; pass<40 && Date.now()<deadline; pass++){
      let improved=false;
      for(const v of keys){
        const C=new Set(inc.get(v));
        const base=localCost(C);
        const orig=P.get(v);
        let best=null, bestC=base-1e-6;
        // Verbotene Richtung oder Knicke am Knoten oder an seinen Nachbarn:
        // weiter suchen.
        const stuck=[...C].some(i=>segCost(S[i])>=FORBIDDEN || nodeCost(S[i].a)>=FORBIDDEN || nodeCost(S[i].b)>=FORBIDDEN);
        for(const [dx,dy] of stuck?wideMoves:moves){
          const cand=[orig[0]+dx, orig[1]+dy];
          if(!freeAt(v, cand)) continue;
          P.set(v, cand);
          C.forEach(i=>geom(S[i]));
          if(dirBroken(C)) continue;
          const c=localCost(C);
          if(c<bestC){ bestC=c; best=cand; }
        }
        P.set(v, best||orig);
        C.forEach(i=>geom(S[i]));
        if(best){ improved=true; continue; }
        // Kein Einzelzug hilft: zusammen mit einem Nachbarknoten verschieben
        // (löst etwa zwei verdrehte Teilstücke am selben Knoten).
        for(const w of nbrs.get(v)||[]){
          const C2=new Set([...inc.get(v), ...inc.get(w)]);
          const ow=P.get(w);
          let b2=null, b2C=localCost(C2)-1e-6;
          for(const [dx,dy] of moves){
            const cv=[orig[0]+dx, orig[1]+dy], cw=[ow[0]+dx, ow[1]+dy];
            if(!freeAt(v, cv, w) || !freeAt(w, cw, v)) continue;
            P.set(v, cv); P.set(w, cw);
            C2.forEach(i=>geom(S[i]));
            if(dirBroken(C2)) continue;
            const c=localCost(C2);
            if(c<b2C){ b2C=c; b2=[cv, cw]; }
          }
          P.set(v, b2?b2[0]:orig); P.set(w, b2?b2[1]:ow);
          C2.forEach(i=>geom(S[i]));
          if(b2){ improved=true; break; }
        }
      }
      if(shapePass()) improved=true;
      if(!improved) break;
    }
    }
    improve(Date.now()+600);
    // Platz einschieben: bleibt ein Markerpaar zu nah, rückt alles jenseits
    // der Mittellinie zwischen beiden (waagerecht oder senkrecht) um ganze
    // Rastereinheiten weg; kreuzende Teilstücke werden länger. Danach kurz
    // nachoptimieren, bis kein Paar mehr zu nah ist.
    function worstMark(){
      const M=[];
      const seen=new Set();
      for(const s of S){
        for(let i=0;i<s.st.length;i++) M.push([s.mk[i], s.st[i], s.sd[i]]);
        if(!seen.has(s.a)){ seen.add(s.a); M.push([s.ma, P.get(s.a), null]); }
        if(!seen.has(s.b)){ seen.add(s.b); M.push([s.mb, P.get(s.b), null]); }
      }
      let worst=null;
      for(let i=0;i<M.length;i++) for(let j=i+1;j<M.length;j++){
        const p=M[i][1], q=M[j][1];
        if(Math.abs(p[0]-q[0])>3 || Math.abs(p[1]-q[1])>3) continue;
        const g=markGap(M[i][0], p, M[i][2], M[j][0], q, M[j][2]);
        if(g<0 && (!worst || g<worst.g)) worst={g, p, q, d:M[i][2]||M[j][2]};
      }
      return worst;
    }
    const pushUntil=Date.now()+PUSH_MS;
    for(let round=0; round<PUSH_ROUNDS && Date.now()<pushUntil; round++){
      const w=worstMark();
      if(!w) break;
      const ux=w.q[0]-w.p[0], uy=w.q[1]-w.p[1], L=Math.hypot(ux,uy);
      if(L<1e-6) break;
      // Schubrichtung: quer zur Strecke, wenn die Marker nebeneinander
      // liegen (Korridore bleiben gerade), sonst entlang der Strecke; ohne
      // Streckenrichtung (Knotenpunkte) nach dem Abstandsvektor.
      let base;
      if(w.d){
        const vx=ux/L, vy=uy/L, d=w.d;
        base=Math.abs(-vx*d[1]+vy*d[0])>=Math.abs(vx*d[0]+vy*d[1])?[-d[1], d[0]]:d;
      }else base=Math.abs(ux)>=Math.abs(uy)?[1,0]:[0,1];
      // Nur waagerecht oder senkrecht: eine diagonale Trennlinie würde
      // waagerechte und senkrechte Strecken schief ziehen.
      const th=Math.round(Math.atan2(base[1], base[0])/(Math.PI/2))*(Math.PI/2);
      let dir=[Math.round(Math.cos(th)), Math.round(Math.sin(th))];
      if(dir[0]*ux+dir[1]*uy<0) dir=[-dir[0], -dir[1]];
      const dl=Math.hypot(dir[0], dir[1]), cos=Math.max(0.3, (dir[0]*ux+dir[1]*uy)/(dl*L));
      const shift=Math.max(1, Math.ceil(-w.g/(dl*cos)));
      // Trennlinie zwischen beiden Markern dort, wo sie möglichst keine
      // Kante zerschneidet, die parallel zu ihr läuft (die würde schief).
      const proj=pt=>pt[0]*dir[0]+pt[1]*dir[1];
      const lo=Math.min(proj(w.p), proj(w.q)), hi=Math.max(proj(w.p), proj(w.q));
      const vals=[lo, hi, ...keys.map(k=>proj(P.get(k))).filter(x=>x>lo && x<hi)].sort((a,b)=>a-b);
      let mid=(lo+hi)/2, bestCut=Infinity;
      for(let i=0;i+1<vals.length;i++){
        if(vals[i+1]-vals[i]<1e-9) continue;
        const c=(vals[i]+vals[i+1])/2;
        let n=0;
        for(const s of S){
          const pa=P.get(s.a), pb=P.get(s.b);
          if((proj(pa)-c)*(proj(pb)-c)>=0) continue;
          const ex=pb[0]-pa[0], ey=pb[1]-pa[1], el=Math.hypot(ex,ey)||1;
          if(Math.abs(ex*dir[0]+ey*dir[1])/el<0.4) n++;
        }
        const score=n+Math.abs(c-(lo+hi)/2)*1e-3;
        if(score<bestCut){ bestCut=score; mid=c; }
      }
      for(const k of keys){
        const pk=P.get(k);
        if(proj(pk)>mid){ pk[0]+=shift*dir[0]; pk[1]+=shift*dir[1]; }
      }
      S.forEach(geom);
      improve(Math.min(pushUntil, Date.now()+40));
    }
    return S;
  }
  // Gefahrene Folgen a→j→b an Teilstückenden: Paare der beiden Teilstücke je Knoten.
  function turnPairsOf(list, segs){
    const endSeg=new Map();
    segs.forEach((s,si)=>{
      const n=s.length-1;
      endSeg.set(s[0]+"|"+s[1], si);
      endSeg.set(s[n]+"|"+s[n-1], si);
    });
    const out=new Map(), seen=new Set();
    for(const [a,j,b] of transitions(list)){
      if(a===b) continue;
      const si=endSeg.get(j+"|"+a), sj=endSeg.get(j+"|"+b);
      if(si==null || sj==null || si===sj) continue;
      const k=j+"|"+Math.min(si,sj)+"|"+Math.max(si,sj);
      if(seen.has(k)) continue;
      seen.add(k);
      if(!out.has(j)) out.set(j,[]);
      out.get(j).push([si,sj]);
    }
    return out;
  }
  // Gefahrene Folgen a→j→b je Knoten j und Teilstückpaar: Anzahl Fahrten.
  // Schlüssel "j|si|sj" mit si<sj.
  function turnCountsOf(list, segs){
    const endSeg=new Map();
    segs.forEach((s,si)=>{
      const n=s.length-1;
      endSeg.set(s[0]+"|"+s[1], si);
      endSeg.set(s[n]+"|"+s[n-1], si);
    });
    const out=new Map();
    for(const [a,j,b] of transitions(list)){
      if(a===b) continue;
      const si=endSeg.get(j+"|"+a), sj=endSeg.get(j+"|"+b);
      if(si==null || sj==null || si===sj) continue;
      const k=j+"|"+Math.min(si,sj)+"|"+Math.max(si,sj);
      out.set(k,(out.get(k)||0)+1);
    }
    return out;
  }
  function pointAt(pts, s){
    let acc=0;
    for(let i=0;i<pts.length-1;i++){
      const a=pts[i], b=pts[i+1], L=Math.hypot(b[0]-a[0], b[1]-a[1]);
      if(acc+L>=s-1e-9 || i===pts.length-2){
        const t=L?Math.min(1,Math.max(0,(s-acc)/L)):0;
        return {p:[a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t], d:L?[(b[0]-a[0])/L,(b[1]-a[1])/L]:[1,0], i};
      }
      acc+=L;
    }
    return {p:pts[0], d:[1,0], i:0};
  }
  function subPath(pts, s0, s1){
    const a=pointAt(pts,s0), b=pointAt(pts,s1);
    const out=[a.p];
    for(let i=a.i+1;i<=b.i;i++) out.push(pts[i]);
    out.push(b.p);
    return out;
  }
  function polyLen(pts){
    let L=0;
    for(let i=0;i<pts.length-1;i++) L+=Math.hypot(pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1]);
    return L;
  }
  const DIRS=Array.from({length:8},(_,i)=>[Math.cos(i*Math.PI/4), Math.sin(i*Math.PI/4)]);
  // Beschriftung als Kette von Prüfkreisen entlang der Textmitte, damit
  // gedrehte Texte nicht über ihr achsparalleles Rechteck kollidieren.
  let measureCtx=null;
  function textWidth(name, bold){
    if(!measureCtx){
      try{ measureCtx=document.createElement("canvas").getContext("2d"); }catch(e){}
    }
    if(!measureCtx) return name.length*(bold?7:6.4);
    const fam=getComputedStyle(document.body).fontFamily||"sans-serif";
    measureCtx.font=(bold?"700 ":"400 ")+"11px "+fam;
    return measureCtx.measureText(name).width;
  }
  // Quer zu einer waagerechten Strecke steht der Text um 45° gedreht;
  // alt dreht in die andere Richtung (−45° bzw. +45°).
  function labelGeom(name, p, v, bold, alt, off){
    const x=p[0]+v[0]*off, y=p[1]+v[1]*off;
    const w=textWidth(name, bold);
    let anchor, dy, rot=false, ux, uy, cx0, cy0;
    if(Math.abs(v[0])<0.3){
      rot=alt?45:-45;
      anchor=(v[1]<0)!==!!alt?"start":"end";
      dy="0.35em";
      const dir=anchor==="start"?1:-1;
      ux=dir*0.7071; uy=(alt?dir:-dir)*0.7071; cx0=x; cy0=y;
    }else{
      anchor=v[0]>0?"start":"end";
      const mid=Math.abs(v[1])<0.3;
      dy=mid?"0.35em":v[1]<0?"0":"0.8em";
      ux=anchor==="start"?1:-1; uy=0;
      cx0=x; cy0=mid?y:v[1]<0?y-4:y+5;
    }
    const pts=[];
    // Über das Textende hinaus prüfen, damit Nachbarn nicht zusammenlaufen;
    // der Anfang bleibt frei vom eigenen Haltepunkt.
    for(let t=3; t<=w+6; t+=5) pts.push([cx0+ux*t, cy0+uy*t]);
    if(!pts.length) pts.push([cx0,cy0]);
    const ex=cx0+ux*w, ey=cy0+uy*w;
    const box=[Math.min(cx0,ex)-7, Math.min(cy0,ey)-8, Math.max(cx0,ex)+7, Math.max(cy0,ey)+8];
    const tr=rot?` transform="rotate(${rot} ${x.toFixed(1)} ${y.toFixed(1)})"`:"";
    const attrs=`x="${x.toFixed(1)}" y="${y.toFixed(1)}" dy="${dy}" text-anchor="${anchor}"${tr}`;
    return {pts, box, attrs};
  }
  function makeGrid(cell){
    const m=new Map();
    const key=(i,j)=>i+","+j;
    return {
      add(x,y,r){
        const i=Math.floor(x/cell), j=Math.floor(y/cell);
        const k=key(i,j);
        if(!m.has(k)) m.set(k,[]);
        m.get(k).push([x,y,r]);
      },
      hits(x,y,r){
        const i=Math.floor(x/cell), j=Math.floor(y/cell);
        let n=0;
        for(let di=-1;di<=1;di++) for(let dj=-1;dj<=1;dj++){
          for(const o of m.get(key(i+di,j+dj))||[]){
            const d=Math.hypot(o[0]-x,o[1]-y);
            if(d<o[2]+r) n++;
          }
        }
        return n;
      },
    };
  }
  // Knicke als quadratische Kurve abrunden; at liefert je Eingangspunkt die
  // Bogenlänge seines Stellvertreters (Ecke = Kurvenmitte) auf dem runden Zug.
  function roundPath(pts, R){
    const out=[pts[0]], at=[0];
    let acc=0;
    function push(p){
      const q=out[out.length-1], L=Math.hypot(p[0]-q[0], p[1]-q[1]);
      if(L<1e-6) return;
      acc+=L; out.push(p);
    }
    for(let i=1;i<pts.length;i++){
      const v=pts[i];
      if(i===pts.length-1){ push(v); at.push(acc); break; }
      const u=pts[i-1], w=pts[i+1];
      const Li=Math.hypot(v[0]-u[0], v[1]-u[1]), Lo=Math.hypot(w[0]-v[0], w[1]-v[1]);
      const a=[(v[0]-u[0])/(Li||1), (v[1]-u[1])/(Li||1)], b=[(w[0]-v[0])/(Lo||1), (w[1]-v[1])/(Lo||1)];
      const th=Math.acos(Math.max(-1, Math.min(1, a[0]*b[0]+a[1]*b[1])));
      if(!Li || !Lo || th<1e-3){ push(v); at.push(acc); continue; }
      const t=Math.min(R*Math.tan(th/2), Li/2, Lo/2);
      const p1=[v[0]-a[0]*t, v[1]-a[1]*t], p2=[v[0]+b[0]*t, v[1]+b[1]*t];
      push(p1);
      const N=2*Math.max(2, Math.ceil(th/0.25));
      for(let k=1;k<=N;k++){
        const s=k/N, m=1-s;
        push([m*m*p1[0]+2*m*s*v[0]+s*s*p2[0], m*m*p1[1]+2*m*s*v[1]+s*s*p2[1]]);
        if(2*k===N) at.push(acc);
      }
    }
    return {pts:out, at};
  }
  function tangentAt(pts, k){
    for(let j=1;j<pts.length;j++){
      const a=pts[Math.max(0,k-j)], b=pts[Math.min(pts.length-1,k+j)];
      const L=Math.hypot(b[0]-a[0], b[1]-a[1]);
      if(L>1e-6) return [(b[0]-a[0])/L, (b[1]-a[1])/L];
    }
    return [1,0];
  }
  // Versatz quer zur Zeichenrichtung; positiv = rechts davon (y nach unten).
  function offsetPoly(pts, o){
    return pts.map((p,k)=>{ const d=tangentAt(pts,k); return [p[0]-d[1]*o, p[1]+d[0]*o]; });
  }
  function cubic(p0, p1, p2, p3, n){
    const out=[];
    for(let k=0;k<=n;k++){
      const s=k/n, m=1-s, a=m*m*m, b=3*m*m*s, c=3*m*s*s, d=s*s*s;
      out.push([a*p0[0]+b*p1[0]+c*p2[0]+d*p3[0], a*p0[1]+b*p1[1]+c*p2[1]+d*p3[1]]);
    }
    return out;
  }
  const pathD=pts=>"M"+pts.map(p=>p[0].toFixed(1)+" "+p[1].toFixed(1)).join("L");
  // Punkte auf pts zwischen den Bogenlängen s0 und s1, um offAt(s) quer
  // versetzt (positiv = rechts der Zeichenrichtung).
  function sampleOffset(pts, s0, s1, offAt){
    const out=[];
    let acc=0;
    for(let i=0;i<pts.length-1 && acc<=s1;i++){
      const a=pts[i], b=pts[i+1], L=Math.hypot(b[0]-a[0], b[1]-a[1]);
      if(!L) continue;
      const lo=Math.max(s0,acc), hi=Math.min(s1,acc+L);
      if(hi>=lo){
        const d=[(b[0]-a[0])/L, (b[1]-a[1])/L];
        const n=Math.max(1, Math.ceil((hi-lo)/3));
        for(let k=0;k<=n;k++){
          const s=lo+(hi-lo)*k/n, f=(s-acc)/L, o=offAt(s);
          out.push([a[0]+(b[0]-a[0])*f-d[1]*o, a[1]+(b[1]-a[1])*f+d[0]*o]);
        }
      }
      acc+=L;
    }
    return dropCollinear(out);
  }
  function dropCollinear(pts){
    if(pts.length<3) return pts;
    const out=[pts[0]];
    for(let i=1;i<pts.length-1;i++){
      const a=out[out.length-1], b=pts[i], c=pts[i+1];
      if(Math.hypot(b[0]-a[0], b[1]-a[1])<1e-3) continue;
      const L=Math.hypot(c[0]-a[0], c[1]-a[1])||1;
      if(Math.abs(orient(a,b,c))/L>0.05) out.push(b);
    }
    out.push(pts[pts.length-1]);
    return out;
  }
  // Unternehmensplan: je Linie und Fahrtrichtung ein Strich in Linienfarbe,
  // rechts der Fahrtrichtung (Rechtsverkehr). Je Richtung liegen die Linien
  // von der Mitte nach außen in fester Rangfolge (Fahrten im ganzen Netz),
  // damit sie sich selten kreuzen. Wechselt der Versatz an einem Halt,
  // gleitet der Strich kurz hinüber. Gefahrene Folgen einer Linie über
  // Zugenden werden Verbindungskurven.
  function opStrokes(list, routes, endAt, trimLen, band, strokes){
    const W=2.6, PITCH=OP_PITCH, GAP=OP_GAP, SEP="\x1e";
    const ek=(a,b)=>a<b?a+"|"+b:b+"|"+a;
    // Kante (ungerichtet) → Linie → {ab, ba}; ab = von kleinerer zu größerer ID.
    const per=new Map(), total=new Map();
    for(const r of list){
      const ln=r.t.line;
      for(const [a0,b0] of r.edges){
        const a=String(a0), b=String(b0);
        if(a===b) continue;
        const k=ek(a,b);
        if(!per.has(k)) per.set(k,new Map());
        const e=per.get(k).get(ln)||{ab:0, ba:0};
        if(a<b) e.ab++; else e.ba++;
        per.get(k).set(ln,e);
        total.set(ln,(total.get(ln)||0)+1);
      }
    }
    const rank=new Map([...total.keys()]
      .sort((x,y)=>total.get(y)-total.get(x) || String(x).localeCompare(String(y),"de",{numeric:true}))
      .map((k,i)=>[k,i]));
    const byRank=(x,y)=>rank.get(x)-rank.get(y);
    const count=(a,b,ln)=>{
      const e=(per.get(ek(a,b))||new Map()).get(ln);
      return e?(a<b?e.ab:e.ba):0;
    };
    // Je Zugkante: Abstand von der Mitte je Linie, getrennt für die Fahrt
    // in Zeichenrichtung (fwd) und dagegen (bwd).
    const routeOff=new Map();
    for(const rt of routes){
      routeOff.set(rt, rt.stops.slice(0,-1).map((s,i)=>{
        const u=rt.stops[i+1], ls=[...(per.get(ek(s,u))||new Map()).keys()].sort(byRank);
        const side=(from,to)=>{
          const m=new Map();
          ls.filter(ln=>count(from,to,ln)).forEach((ln,x)=>m.set(ln, GAP/2+(x+0.5)*PITCH));
          return m;
        };
        const fwd=side(s,u), bwd=side(u,s);
        const w=GAP/2+Math.max(fwd.size, bwd.size)*PITCH;
        band(s, w); band(u, w);
        return {fwd, bwd};
      }));
    }
    const seq=new Map(), trim=new Set();
    for(const [a,j,b,ln] of transitions(list)){
      if(a===b || a===j || j===b) continue;
      if(!endAt.has(j+"|"+a) || !endAt.has(j+"|"+b)) continue;
      const k=[a,j,b,ln].join(SEP);
      seq.set(k,(seq.get(k)||0)+1);
      trim.add(["in",a,j,ln].join(SEP)); trim.add(["out",j,b,ln].join(SEP));
    }
    for(const rt of routes){
      const {stops, pts, arcs}=rt, n=stops.length-1, offs=routeOff.get(rt);
      const lns=new Set();
      offs.forEach(o=>{ o.fwd.forEach((_,ln)=>lns.add(ln)); o.bwd.forEach((_,ln)=>lns.add(ln)); });
      for(const ln of [...lns].sort(byRank)){
        const col=opLineColor(ln)[0], name=lineName(ln)||"(ohne Linie)";
        for(const dir of [1,-1]){
          // Versatz in Zeichenrichtung: rechts (+) für fwd, links (−) für bwd.
          const off=e=>{ const m=dir>0?offs[e].fwd:offs[e].bwd; return m.has(ln)?dir*m.get(ln):null; };
          for(let i0=0;i0<n;){
            if(off(i0)==null){ i0++; continue; }
            let i1=i0+1;
            while(i1<n && off(i1)!=null) i1++;
            const t=trimLen(rt), a=stops[i0], b=stops[i1];
            // Anfang/Ende in Fahrtrichtung: dir>0 von a nach b, sonst von b nach a.
            const cut0=i0===0 && trim.has((dir>0?["out",a,stops[1],ln]:["in",stops[1],a,ln]).join(SEP));
            const cut1=i1===n && trim.has((dir>0?["in",stops[n-1],b,ln]:["out",b,stops[n-1],ln]).join(SEP));
            const ramp=e=>Math.min(10, 0.35*(arcs[e+1]-arcs[e]));
            const offAt=s=>{
              let e=i0;
              while(e<i1-1 && s>=arcs[e+1]) e++;
              const base=off(e);
              if(e>i0){
                const prev=off(e-1), r=ramp(e);
                if(prev!==base && s<arcs[e]+r){ const mid=(prev+base)/2; return mid+(base-mid)*(s-arcs[e])/r; }
              }
              if(e+1<i1){
                const nx=off(e+1), r=ramp(e);
                if(nx!==base && s>arcs[e+1]-r){ const mid=(nx+base)/2; return mid+(base-mid)*(arcs[e+1]-s)/r; }
              }
              return base;
            };
            const ns=[];
            for(let e=i0;e<i1;e++) ns.push(dir>0?count(stops[e],stops[e+1],ln):count(stops[e+1],stops[e],ln));
            const lo=Math.min(...ns), hi=Math.max(...ns);
            const from=stopName(dir>0?a:b), to=stopName(dir>0?b:a);
            strokes.push({pts:sampleOffset(pts, arcs[i0]+(cut0?t:0), arcs[i1]-(cut1?t:0), offAt), w:W, color:col,
              tip:`${name}: ${from} → ${to}: ${lo===hi?fmtN(lo):fmtN(lo)+"–"+fmtN(hi)}×`});
            i0=i1;
          }
        }
      }
    }
    // Endpunkt des Strichs von/nach j (Nachbar nb) samt Fahrtrichtung.
    function end(j, nb, incoming, ln){
      const {rt, start}=endAt.get(j+"|"+nb);
      const offs=routeOff.get(rt), e=offs[start?0:offs.length-1];
      // Fährt die Linie in Zeichenrichtung des Zugs?
      const fwd=start!==incoming;
      const o=(fwd?e.fwd:e.bwd).get(ln);
      if(o==null) return null;
      const t=trimLen(rt), at=pointAt(rt.pts, start?t:rt.L-t);
      const td=fwd?at.d:[-at.d[0], -at.d[1]];
      return {p:[at.p[0]-td[1]*o, at.p[1]+td[0]*o], d:td};
    }
    for(const [k,c] of seq){
      const [a,j,b,ln]=k.split(SEP);
      const s=end(j, a, true, ln), e=end(j, b, false, ln);
      if(!s || !e) continue;
      const L=Math.hypot(e.p[0]-s.p[0], e.p[1]-s.p[1]), h=0.45*L;
      const p=cubic(s.p, [s.p[0]+s.d[0]*h, s.p[1]+s.d[1]*h], [e.p[0]-e.d[0]*h, e.p[1]-e.d[1]*h], e.p, 10);
      strokes.push({pts:p, w:W, color:opLineColor(ln)[0],
        tip:`${lineName(ln)||"(ohne Linie)"}: ${stopName(a)} → ${stopName(j)} → ${stopName(b)}: ${fmtN(c)}×`});
    }
  }
  function opLegend(list){
    const n=new Map();
    for(const r of list) n.set(r.t.line,(n.get(r.t.line)||0)+1);
    const keys=[...n.keys()].sort((a,b)=>n.get(b)-n.get(a) || String(a).localeCompare(String(b),"de",{numeric:true}));
    const ln="var(--muted)";
    return `<div class="ld-legend">${keys.map(k=>`<span title="${fmtN(n.get(k))} ${n.get(k)===1?"Fahrt":"Fahrten"}">${opBadgeHtml(k)}</span>`).join("")}</div>
      <div class="ld-legend">
        <span><svg width="22" height="14"><path d="M0 5.2H22M0 8.8H22" stroke="${ln}" stroke-width="2.6"/><circle class="ld-n used" cx="11" cy="7" r="5.5"/></svg> Ein-/Ausstieg</span>
        <span><svg width="22" height="14"><path d="M0 5.2H22M0 8.8H22" stroke="${ln}" stroke-width="2.6"/><rect class="ld-n held" x="8.6" y="1.5" width="4.8" height="11" rx="2.4"/></svg> gehalten</span>
        <span><svg width="22" height="14"><path d="M0 5.2H22M0 8.8H22" stroke="${ln}" stroke-width="2.6"/><line class="ld-n pass" x1="11" y1="3" x2="11" y2="11"/></svg> nur durchfahren</span>
        <span>je Linie und Fahrtrichtung ein Strich in Linienfarbe, rechts gefahren; gemeinsame Abschnitte nebeneinander</span>
      </div>`;
  }
  function netPlan(list){
    const {adj, edges, role}=buildGraph(list);
    if(!edges.size) return `<div class="panel ld-plan"><h3>Netzplan</h3><p class="hint">Kein Laufweg.</p></div>`;
    const geo=fillGeo(adj);
    const segs=segmentsOf(chains(adj), geo);
    // Halbe Bündelbreite je Kante in Rasterabständen: Unternehmensplan je
    // Richtung ein Strich pro Linie, Linienplan höchstens ein Strich je Richtung.
    const opMode=opScope!=null;
    const edgeHalf=new Map();
    {
      const per=new Map();
      for(const r of list) for(const [a0,b0] of r.edges){
        const a=String(a0), b=String(b0);
        if(a===b) continue;
        const k=a<b?a+"|"+b:b+"|"+a;
        if(!per.has(k)) per.set(k,[new Set(), new Set()]);
        per.get(k)[a<b?0:1].add(r.t.line);
      }
      for(const [k,[f,g]] of per) edgeHalf.set(k, (opMode?OP_GAP/2+Math.max(f.size,g.size)*OP_PITCH:6)/U);
    }
    const segU=segs.map(s=>unitsAlong(role, s));
    const segHalf=segs.map(s=>{
      let h=0;
      for(let i=0;i+1<s.length;i++){ const a=s[i], b=s[i+1]; h=Math.max(h, edgeHalf.get(a<b?a+"|"+b:b+"|"+a)||0); }
      return h;
    });
    // Markerform wie beim Zeichnen (Halbachsen in Rasterabständen).
    const halfAtStop=new Map();
    for(const [k,h] of edgeHalf) for(const s of k.split("|")) halfAtStop.set(s, Math.max(halfAtStop.get(s)||0, h*U));
    const markOf=sid=>{
      const r=role.get(sid)||{b:0,a:0,t:0,p:0}, bw=halfAtStop.get(sid)||3;
      const used=!!(r.b||r.a), held=!!r.t, deg=adj.get(sid).size;
      let a, b;
      if(used || (held && deg!==2)){
        if(bw>=RECT_MIN_HALF){ a=deg===2?(used?5.5:4):bw+1.5; b=bw+1.5; }
        else a=b=Math.max(used?(deg===2?5.5:6.5):4, bw+1.5);
      }else if(held){ a=2.4; b=bw+1.5; }
      else{ a=0.8; b=bw; }
      const rect=(used || (held && deg!==2)) && bw>=RECT_MIN_HALF;
      return {a:a/U, b:b/U, r:(rect?Math.hypot(a,b):Math.max(a,b))/U};
    };
    let K, opt;
    if(planEngine==="deflate" || planEngine==="city"){
      ({K, opt}=layoutDeflate(segs, geo, segU, segHalf, markOf, turnPairsOf(list, segs), planEngine==="city", turnCountsOf(list, segs)));
    }
    else{
      const target=segTargets(segs, geo, segU);
      K=layoutKeys(segs, geo, segU, target).P;
      opt=optimizeLayout(segs, K, geo, target, turnPairsOf(list, segs), segU, segHalf, markOf);
    }
    const color=opMode?"var(--fg)":lineColor();
    // Teilstücke an Knoten vom Grad 2 wieder zu durchgehenden Zügen verbinden,
    // damit auch dort die Kurve rund bleibt.
    const segPts=opt.map(o=>o.pts.map(p=>[p[0]*U, p[1]*U]));
    const ends=new Map();
    segs.forEach((s,si)=>{
      for(const k of [s[0], s[s.length-1]]){ if(!ends.has(k)) ends.set(k,[]); ends.get(k).push(si); }
    });
    const joinable=k=>adj.get(k).size===2 && ends.get(k).length===2 && ends.get(k)[0]!==ends.get(k)[1];
    const taken=new Set(), routes=[];
    segs.forEach((_,si0)=>{
      if(taken.has(si0)) return;
      taken.add(si0);
      const parts=[{si:si0, rev:false}];
      for(;;){
        const l=parts[parts.length-1], s=segs[l.si], t=l.rev?s[0]:s[s.length-1];
        if(!joinable(t)) break;
        const nx=ends.get(t).find(x=>x!==l.si);
        if(taken.has(nx)) break;
        taken.add(nx); parts.push({si:nx, rev:segs[nx][0]!==t});
      }
      for(;;){
        const f=parts[0], s=segs[f.si], h=f.rev?s[s.length-1]:s[0];
        if(!joinable(h)) break;
        const nx=ends.get(h).find(x=>x!==f.si);
        if(taken.has(nx)) break;
        taken.add(nx); parts.unshift({si:nx, rev:segs[nx][segs[nx].length-1]!==h});
      }
      const stops=[], base=[], keyIdx=[], keyStop=[];
      parts.forEach((pt,j)=>{
        let s=segs[pt.si], q=segPts[pt.si];
        if(pt.rev){ s=[...s].reverse(); q=[...q].reverse(); }
        if(!j){ stops.push(s[0]); base.push(q[0]); keyIdx.push(0); keyStop.push(0); }
        for(let i=1;i<q.length;i++) base.push(q[i]);
        for(let i=1;i<s.length;i++) stops.push(s[i]);
        keyIdx.push(base.length-1); keyStop.push(stops.length-1);
      });
      const r=roundPath(base, 1.2*U);
      const arcs=new Array(stops.length), cu=unitsAlong(role, stops);
      for(let j=0;j<keyStop.length-1;j++){
        const s0=r.at[keyIdx[j]], s1=r.at[keyIdx[j+1]], i0=keyStop[j], i1=keyStop[j+1];
        for(let i=i0;i<=i1;i++) arcs[i]=s0+(s1-s0)*(cu[i]-cu[i0])/(cu[i1]-cu[i0]);
      }
      routes.push({stops, pts:r.pts, arcs, L:polyLen(r.pts)});
    });
    // Je Fahrtrichtung ein Strich rechts der Mitte (Rechtsverkehr).
    const GAP=1.8, TRIM=15;
    const maxDir=Math.max(1,...[...edges.values()].map(e=>Math.max(e.ab, e.ba)));
    const wOf=n=>n?1.6+3.4*Math.sqrt(n/maxDir):0;
    const offOf=n=>GAP/2+wOf(n)/2;
    const ek=(a,b)=>a<b?a+"|"+b:b+"|"+a;
    const dirCount=(a,b)=>{ const e=edges.get(ek(a,b)); return e?(a===e.a?e.ab:e.ba):0; };
    // Fahrtfolgen über Zugenden hinweg (a→j→b) werden zu Übergangskurven.
    const endAt=new Map();
    routes.forEach(rt=>{
      const n=rt.stops.length-1;
      endAt.set(rt.stops[0]+"|"+rt.stops[1], {rt, start:true});
      endAt.set(rt.stops[n]+"|"+rt.stops[n-1], {rt, start:false});
    });
    const seq=new Map();
    for(const [a,j,b] of opMode?[]:transitions(list)){
      if(a===b || a===j || j===b) continue;
      if(!endAt.has(j+"|"+a) || !endAt.has(j+"|"+b)) continue;
      const k=a+"|"+j+"|"+b;
      seq.set(k,(seq.get(k)||0)+1);
    }
    const trimmed=new Set();
    for(const k of seq.keys()){
      const [a,j,b]=k.split("|");
      trimmed.add("in:"+a+">"+j); trimmed.add("out:"+j+">"+b);
    }
    const trimLen=rt=>Math.min(TRIM, 0.4*(rt.arcs[1]-rt.arcs[0]), 0.4*(rt.arcs[rt.arcs.length-1]-rt.arcs[rt.arcs.length-2]));
    // Endpunkt eines gerichteten Strichs von/nach j samt Fahrtrichtung.
    function strokeEnd(j, nb, incoming){
      const {rt, start}=endAt.get(j+"|"+nb);
      const t=trimLen(rt), at=pointAt(rt.pts, start?t:rt.L-t);
      const fwd=start!==incoming;
      const td=fwd?at.d:[-at.d[0], -at.d[1]];
      const o=offOf(incoming?dirCount(nb,j):dirCount(j,nb));
      return {p:[at.p[0]-td[1]*o, at.p[1]+td[0]*o], d:td};
    }
    const strokes=[], bandAt=new Map(), pos=new Map(), dirsAt=new Map(), dirTo=new Map();
    const band=(s,v)=>bandAt.set(s, Math.max(bandAt.get(s)||0, v));
    let cx=0, cy=0;
    for(const p of K.values()){ cx+=p[0]; cy+=p[1]; }
    cx/=K.size; cy/=K.size;
    function addDir(s, d){
      if(!dirsAt.has(s)) dirsAt.set(s,[]);
      dirsAt.get(s).push(d);
    }
    for(const rt of routes){
      const {stops, pts, arcs, L}=rt, n=stops.length-1;
      // Beschriftungsseite je Zug vom Schwerpunkt weg; die Normale selbst
      // gilt je Halt, damit sie auch hinter einem Knick quer steht.
      const mid=pointAt(pts, L/2);
      const side=(-mid.d[1])*(mid.p[0]-cx*U)+mid.d[0]*(mid.p[1]-cy*U)<0?-1:1;
      stops.forEach((sid,i)=>{
        const at=pointAt(pts, arcs[i]);
        if(i===0||i===n){
          const d=i===0?pointAt(pts, Math.min(L, 1)).d:pointAt(pts, Math.max(0, L-1)).d;
          addDir(sid, i===0?d:[-d[0],-d[1]]);
          if(!pos.has(sid)) pos.set(sid,{p:at.p, d});
        }else{
          pos.set(sid,{p:at.p, n:[-at.d[1]*side, at.d[0]*side], d:at.d});
        }
        // Abgangsrichtung je Nachbar, kurz hinter dem Halt gemessen
        for(const k of [i-1, i+1]){
          if(k<0 || k>n) continue;
          const q=pointAt(pts, k>i?Math.min(arcs[i]+8, arcs[k]):Math.max(arcs[i]-8, arcs[k])).p;
          const dx=q[0]-at.p[0], dy=q[1]-at.p[1], L=Math.hypot(dx,dy);
          if(L>1e-6) dirTo.set(sid+"|"+stops[k], [dx/L, dy/L]);
        }
      });
      for(const dir of opMode?[]:[1,-1]){
        const cnt=i=>dir>0?dirCount(stops[i], stops[i+1]):dirCount(stops[i+1], stops[i]);
        for(let i=0;i<n;i++){
          const w=GAP/2+wOf(cnt(i));
          band(stops[i], w); band(stops[i+1], w);
        }
        for(let i0=0;i0<n;){
          let i1=i0+1;
          while(i1<n && cnt(i1)===cnt(i0)) i1++;
          const c=cnt(i0);
          if(c){
            const a=stops[i0], b=stops[i1];
            const cut0=i0===0 && trimmed.has(dir>0?"out:"+a+">"+stops[1]:"in:"+stops[1]+">"+a);
            const cut1=i1===n && trimmed.has(dir>0?"in:"+stops[n-1]+">"+b:"out:"+b+">"+stops[n-1]);
            const t=trimLen(rt);
            let p=subPath(pts, arcs[i0]+(cut0?t:0), arcs[i1]-(cut1?t:0));
            if(dir<0) p=p.reverse();
            const from=stopName(dir>0?a:b), to=stopName(dir>0?b:a);
            strokes.push({pts:offsetPoly(p, offOf(c)), w:wOf(c), tip:`${from} → ${to}: ${fmtN(c)}×`});
          }
          i0=i1;
        }
      }
    }
    for(const [k,c] of seq){
      const [a,j,b]=k.split("|");
      const s=strokeEnd(j, a, true), e=strokeEnd(j, b, false);
      const L=Math.hypot(e.p[0]-s.p[0], e.p[1]-s.p[1]), h=0.45*L;
      const p=cubic(s.p, [s.p[0]+s.d[0]*h, s.p[1]+s.d[1]*h], [e.p[0]-e.d[0]*h, e.p[1]-e.d[1]*h], e.p, 10);
      strokes.push({pts:p, w:Math.min(wOf(dirCount(a,j)), wOf(dirCount(j,b))), tip:`${stopName(a)} → ${stopName(j)} → ${stopName(b)}: ${fmtN(c)}×`});
    }
    if(opMode) opStrokes(list, routes, endAt, trimLen, band, strokes);
    const lines=strokes.map(s=>`<path d="${pathD(s.pts)}" stroke-width="${s.w.toFixed(1)}"${s.color?` stroke="${s.color}"`:""}><title>${esc(s.tip)}</title></path>`).join("");
    const showPass=passLabelsEl.checked;
    const nodes=[], labels=[], hits=[];
    let bx0=Infinity, by0=Infinity, bx1=-Infinity, by1=-Infinity;
    function grow(b){ bx0=Math.min(bx0,b[0]); by0=Math.min(by0,b[1]); bx1=Math.max(bx1,b[2]); by1=Math.max(by1,b[3]); }
    const grid=makeGrid(24), nodeGrid=makeGrid(24), labelGrid=makeGrid(24);
    for(const s of strokes){
      for(let i=0;i<s.pts.length-1;i++){
        const a=s.pts[i], b=s.pts[i+1], L=Math.hypot(b[0]-a[0], b[1]-a[1]);
        const n=Math.max(1, Math.ceil(L/5));
        for(let k=0;k<n;k++) grid.add(a[0]+(b[0]-a[0])*k/n, a[1]+(b[1]-a[1])*k/n, s.w/2+1);
      }
    }
    const order=[];
    for(const [sid,info] of [...pos.entries()].sort((x,y)=>x[0]<y[0]?-1:1)){
      const p=info.p, bw=bandAt.get(sid)||3;
      grow([p[0]-10,p[1]-10,p[0]+10,p[1]+10]);
      const r=role.get(sid)||{b:0,a:0,t:0,p:0};
      const used=!!(r.b||r.a), held=!!r.t, passOnly=!used&&!held;
      const deg=adj.get(sid).size;
      const name=stopName(sid);
      const tipParts=[name];
      if(r.b) tipParts.push(`Einstiege ${fmtN(r.b)}`);
      if(r.a) tipParts.push(`Ausstiege ${fmtN(r.a)}`);
      if(r.t) tipParts.push(`gehalten ${fmtN(r.t)}`);
      if(r.p) tipParts.push(`durchfahren ${fmtN(r.p)}`);
      const tip=`<title>${esc(tipParts.join("\n"))}</title>`;
      const xy=`${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
      const ang=(Math.atan2(info.d[1], info.d[0])*180/Math.PI).toFixed(1);
      let rad;
      if((used || (held && deg!==2)) && bw>=RECT_MIN_HALF){
        // Breites Bündel: abgerundetes Rechteck statt eines riesigen Kreises.
        // Jeder Ast trägt sein Bündel bei: quer zur Laufrichtung info.d so
        // weit wie die Bündel entlang ihr, entlang so weit wie die Bündel,
        // die quer dazu ankommen (T-Kreuzung).
        let A=used?5.5:4, B=0;
        for(const nb of adj.get(sid)){
          const dir=dirTo.get(sid+"|"+nb), h=(edgeHalf.get(sid<nb?sid+"|"+nb:nb+"|"+sid)||0)*U;
          if(!dir || !h) continue;
          const nx=-dir[1], ny=dir[0];
          A=Math.max(A, h*Math.abs(nx*info.d[0]+ny*info.d[1])+1.5);
          B=Math.max(B, h*Math.abs(-nx*info.d[1]+ny*info.d[0])+1.5);
        }
        B=Math.max(B, used?5.5:4);
        const rx=Math.min(used?3:2.5, A, B);
        rad=Math.max(A, B);
        nodes.push(`<rect class="ld-n big ${used?"used":"held"}" x="${(-A).toFixed(1)}" y="${(-B).toFixed(1)}" width="${(2*A).toFixed(1)}" height="${(2*B).toFixed(1)}" rx="${rx}" transform="translate(${xy}) rotate(${ang})">${tip}</rect>`);
      }else if(used || (held && deg!==2)){
        rad=Math.max(used?(deg===2?5.5:6.5):4, bw+1.5);
        nodes.push(`<circle class="ld-n ${used?"used":"held"}" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${rad.toFixed(1)}">${tip}</circle>`);
      }else if(held){
        // Querbalken über beide Richtungsstriche
        rad=bw+1.5;
        nodes.push(`<rect class="ld-n held" x="-2.4" y="${(-rad).toFixed(1)}" width="4.8" height="${(2*rad).toFixed(1)}" rx="2.4" transform="translate(${xy}) rotate(${ang})">${tip}</rect>`);
      }else{
        rad=bw;
        nodes.push(`<line class="ld-n pass" x1="0" y1="${(-rad).toFixed(1)}" x2="0" y2="${rad.toFixed(1)}" transform="translate(${xy}) rotate(${ang})">${tip}</line>`);
      }
      hits.push(`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${Math.max(9, rad+3).toFixed(1)}" data-sid="${esc(sid)}">${tip}</circle>`);
      nodeGrid.add(p[0], p[1], rad+1);
      if(passOnly && !showPass) continue;
      order.push({sid, info, name, used, passOnly, prio:used?0:held?1:2, deg, off:rad+4});
    }
    order.sort((x,y)=>x.prio-y.prio || (y.deg!==2)-(x.deg!==2));
    for(const o of order){
      const p=o.info.p;
      const cands=[];
      if(o.info.n){
        const n=o.info.n;
        cands.push(n, [-n[0],-n[1]]);
        if(Math.abs(n[0])<0.3) cands.push([...n, 1], [-n[0],-n[1], 1]);
      }else{
        const ds=dirsAt.get(o.sid)||[];
        const scored=DIRS.map((c,i)=>{
          const minAng=ds.length?Math.min(...ds.map(d=>Math.acos(Math.max(-1,Math.min(1,c[0]*d[0]+c[1]*d[1]))))):Math.PI;
          return {c, s:minAng+(i===0?0.01:0)};
        }).sort((x,y)=>y.s-x.s);
        scored.forEach(x=>cands.push(x.c));
      }
      DIRS.forEach(c=>{ if(!cands.some(x=>Math.abs(x[0]-c[0])<1e-6&&Math.abs(x[1]-c[1])<1e-6&&!x[2])) cands.push(c); });
      DIRS.forEach(c=>{ if(Math.abs(c[0])<0.3 && !cands.some(x=>Math.abs(x[0]-c[0])<1e-6&&Math.abs(x[1]-c[1])<1e-6&&x[2])) cands.push([...c, 1]); });
      // Fremde Texte und Halte wiegen schwerer als eine gestreifte Strecke.
      let best=null, bestScore=Infinity;
      cands.forEach((v,ci)=>{
        const g=labelGeom(o.name, p, v, o.used, !!v[2], o.off);
        let score=ci*0.4;
        for(const q of g.pts){
          score+=12*labelGrid.hits(q[0], q[1], 5.5)+12*nodeGrid.hits(q[0], q[1], 5.5)+3*grid.hits(q[0], q[1], 5.5);
        }
        if(score<bestScore){ bestScore=score; best=g; }
      });
      for(const q of best.pts) labelGrid.add(q[0], q[1], 5.5);
      const cls=o.used?"ld-l used":o.passOnly?"ld-l pass":"ld-l";
      labels.push(`<text class="${cls}" data-sid="${esc(o.sid)}" ${best.attrs}>${esc(o.name)}</text>`);
      grow(best.box);
    }
    const pad=8;
    const vx=bx0-pad, vy=by0-pad, vw=bx1-bx0+2*pad, vh=by1-by0+2*pad;
    const svg=`<svg class="ld-svg" viewBox="${vx.toFixed(1)} ${vy.toFixed(1)} ${vw.toFixed(1)} ${vh.toFixed(1)}"
      width="${Math.ceil(vw)}" height="${Math.ceil(vh)}" role="img" aria-label="Schematischer Netzplan ${esc(opMode?opScope:lineName(current))}">
      <g class="ld-edges" stroke="${color}">${lines}</g>
      <g class="ld-nodes" style="--ld-line:${color}">${nodes.join("")}</g>
      <g class="ld-labels">${labels.join("")}</g>
      <g class="ld-hits">${hits.join("")}</g></svg>`;
    planCtx={adj, role, dirTo, pos, list, color};
    return `<div class="panel ld-plan"><h3>Netzplan <span class="muted">· schematisch, 45°-Raster · Station anklicken für die Lupe</span>
      <span class="ld-zoom ld-engine" title="Layoutverfahren">${[["deflate","Luft raus"],["city","Luft raus (Stadt)"],["classic","Bisher"]].map(([k,l])=>`<button type="button" data-engine="${k}" aria-pressed="${planEngine===k}">${l}</button>`).join("")}</span>
      <span class="ld-zoom"><button type="button" data-zoom="out" aria-label="Verkleinern">−</button><button type="button" data-zoom="fit">Einpassen</button><button type="button" data-zoom="in" aria-label="Vergrößern">+</button></span></h3>
      <div class="ld-svg-wrap">${svg}</div>
      ${opMode?opLegend(list):`<div class="ld-legend">
        <span><svg width="22" height="14"><path d="M0 5.2H22M0 8.8H22" stroke="${color}" stroke-width="2.6"/><circle class="ld-n used" cx="11" cy="7" r="5.5"/></svg> Ein-/Ausstieg</span>
        <span><svg width="22" height="14"><path d="M0 5.2H22M0 8.8H22" stroke="${color}" stroke-width="2.6"/><rect class="ld-n held" x="8.6" y="1.5" width="4.8" height="11" rx="2.4" style="--ld-line:${color}"/></svg> gehalten</span>
        <span><svg width="22" height="14"><path d="M0 5.2H22M0 8.8H22" stroke="${color}" stroke-width="2.6"/><line class="ld-n pass" x1="11" y1="3" x2="11" y2="11"/></svg> nur durchfahren</span>
        <span><svg width="26" height="14"><path d="M1 4.5H19M15 1.5L19 4.5L15 7.5" fill="none" stroke="${color}" stroke-width="1.8"/><path d="M25 9.5H7M11 6.5L7 9.5L11 12.5" fill="none" stroke="${color}" stroke-width="1.8"/></svg> je Fahrtrichtung ein Strich, rechts gefahren; Stärke = Fahrten</span>
      </div>`}</div>`;
  }

  // ---------- Lupe: Fahrtbeziehungen an einem Knoten ----------
  // Arme in der Richtung des Plans; Durchbindungen innerhalb der Linie zählen
  // wie ein Halt.
  let planCtx=null;
  function closeLens(){ lens.close(); }
  function openLens(sid, target){
    lens.close();
    if(!planCtx || !planCtx.adj.has(sid)) return;
    const plan=bodyEl.querySelector(".ld-plan"), svg=plan&&plan.querySelector(".ld-svg");
    if(!svg) return;
    const T=D().trips||[];
    const p=planCtx.pos.get(sid);
    let ring=null;
    if(p){
      ring=document.createElementNS("http://www.w3.org/2000/svg","circle");
      ring.setAttribute("class","ld-lens-ring");
      ring.setAttribute("cx", p.p[0]); ring.setAttribute("cy", p.p[1]); ring.setAttribute("r", 16);
      svg.querySelector(".ld-hits").before(ring);
    }
    lens.open({
      sid, list:planCtx.list, byLine:opScope!=null, parent:plan, target,
      joins:opScope!=null?y=>inScope(T[y]):y=>T[y].line===current,
      dirs:nb=>planCtx.dirTo.get(sid+"|"+nb)||[1,0],
      colorOf:opScope!=null?m=>opLineColor(m.line)[0]:()=>planCtx.color,
      onClose:()=>{ if(ring) ring.remove(); },
    });
  }
  let dragged=false;
  bodyEl.addEventListener("pointerdown", e=>{
    const wrap=e.target.closest(".ld-svg-wrap");
    if(!wrap || e.button!==0) return;
    const main=document.querySelector("main");
    const x0=e.clientX, y0=e.clientY, sl=wrap.scrollLeft, st=main?main.scrollTop:0;
    dragged=false;
    function move(ev){
      const dx=ev.clientX-x0, dy=ev.clientY-y0;
      if(!dragged && Math.hypot(dx,dy)<5) return;
      dragged=true;
      wrap.classList.add("dragging");
      wrap.scrollLeft=sl-dx;
      if(main) main.scrollTop=st-dy;
    }
    function up(){
      wrap.classList.remove("dragging");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    }
    if(e.pointerType==="mouse"){
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    }
  });
  bodyEl.addEventListener("click", e=>{
    if(dragged){ dragged=false; return; }
    const eb=e.target.closest("[data-engine]");
    if(eb){
      planEngine=eb.getAttribute("data-engine");
      try{ localStorage.setItem("trwl-plan-engine", planEngine); }catch(err){}
      planZoom=null;
      closeLens();
      render();
      return;
    }
    const zb=e.target.closest("[data-zoom]");
    if(zb){
      const z=zb.getAttribute("data-zoom");
      if(z==="fit"){ planZoom=null; applyZoom(); closeLens(); }
      else zoomBy(z==="in"?1.25:0.8);
      return;
    }
    const t=e.target.closest(".ld-svg [data-sid]");
    if(t){ openLens(t.getAttribute("data-sid"), t); return; }
    const le=lens.element();
    if(le && bodyEl.contains(le) && !le.contains(e.target)) closeLens();
  });

  // ---------- Seite ----------
  function render(){
    if(current==null && opScope==null) return;
    const kpis=D().kpis||{};
    bindDateInput(dateFromEl, kpis.first, kpis.last);
    bindDateInput(dateToEl, kpis.first, kpis.last);
    titleEl.innerHTML=opScope!=null
      ? `${esc(opScope||"(ohne Operator)")} <span class="muted">alle Linien</span>`
      : badgeHtml(current)+` <span class="muted">${esc(lineOperator(current))}</span>`;
    const parts=[];
    if(homeActive()) parts.push("Heimatregion");
    const from=dateFromEl.value, to=dateToEl.value;
    if(from&&to) parts.push(fmtDate(from)+"–"+fmtDate(to));
    else if(from) parts.push("ab "+fmtDate(from));
    else if(to) parts.push("bis "+fmtDate(to));
    const what=opScope!=null?"dieses Unternehmens":"dieser Linie";
    subEl.textContent=`Statistiken und Netzplan ${what}`+(parts.length?" · "+parts.join(" · "):"")+".";
    const list=rides();
    if(opScope!=null) assignOpColors(list);
    countEl.textContent=`${fmtN(list.length)} ${list.length===1?"Fahrt":"Fahrten"}`;
    tables=[];
    if(!list.length){
      bodyEl.innerHTML=`<p class="hint">Keine Fahrten ${what} im gewählten Zeitraum.</p>`;
      return;
    }
    bodyEl.innerHTML=kpiCards(list)
      +netPlan(list)
      +timeCharts(list)
      +(opScope!=null?`<div class="ld-grid">${lineTable(list)}${locTable(list)}</div><div class="ld-grid">${vehTable(list)}</div>`
        :`<div class="ld-grid">${locTable(list)}${vehTable(list)}</div>`)
      +`<div class="ld-grid">${stationTable(list)}${relationTable(list)}</div>`
      +edgeTable(list)
      +tripTable(list);
    bodyEl.querySelectorAll("table[data-ld-table]").forEach(paintTable);
    fitPlan();
  }
  // Textbreiten sind nur geschätzt; sichtbar gerendert die echte Box nehmen.
  function fitPlan(){
    const svg=bodyEl.querySelector(".ld-svg");
    if(!svg) return;
    let b;
    try{ b=svg.getBBox(); }catch(e){ return; }
    if(!b || !b.width) return;
    const pad=8;
    svg.setAttribute("viewBox", [b.x-pad, b.y-pad, b.width+2*pad, b.height+2*pad].map(v=>v.toFixed(1)).join(" "));
    svg.dataset.w=b.width+2*pad;
    svg.dataset.h=b.height+2*pad;
    applyZoom();
  }
  // Ohne eigene Wahl wird der Plan in die Breite eingepasst (nie vergrößert).
  let planZoom=null;
  function fitScale(svg){
    const wrap=svg.parentNode;
    if(wrap.clientWidth<50) return 1;
    return Math.min(1, (wrap.clientWidth-16)/Number(svg.dataset.w));
  }
  function applyZoom(){
    const svg=bodyEl.querySelector(".ld-svg");
    if(!svg || !svg.dataset.w) return;
    const z=planZoom==null?fitScale(svg):planZoom;
    svg.setAttribute("width", Math.ceil(Number(svg.dataset.w)*z));
    svg.setAttribute("height", Math.ceil(Number(svg.dataset.h)*z));
  }
  function zoomBy(f){
    const svg=bodyEl.querySelector(".ld-svg");
    if(!svg || !svg.dataset.w) return;
    const wrap=svg.parentNode, z0=planZoom==null?fitScale(svg):planZoom;
    const z=Math.max(0.2, Math.min(3, z0*f));
    // Mittelpunkt des sichtbaren Ausschnitts beibehalten
    const cx=(wrap.scrollLeft+wrap.clientWidth/2)/z0;
    planZoom=z;
    applyZoom();
    wrap.scrollLeft=cx*z-wrap.clientWidth/2;
    closeLens();
  }
  window.addEventListener("resize", ()=>{ if(planZoom==null) applyZoom(); });
  function open(key, opts){
    opts=opts||{};
    current=key;
    opScope=null;
    show(opts);
  }
  function openOperator(op, opts){
    current=null;
    opScope=op;
    show(opts||{});
  }
  function show(opts){
    planZoom=null;
    closeLens();
    dateFromEl.value=opts.from||"";
    dateToEl.value=opts.to||"";
    showTab("lineDetail");
    const nav=document.querySelector('.navlist [data-tab="lines"]');
    if(nav) nav.classList.add("active");
    render();
    const main=document.querySelector("main");
    if(main) main.scrollTop=0;
  }
  if(backEl) backEl.onclick=()=>showTab("lines");
  [dateFromEl,dateToEl].forEach(el=>{
    el.addEventListener("input", render);
    el.addEventListener("change", render);
  });
  passLabelsEl.addEventListener("change", render);
  onHomeChange(render);
  return {open, openOperator};
})();
function openLineDetail(key, opts){ lineDetail.open(key, opts); }
function openOperatorDetail(op, opts){ lineDetail.openOperator(op, opts); }
