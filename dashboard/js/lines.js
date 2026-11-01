// ---------- Linien-Katalog ----------
(function(){
  let LINES=D().lines||[];
  let LC=D().lineColors||{};
  const filterEl=document.getElementById("linesFilter");
  const dateFromEl=document.getElementById("linesDateFrom");
  const dateToEl=document.getElementById("linesDateTo");
  const countEl=document.getElementById("linesCount");
  const bodyEl=document.getElementById("linesBody");
  // RYB: Primär → Sekundär → Tertiär → dunklere, dann hellere Stufen.
  const BR_PALETTE=[
    "#dc2626","#ffd200","#2563eb",
    "#ea580c","#16a34a","#7c3aed",
    "#e11d48","#65a30d","#0891b2","#c026d3","#d97706","#4f46e5",
    "#9f1239","#a16207","#1e40af","#9a3412","#166534","#5b21b6",
    "#fb7185","#facc15","#60a5fa","#fb923c","#4ade80","#a78bfa",
  ];
  const openLines=new Set();

  function fmtN(v){
    if(v==null||v==="") return "—";
    return Number(v).toLocaleString("de-DE");
  }
  function badge(key){
    const name=lineName(key);
    const c=LC[key];
    const st=c?` style="background:${c[0]};color:${c[1]}"`:"";
    return `<span class="line-badge"${st}>${esc(name||"(ohne Linie)")}</span>`;
  }
  function locColor(i){
    return BR_PALETTE[i%BR_PALETTE.length];
  }
  function locLabel(name){ return name||"ohne"; }
  function shares(items, total){
    if(!total) return items.map(([k,n])=>({key:k,n:n,pct:0}));
    let used=0;
    return items.map(([k,n],i)=>{
      const raw=100*n/total;
      const pct=i===items.length-1
        ? Math.max(0, Math.round((100-used)*10)/10)
        : Math.round(raw*10)/10;
      used+=pct;
      return {key:k,n:n,pct:pct};
    });
  }

  const BIT_BOARDED=1, BIT_ALIGHTED=2, BIT_THROUGH=4, BIT_PASSED=8;
  function ek(a,b){ return a+"\0"+b; }
  function sk(a,b,c){ return a+"\0"+b+"\0"+c; }
  function stopName(sid){
    const names=D().lineStopNames||{};
    const n=names[sid]!=null?names[sid]:names[String(sid)];
    if(n) return n;
    const st=(D().stations||{})[sid]||(D().stations||{})[String(sid)];
    if(st&&st[2]) return st[2];
    return String(sid);
  }
  function dateBounds(){
    return {
      from:dateFromEl&&dateFromEl.value||"",
      to:dateToEl&&dateToEl.value||"",
    };
  }
  function coverDirectedPaths(edgeMap, seqMap, nameOf){
    const unused=new Map(edgeMap);
    const routes=[];
    // Kante -> Nachbarn, mit denen sie in einer befahrenen Folge steht.
    const seqNext=new Map(), seqPrev=new Map();
    for(const [key,n] of seqMap){
      if(!(n>0)) continue;
      const [a,b,c]=key.split("\0");
      const kn=ek(a,b), kp=ek(b,c);
      if(!seqNext.has(kn)) seqNext.set(kn,[]);
      seqNext.get(kn).push(c);
      if(!seqPrev.has(kp)) seqPrev.set(kp,[]);
      seqPrev.get(kp).push(a);
    }
    function adj(){
      const fwd=new Map(), rev=new Map();
      for(const e of unused.values()){
        if(!fwd.has(e.a)) fwd.set(e.a,[]);
        fwd.get(e.a).push(e.b);
        if(!rev.has(e.b)) rev.set(e.b,[]);
        rev.get(e.b).push(e.a);
      }
      return [fwd, rev];
    }
    function pathEdge(u,v,forward){ return forward?ek(u,v):ek(v,u); }
    function maxChain(start, prev, usedEdges, nbrs, forward){
      let best=0;
      function dfs(u, prevU, n, used){
        if(n>best) best=n;
        for(const w of nbrs.get(u)||[]){
          if(w===prevU) continue;
          const edge=pathEdge(u,w,forward);
          if(used.has(edge)||!unused.has(edge)) continue;
          const next=new Set(used);
          next.add(edge);
          dfs(w,u,n+1,next);
        }
      }
      dfs(start, prev, 0, usedEdges);
      return best;
    }
    // Kante u–v steckt in einer befahrenen Folge über u mit freier Kante.
    function reserved(u, v, forward, usedEdges){
      const free=edge=>unused.has(edge)&&!usedEdges.has(edge);
      if(forward) return (seqPrev.get(ek(u,v))||[]).some(x=>free(ek(x,u)));
      return (seqNext.get(ek(v,u))||[]).some(y=>free(ek(u,y)));
    }
    function pickHop(path, usedEdges, forward, fwd, rev){
      const u=forward?path[path.length-1]:path[0];
      const pred=forward
        ?(path.length>=2?path[path.length-2]:null)
        :(path.length>=2?path[1]:null);
      const nbrs=forward?fwd:rev;
      const cands=[];
      for(const v of nbrs.get(u)||[]){
        if(pred!=null && v===pred) continue;
        const edge=pathEdge(u,v,forward);
        if(usedEdges.has(edge)||!unused.has(edge)) continue;
        if(pred!=null){
          const triple=forward?sk(pred,u,v):sk(v,u,pred);
          if(!seqMap.get(triple) && reserved(u,v,forward,usedEdges)) continue;
        }
        cands.push(v);
      }
      if(!cands.length) return null;
      if(cands.length===1) return cands[0];
      function key(v){
        const edge=pathEdge(u,v,forward);
        const extra=new Set(usedEdges);
        extra.add(edge);
        let freq;
        if(pred==null) freq=unused.get(edge).n;
        else if(forward) freq=seqMap.get(sk(pred,u,v))||0;
        else freq=seqMap.get(sk(v,u,pred))||0;
        return [-freq, -maxChain(v,u,extra,nbrs,forward), nameOf(v)];
      }
      cands.sort((v1,v2)=>{
        const k1=key(v1), k2=key(v2);
        for(let i=0;i<3;i++) if(k1[i]!==k2[i]) return k1[i]<k2[i]?-1:1;
        return 0;
      });
      return cands[0];
    }
    function grow(){
      if(!unused.size) return null;
      let seed=null;
      for(const e of unused.values()){
        const na=nameOf(e.a), nb=nameOf(e.b);
        if(!seed || e.n>seed.n || (e.n===seed.n && (na<seed.na || (na===seed.na && nb<seed.nb)))){
          seed={n:e.n, na, nb, a:e.a, b:e.b};
        }
      }
      const path=[seed.a, seed.b];
      const usedEdges=new Set([ek(seed.a, seed.b)]);
      while(true){
        const [fwd, rev]=adj();
        const nxt=pickHop(path, usedEdges, true, fwd, rev);
        if(nxt==null) break;
        usedEdges.add(ek(path[path.length-1], nxt));
        path.push(nxt);
      }
      while(true){
        const [fwd, rev]=adj();
        const prv=pickHop(path, usedEdges, false, fwd, rev);
        if(prv==null) break;
        usedEdges.add(ek(prv, path[0]));
        path.unshift(prv);
      }
      return [path, usedEdges];
    }
    while(unused.size){
      const grown=grow();
      if(!grown) break;
      const path=grown[0], usedEdges=grown[1];
      if(!path || path.length<2) break;
      const counts=[];
      for(let i=0;i<path.length-1;i++){
        const edge=ek(path[i], path[i+1]);
        counts.push(unused.get(edge).n);
        unused.delete(edge);
      }
      let loop=false;
      if(path.length>2 && path[0]===path[path.length-1]){
        loop=true;
      }else if(path.length>2){
        const close=ek(path[path.length-1], path[0]);
        const lastRev=ek(path[path.length-1], path[path.length-2]);
        if(unused.has(close) && !usedEdges.has(close) && close!==lastRev){
          counts.push(unused.get(close).n);
          unused.delete(close);
          loop=true;
        }
      }
      routes.push({loop, ids:path, counts});
    }
    return routes;
  }
  function aggregateLines(rides){
    const usedGlobal=new Set();
    for(const r of rides){
      for(const st of r[4]||[]){
        if(st[1]&(BIT_BOARDED|BIT_ALIGHTED)) usedGlobal.add(st[0]);
      }
    }
    const byLine=new Map();
    for(const r of rides){
      const lk=r[1];
      if(!lk) continue;
      let g=byLine.get(lk);
      if(!g){
        g={
          key:lk, operator:lineOperator(lk), count:0, distanceKm:0,
          locCounts:new Map(), locKm:new Map(),
          edges:new Map(), seq:new Map(), role:new Map(),
        };
        byLine.set(lk,g);
      }
      g.count+=1;
      g.distanceKm+=(Number(r[3])||0);
      const loc=r[2]||"";
      if(loc){
        g.locCounts.set(loc,(g.locCounts.get(loc)||0)+1);
        g.locKm.set(loc,(g.locKm.get(loc)||0)+(Number(r[3])||0));
      }
      const stops=r[4]||[];
      for(const st of stops){
        const sid=st[0], bits=st[1]||0;
        let role=g.role.get(sid);
        if(!role){ role={b:0,a:0,t:0,p:0}; g.role.set(sid,role); }
        if(bits&BIT_BOARDED) role.b++;
        if(bits&BIT_ALIGHTED) role.a++;
        if(bits&BIT_THROUGH) role.t++;
        if(bits&BIT_PASSED) role.p++;
      }
      for(let i=0;i<stops.length-1;i++){
        const a=stops[i][0], b=stops[i+1][0];
        if(a===b) continue;
        const edge=ek(a,b);
        const prev=g.edges.get(edge);
        if(prev) prev.n++;
        else g.edges.set(edge,{a,b,n:1});
        if(i>=1){
          const p=stops[i-1][0];
          if(p!==a) g.seq.set(sk(p,a,b),(g.seq.get(sk(p,a,b))||0)+1);
        }
      }
    }
    const rows=[];
    for(const g of byLine.values()){
      const locClasses=[];
      let taggedKm=0;
      const tagged=[...g.locCounts.entries()].sort((a,b)=>b[1]-a[1]);
      for(const [k,n] of tagged){
        const raw=g.locKm.get(k)||0;
        taggedKm+=raw;
        locClasses.push([k, n, Math.round(raw*10)/10]);
      }
      const taggedN=tagged.reduce((s,x)=>s+x[1],0);
      const untagged=Math.max(0, g.count-taggedN);
      if(untagged){
        locClasses.push(["", untagged, Math.round(Math.max(0, g.distanceKm-taggedKm)*10)/10]);
      }
      const routes=coverDirectedPaths(g.edges, g.seq, stopName).map(route=>({
        loop:route.loop,
        counts:route.counts,
        stops:route.ids.map(sid=>{
          const role=g.role.get(sid);
          let flags=0;
          if(usedGlobal.has(sid)) flags|=1;
          if(role && (role.b||role.a)) flags|=2;
          if(role && role.p && !(role.b||role.a||role.t)) flags|=4;
          return [sid, stopName(sid), flags];
        }),
      }));
      rows.push({
        key:g.key,
        operator:g.operator,
        count:g.count,
        distanceKm:Math.round(g.distanceKm*10)/10,
        locClasses,
        routes,
      });
    }
    const opKm=new Map();
    for(const r of rows) opKm.set(r.operator,(opKm.get(r.operator)||0)+(r.distanceKm||0));
    rows.sort((a,b)=>{
      const dk=(opKm.get(b.operator)||0)-(opKm.get(a.operator)||0);
      if(dk) return dk;
      const oc=String(a.operator).localeCompare(String(b.operator),"de",{sensitivity:"accent"});
      if(oc) return oc;
      const nc=lineName(a.key).localeCompare(lineName(b.key),"de",{sensitivity:"accent"});
      if(nc) return nc;
      return a.key<b.key?-1:a.key>b.key?1:0;
    });
    return rows;
  }
  function linesForView(){
    const {from,to}=dateBounds();
    if(!from && !to) return D().lines||[];
    const rides=(D().lineRides||[]).filter(r=>{
      const d=r[0]||"";
      if(!d) return false;
      return (!from || d>=from) && (!to || d<=to);
    });
    return aggregateLines(rides);
  }
  function visibleGroups(){
    const q=(filterEl&&filterEl.value||"").trim().toLowerCase();
    const rows=LINES.filter(r=>{
      if(!q) return true;
      const name=lineName(r.key).toLowerCase();
      const op=(r.operator||"").toLowerCase();
      return name.includes(q) || op.includes(q);
    });
    const groups=[];
    const byOp=new Map();
    for(const r of rows){
      const op=r.operator||"";
      let g=byOp.get(op);
      if(!g){
        g={operator:op, lines:[], km:0, count:0};
        byOp.set(op,g);
        groups.push(g);
      }
      g.lines.push(r);
      g.km+=r.distanceKm||0;
      g.count+=r.count||0;
    }
    for(const g of groups){
      g.lines.sort((a,b)=>lineName(a.key).localeCompare(lineName(b.key),undefined,{numeric:true}));
    }
    return {rows, groups};
  }
  function baureihen(r){
    const classes=r.locClasses||[];
    const parts=shares(classes, r.count||0);
    const kmItems=classes.map(it=>[it[0], Number(it[2])||0]);
    const kmTotal=kmItems.reduce((s,it)=>s+it[1],0);
    const kmParts=shares(kmItems, kmTotal);
    if(!parts.length){
      const empty="keine Baureihen getaggt";
      return {trips:empty, km:empty};
    }
    const trips=parts.map(p=>{
      const n=p.n===1?"1 Fahrt":fmtN(p.n)+" Fahrten";
      return locLabel(p.key)+" "+n+" ("+pctText(p.pct)+" %)";
    }).join("; ");
    const km=kmParts.map(p=>
      locLabel(p.key)+" "+fmtN(p.n)+" km ("+pctText(p.pct)+" %)"
    ).join("; ");
    return {trips, km};
  }
  function fahrweg(route){
    const names=(route.stops||[]).map(s=>s[1]||"—");
    const path=names.join(" → ");
    return route.loop&&path?path+" ↻":path;
  }
  function activeFilterHeading(){
    const parts=[];
    if(homeActive()) parts.push("Heimatregion");
    const {from,to}=dateBounds();
    if(from&&to) parts.push(fmtDate(from)+"–"+fmtDate(to));
    else if(from) parts.push("ab "+fmtDate(from));
    else if(to) parts.push("bis "+fmtDate(to));
    const q=(filterEl&&filterEl.value||"").trim();
    if(q) parts.push("Suche "+q);
    const label=parts.length ? parts.join(" · ") : "Alle";
    return "Linien · Eingestellte Filter: "+label;
  }
  function linesTsv(groups){
    const headers=["Operator","Linie","Baureihen","Baureihen km","Fahrweg"];
    const out=[tsvCell(activeFilterHeading()), headers.join("\t")];
    groups.forEach(g=>{
      const op=g.operator||"(ohne Operator)";
      g.lines.forEach(r=>{
        const br=baureihen(r);
        const base=[op, lineName(r.key)||"(ohne Linie)", br.trips, br.km];
        const routes=r.routes||[];
        if(!routes.length){
          out.push(base.concat([""]).map(tsvCell).join("\t"));
          return;
        }
        routes.forEach(route=>{
          out.push(base.concat([fahrweg(route)]).map(tsvCell).join("\t"));
        });
      });
    });
    return out.join("\n");
  }

  function render(){
    const kpis=D().kpis||{};
    bindDateInput(dateFromEl, kpis.first, kpis.last);
    bindDateInput(dateToEl, kpis.first, kpis.last);
    LINES=linesForView();
    LC=D().lineColors||{};
    const headEl=document.getElementById("linesFilterHead");
    if(headEl) headEl.textContent=activeFilterHeading();
    const {rows, groups}=visibleGroups();
    if(countEl){
      const nL=rows.length, nO=groups.length;
      countEl.textContent=nL
        ? `${fmtN(nL)} ${nL===1?"Linie":"Linien"} · ${fmtN(nO)} ${nO===1?"Operator":"Operatoren"}`
        : ((D().lines||[]).length?"Keine Treffer.":"Keine Linien.");
    }
    if(!bodyEl) return;
    if(!rows.length){
      bodyEl.innerHTML=`<p class="hint">${esc((D().lines||[]).length?"Keine Treffer.":"Keine Linien in den Daten.")}</p>`;
      return;
    }
    bodyEl.innerHTML=groups.map((g,gi)=>{
      const opLabel=g.operator||"(ohne Operator)";
      const n=g.lines.length;
      const cards=g.lines.map(r=>lineCard(r)).join("");
      return `<div class="line-op"><h3>${esc(opLabel)} <span class="muted">· ${fmtN(n)} ${n===1?"Linie":"Linien"} · ${fmtN(Math.round(g.km*10)/10)} km · ${fmtN(g.count)} Fahrten</span>
        <button type="button" class="line-card-detail line-op-detail" data-gi="${gi}" title="Detailseite mit Statistiken und Netzplan aller Linien dieses Unternehmens">Netzplan →</button></h3>${cards}</div>`;
    }).join("");
    bodyEl.querySelectorAll(".line-op-detail").forEach(btn=>{
      btn.addEventListener("click",()=>{
        const g=groups[+btn.dataset.gi];
        if(g) openOperatorDetail(g.operator, dateBounds());
      });
    });
    bodyEl.querySelectorAll("details.line-card .line-card-detail").forEach(btn=>{
      btn.addEventListener("click",e=>{
        e.preventDefault();
        e.stopPropagation();
        const row=LINES[+btn.closest("details.line-card").dataset.idx];
        if(row) openLineDetail(row.key, dateBounds());
      });
    });
    bodyEl.querySelectorAll("details.line-card").forEach(el=>{
      el.addEventListener("toggle",()=>{
        const row=LINES[+el.dataset.idx];
        if(!row) return;
        if(el.open) openLines.add(row.key); else openLines.delete(row.key);
      });
    });
  }

  function pctText(pct){
    return String(pct).replace(".",",");
  }
  function stackBar(parts, unit){
    return parts.map((p,i)=>{
      if(!p.pct) return "";
      const col=locColor(i);
      const u=unit==="km"?"km":(p.n===1?"Fahrt":"Fahrten");
      const title=`${locLabel(p.key)}: ${fmtN(p.n)} ${u} (${pctText(p.pct)} %)`;
      return `<i style="flex:${p.pct} 0 0;background:${col}" title="${esc(title)}"></i>`;
    }).join("");
  }
  function lineCard(r){
    const classes=r.locClasses||[];
    const total=r.count||0;
    const parts=shares(classes, total);
    const kmItems=classes.map(it=>[it[0], Number(it[2])||0]);
    const kmTotal=kmItems.reduce((s,it)=>s+it[1],0);
    const kmParts=shares(kmItems, kmTotal);
    const countBar=stackBar(parts, "Fahrten");
    const kmBar=stackBar(kmParts, "km");
    const legend=parts.map((p,i)=>{
      const col=locColor(i);
      const km=kmParts[i];
      const kmTitle=km?` · ${fmtN(km.n)} km (${pctText(km.pct)} %)`:"";
      return `<span title="${fmtN(p.n)} Fahrten${kmTitle}"><i style="background:${col}"></i>${esc(locLabel(p.key))} ${pctText(p.pct)} %</span>`;
    }).join("");
    const bars=parts.length?`<div class="line-stack-row"><span class="line-stack-k">Fahrten</span><div class="line-stack">${countBar}</div></div>
        <div class="line-stack-row"><span class="line-stack-k">km</span><div class="line-stack">${kmBar}</div></div>`:"";
    const nR=(r.routes||[]).length;
    const idx=LINES.indexOf(r);
    const isOpen=openLines.has(r.key);
    return `<details class="line-card"${isOpen?" open":""} data-idx="${idx}">
      <summary class="line-card-head">${badge(r.key)}
        <span class="line-card-meta">${fmtN(r.distanceKm)} km · ${fmtN(r.count)} ${r.count===1?"Fahrt":"Fahrten"} · ${fmtN(nR)} ${nR===1?"Laufweg":"Laufwege"}</span>
        <button type="button" class="line-card-detail" title="Detailseite mit Statistiken und Netzplan">Details →</button>
      </summary>
      <div class="line-card-body">
        ${bars}
        <div class="line-stack-legend">${legend||'<span>keine Baureihen getaggt</span>'}</div>
        ${pearl(r)}
      </div>
    </details>`;
  }

  function pearl(r){
    const routes=r.routes||[];
    if(!routes.length) return `<p class="hint">Kein Laufweg.</p>`;
    const c=LC[r.key];
    const st=c?` style="--pearl:${c[0]}"`:"";
    const items=routes.map(route=>{
      const stops=route.stops||[];
      const counts=route.counts||[];
      const loop=!!route.loop;
      const rows=[];
      stops.forEach((s,i)=>{
        const flags=s[2]||0;
        const cls=["pearl-row","pearl-stop"];
        if(flags&1) cls.push("used");
        if(flags&2) cls.push("used-line");
        if(flags&4) cls.push("pass");
        const mark=flags&4?"":"<i></i>";
        rows.push(`<div class="${cls.join(" ")}"><span class="pearl-n"></span><span class="pearl-mark">${mark}</span><span class="pearl-lab">${esc(s[1]||"—")}</span></div>`);
        const last=i===stops.length-1;
        if(!last){
          rows.push(`<div class="pearl-row pearl-edge"><span class="pearl-n">${fmtN(counts[i])}</span><span class="pearl-mark"></span><span class="pearl-lab"></span></div>`);
        }else if(loop){
          rows.push(`<div class="pearl-row pearl-edge"><span class="pearl-n">${fmtN(counts[i])}</span><span class="pearl-mark"></span><span class="pearl-lab muted">↻</span></div>`);
        }
      });
      return `<div class="pearl${loop?" loop":""}"${st}>${rows.join("")}</div>`;
    });
    return `<div class="pearls">${items.join("")}</div>`;
  }

  const copyBtn=document.getElementById("linesCopy");
  function copyTsv(){
    copyText(linesTsv(visibleGroups().groups), copyBtn);
  }
  if(filterEl) filterEl.addEventListener("input", render);
  [dateFromEl,dateToEl].forEach(el=>{
    if(!el) return;
    el.addEventListener("input", render);
    el.addEventListener("change", render);
  });
  if(copyBtn) copyBtn.onclick=copyTsv;
  render();
  onHomeChange(render);
})();
