// ---------- Lupe: Fahrtbeziehungen an einer Station ----------
// Gemeinsam für Netzplan (Linien-Detailseite) und Karte. Jede Beziehung (aus
// Richtung A nach B, Einstieg, Ausstieg) ist eine eigene Spur: Arme in der
// Richtung des Aufrufers, Spuren rechts der Armachse, Linksabbieger innen,
// Rechtsabbieger außen.
const lens=(function(){
  const BIT_BOARDED=1, BIT_ALIGHTED=2, BIT_THROUGH=4, BIT_PASSED=8;
  const KIND_LABEL={board:"Einstieg", start:"Fahrtbeginn", alight:"Ausstieg", end:"Fahrtende"};
  let el=null, target=null, onClose=null, openSid=null;

  function fmtN(v){ return Number(v).toLocaleString("de-DE"); }
  function stopName(sid){
    const src=D();
    const n=(src.statStopNames||{})[sid];
    if(n) return n;
    const st=(src.stations||{})[sid];
    if(st&&st[2]) return st[2];
    return String(sid);
  }
  function badge(key){
    const c=(D().lineColors||{})[key];
    const st=c?` style="background:${c[0]};color:${c[1]}"`:"";
    return `<span class="line-badge"${st}>${esc(lineName(key)||"(ohne Linie)")}</span>`;
  }

  // Durchbindung: auf eine Fahrt, die an j ohne Ausstieg endet (dubi=ende),
  // folgt als nächster Check-in (createdAt) eine Fahrt, die an j ohne
  // Einstieg beginnt (dubi=start). Liefert den Index der Folgefahrt.
  let dubiCache=null;
  function checkinOrder(){
    const src=D();
    if(dubiCache && dubiCache.src===src) return dubiCache;
    const T=src.trips||[];
    const at=T.map(t=>Date.parse(t.createdAt||""));
    const ord=T.map((_,k)=>k).filter(k=>!isNaN(at[k])).sort((x,y)=>at[x]-at[y] || x-y);
    const nxt=new Map(), prv=new Map();
    for(let k=0;k+1<ord.length;k++){ nxt.set(ord[k], ord[k+1]); prv.set(ord[k+1], ord[k]); }
    dubiCache={src, nxt, prv};
    return dubiCache;
  }
  function stopBits(i, sid){
    let bits=0;
    for(const [s0,b] of (D().statStops||[])[i]||[]) if(String(s0)===sid) bits|=b;
    return bits;
  }
  function dubiNext(i){
    const all=D().statEdges||[];
    const E=all[i]||[];
    if(!E.length) return null;
    const j=String(E[E.length-1][1]);
    if(stopBits(i, j)&BIT_ALIGHTED) return null;
    const y=checkinOrder().nxt.get(i);
    const Ey=y==null?null:all[y];
    if(!Ey || !Ey.length || String(Ey[0][0])!==j) return null;
    if(stopBits(y, j)&BIT_BOARDED) return null;
    return y;
  }
  function dubiPrev(i){
    const x=checkinOrder().prv.get(i);
    return x!=null && dubiNext(x)===i?x:null;
  }

  // Ein-/Ausstiege, Halte und Durchfahrten an j über die Fahrten der Liste.
  function roles(j, list){
    const g={b:0,a:0,t:0,p:0};
    for(const r of list){
      let bits=0;
      for(const [sid,b] of r.stops) if(String(sid)===j) bits|=b;
      if(bits&BIT_BOARDED) g.b++;
      if(bits&BIT_ALIGHTED) g.a++;
      if(bits&BIT_THROUGH) g.t++;
      if(bits&BIT_PASSED) g.p++;
    }
    return g;
  }
  function factsText(g){
    const facts=[];
    if(g.b) facts.push(`Einstiege ${fmtN(g.b)}`);
    if(g.a) facts.push(`Ausstiege ${fmtN(g.a)}`);
    if(g.t) facts.push(`gehalten ${fmtN(g.t)}`);
    if(g.p) facts.push(`durchfahren ${fmtN(g.p)}`);
    return facts.join(" · ");
  }

  // Übergänge je Fahrt (list: {t, i, day, edges, stops}). joins(y) sagt, ob
  // eine Durchbindung mit Fahrt y als Halt zählt (durchgehend A→B); sonst
  // „weiter als …“ / „aus …“. byLine trennt die Beziehungen nach Linie.
  function movements(j, list, joins, byLine){
    const m=new Map();
    const T=D().trips||[], all=D().statEdges||[];
    const inList=new Set(list.map(r=>r.i));
    function add(from, to, kind, r, link, line){
      line=byLine?line:"";
      const k=(from||"")+">"+(to||"")+">"+kind+">"+(link||"")+">"+line;
      const g=m.get(k)||{from, to, kind, link, line, n:0, first:r.day, last:r.day};
      g.n++;
      if(r.day<g.first) g.first=r.day;
      if(r.day>g.last) g.last=r.day;
      m.set(k,g);
    }
    for(const r of list){
      const E=r.edges.map(e=>[String(e[0]), String(e[1])]).filter(e=>e[0]!==e[1]);
      const line=r.t.line||"";
      let bits=0;
      for(const [sid,b] of r.stops) if(String(sid)===j) bits|=b;
      for(let i=0;i<E.length;i++){
        if(E[i][1]===j){
          const nx=E[i+1];
          // Durchbindung zählt wie ein Halt: weiter mit der Folgefahrt.
          const y=i===E.length-1?dubiNext(r.i):null;
          if(nx && nx[0]===j) add(E[i][0], nx[1], "through", r, null, line);
          else if(y!=null && joins(y)) add(E[i][0], String(all[y][0][1]), "through", r, null, line);
          else if(y!=null) add(E[i][0], null, "end", r, lineName(T[y].line), line);
          else add(E[i][0], null, bits&BIT_ALIGHTED?"alight":"end", r, null, line);
        }
        if(E[i][0]===j && !(i>0 && E[i-1][1]===j)){
          const x=i===0?dubiPrev(r.i):null;
          if(x!=null && joins(x)){
            // Bei der Vorfahrt gezählt, außer sie fehlt in der Liste.
            if(!inList.has(x)){ const ex=all[x]; add(String(ex[ex.length-1][0]), E[i][1], "through", r, null, T[x].line||""); }
          }else if(x!=null) add(null, E[i][1], "start", r, lineName(T[x].line), line);
          else add(null, E[i][1], bits&BIT_BOARDED?"board":"start", r, null, line);
        }
      }
    }
    return [...m.values()].sort((x,y)=>y.n-x.n);
  }

  function moveText(m){
    const kind=m.kind==="end"&&m.link?"weiter als "+m.link:m.kind==="start"&&m.link?"aus "+m.link:KIND_LABEL[m.kind];
    const a=m.from!=null?stopName(m.from):kind;
    const b=m.to!=null?stopName(m.to):kind;
    return a+" → "+b;
  }

  // dirs: Nachbar → Richtung [dx, dy] (Bildschirm, y nach unten).
  // colorOf(m): Farbe der Spur.
  function svg(j, moves, dirs, colorOf){
    const arms=[...dirs.keys()].sort().map(nb=>{
      const d=dirs.get(nb);
      return {nb, a:Math.atan2(d[1], d[0])};
    }).sort((x,y)=>x.a-y.a);
    // Arme mit (fast) gleicher Richtung auseinanderziehen
    const MIN=Math.PI*28/180;
    for(let it=0; it<40 && arms.length>1; it++){
      let moved=false;
      for(let i=0;i<arms.length;i++){
        const a=arms[i], b=arms[(i+1)%arms.length];
        const gap=b.a-a.a+(i===arms.length-1?2*Math.PI:0);
        if(gap<MIN-1e-6){ const s=(MIN-gap)/2; a.a-=s; b.a+=s; moved=true; }
      }
      if(!moved) break;
    }
    const arm=new Map();
    arms.forEach(x=>{ x.d=[Math.cos(x.a), Math.sin(x.a)]; x.inL=[]; x.outL=[]; arm.set(x.nb, x); });
    const maxN=Math.max(1,...moves.map(m=>m.n));
    const GAP=2.5;
    moves.forEach((m,i)=>{
      m.i=i;
      m.w=2+5*Math.sqrt(m.n/maxN);
      const A=m.from!=null?arm.get(m.from):null, B=m.to!=null?arm.get(m.to):null;
      // Abbiegewinkel aus Sicht der Ankunft: negativ = links, positiv = rechts
      if(A && B){
        const t=[-A.d[0], -A.d[1]];
        let s=Math.atan2(t[0]*B.d[1]-t[1]*B.d[0], t[0]*B.d[0]+t[1]*B.d[1]);
        if(s>Math.PI-1e-6) s=-Math.PI;
        m.turn=s;
      }else m.turn=-4;
      if(A) A.inL.push(m);
      if(B) B.outL.push(m);
    });
    let band=0;
    for(const x of arms){
      for(const [L, key] of [[x.inL,"oIn"],[x.outL,"oOut"]]){
        L.sort((p,q)=>p.turn-q.turn || q.n-p.n);
        let o=GAP/2;
        L.forEach(m=>{ m[key]=o+m.w/2; o+=m.w+GAP; });
        band=Math.max(band, o);
      }
    }
    const r0=Math.max(46, 2.2*band), R=r0+74;
    const f=v=>v.toFixed(1);
    // Spur auf Arm x im Abstand rad; Ankunft liegt rechts von −d, Abfahrt rechts von d.
    const inPt=(x,o,rad)=>[x.d[0]*rad+x.d[1]*o, x.d[1]*rad-x.d[0]*o];
    const outPt=(x,o,rad)=>[x.d[0]*rad-x.d[1]*o, x.d[1]*rad+x.d[0]*o];
    const paths=[], ends=[], colors=[];
    const k=0.6*r0;
    for(const m of moves){
      const col=colorOf(m);
      let ci=colors.indexOf(col);
      if(ci<0){ ci=colors.length; colors.push(col); }
      const A=m.from!=null?arm.get(m.from):null, B=m.to!=null?arm.get(m.to):null;
      const endAt=p=>ends.push(`<circle class="ld-lens-end" data-k="${m.i}" cx="${f(p[0])}" cy="${f(p[1])}" r="${f(m.w/2+2.5)}" style="--ld-line:${col}"/>`);
      let d="";
      if(A){
        const p=inPt(A,m.oIn,R), q=inPt(A,m.oIn,r0);
        d+=`M${f(p[0])} ${f(p[1])}L${f(q[0])} ${f(q[1])}`;
        if(B){
          const e=outPt(B,m.oOut,r0), g=outPt(B,m.oOut,R);
          d+=`C${f(q[0]-A.d[0]*k)} ${f(q[1]-A.d[1]*k)} ${f(e[0]-B.d[0]*k)} ${f(e[1]-B.d[1]*k)} ${f(e[0])} ${f(e[1])}L${f(g[0])} ${f(g[1])}`;
        }else endAt(q);
      }else if(B){
        const e=outPt(B,m.oOut,r0), g=outPt(B,m.oOut,R);
        d=`M${f(e[0])} ${f(e[1])}L${f(g[0])} ${f(g[1])}`;
        endAt(e);
      }
      const head=B?` marker-end="url(#ldLensArrow${ci})"`:"";
      paths.push(`<path data-k="${m.i}" d="${d}" stroke="${col}" stroke-width="${f(m.w)}"${head}><title>${esc(moveText(m)+": "+fmtN(m.n)+"×")}</title></path>`);
    }
    const markers=colors.map((c,i)=>`<marker id="ldLensArrow${i}" viewBox="0 0 10 10" refX="5" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto">
        <path d="M0 0L10 5L0 10z" fill="${c}"/></marker>`).join("");
    // Namen bleiben bei vielen Spuren (großes R) auf dem Schirm etwa 11px groß.
    const fs=11*Math.max(1, (2*R+80)/330);
    const axes=arms.map(x=>`<line x1="0" y1="0" x2="${f(x.d[0]*(R+4))}" y2="${f(x.d[1]*(R+4))}"/>`).join("");
    const names=arms.map(x=>{
      const p=[x.d[0]*(R+fs), x.d[1]*(R+fs)];
      const anchor=x.d[0]>0.35?"start":x.d[0]<-0.35?"end":"middle";
      const dy=x.d[1]>0.35?"0.9em":x.d[1]<-0.35?"-0.2em":"0.35em";
      return `<text x="${f(p[0])}" y="${f(p[1])}" dy="${dy}" text-anchor="${anchor}">${esc(stopName(x.nb))}</text>`;
    }).join("");
    return `<svg class="ld-lens-svg" viewBox="${-R-20} ${-R-20} ${2*R+40} ${2*R+40}" role="img" aria-label="Fahrtbeziehungen an ${esc(stopName(j))}">
      <defs>${markers}</defs>
      <g class="ld-lens-axes">${axes}</g>
      <circle class="ld-lens-hub" r="${f(r0)}"/>
      <g class="ld-lens-moves">${paths.join("")}</g>
      <g class="ld-lens-ends">${ends.join("")}</g>
      <g class="ld-lens-names" style="font-size:${f(fs)}px">${names}</g></svg>`;
  }

  // Neben das Ziel legen (Element oder Objekt mit getBoundingClientRect),
  // immer ganz im sichtbaren Fenster.
  function place(){
    if(!el || !target) return;
    const tr=target.getBoundingClientRect(), vw=window.innerWidth, vh=window.innerHeight;
    const w=el.offsetWidth, h=el.offsetHeight, cx=tr.left+tr.width/2, cy=tr.top+tr.height/2;
    let left=cx+24;
    if(left+w>vw-8) left=cx-24-w;
    left=Math.max(8, Math.min(left, vw-w-8));
    el.style.left=left+"px";
    el.style.top=Math.max(8, Math.min(cy-120, vh-h-8))+"px";
  }
  window.addEventListener("resize", place);
  document.addEventListener("scroll", place, true);
  document.addEventListener("keydown", e=>{ if(e.key==="Escape" && el) close(); });

  function close(){
    const cb=onClose;
    target=null; onClose=null; openSid=null;
    if(el){ el.remove(); el=null; }
    if(cb) cb();
  }
  // o: {sid, list, joins, byLine, dirs, colorOf, parent, target, onClose}
  function open(o){
    close();
    const sid=o.sid;
    const moves=movements(sid, o.list, o.joins, o.byLine);
    const dirs=new Map();
    for(const m of moves) for(const nb of [m.from, m.to]) if(nb!=null && !dirs.has(nb)) dirs.set(nb, o.dirs(nb));
    const rows=moves.map(m=>`<tr data-k="${m.i}">${o.byLine?`<td>${badge(m.line)}</td>`:""}<td>${esc(moveText(m))}</td><td class="num">${fmtN(m.n)}</td>
      <td class="muted">${m.first===m.last?fmtDate(m.first):fmtDate(m.first)+"–<wbr>"+fmtDate(m.last)}</td></tr>`).join("");
    const svgHtml=moves.length?svg(sid, moves, dirs, o.colorOf):"";
    const cols=o.byLine?4:3;
    el=document.createElement("div");
    el.className="ld-lens";
    el.setAttribute("role","dialog");
    el.setAttribute("aria-label","Lupe "+stopName(sid));
    el.innerHTML=`<div class="ld-lens-head"><b>${esc(stopName(sid))}</b>
        <button type="button" class="ld-lens-close" aria-label="Schließen">×</button></div>
      <div class="ld-lens-facts muted">${esc(factsText(roles(sid, o.list))||"—")}</div>
      ${svgHtml}
      <table class="ld-lens-table"><thead><tr>${o.byLine?"<th>Linie</th>":""}<th>Beziehung</th><th class="num">Fahrten</th><th>Zeitraum</th></tr></thead>
      <tbody>${rows||`<tr><td colspan="${cols}" class="muted">Keine Fahrten.</td></tr>`}</tbody></table>`;
    o.parent.appendChild(el);
    // Armnamen sind unterschiedlich lang; Ausschnitt an die echte Zeichnung anpassen.
    const ls=el.querySelector(".ld-lens-svg");
    if(ls){
      try{
        const b=ls.getBBox();
        ls.setAttribute("viewBox", [b.x-6, b.y-6, b.width+12, b.height+12].map(v=>v.toFixed(1)).join(" "));
      }catch(e){}
    }
    el.querySelector(".ld-lens-close").onclick=close;
    // Beziehung in Tabelle oder Diagramm hervorheben
    const box=el;
    function focus(k){
      if(!ls) return;
      ls.classList.toggle("focus", k!=null);
      ls.querySelectorAll("[data-k]").forEach(e=>e.classList.toggle("hl", e.getAttribute("data-k")===k));
      box.querySelectorAll("tr[data-k]").forEach(e=>e.classList.toggle("hl", e.getAttribute("data-k")===k));
    }
    el.addEventListener("mouseover", e=>{ const t=e.target.closest("[data-k]"); focus(t?t.getAttribute("data-k"):null); });
    el.addEventListener("mouseleave", ()=>focus(null));
    openSid=sid;
    onClose=o.onClose||null;
    target=o.target;
    place();
    return el;
  }

  return {
    open, close, place, stopName, dubiNext, dubiPrev,
    element:()=>el, sid:()=>openSid,
  };
})();
