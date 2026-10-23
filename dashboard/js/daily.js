// ---------- Tagesziele ----------
(function () {
  let DF = {}, DR = {}, dates = [], LC = {};
  const dateEl = document.getElementById("dailyDate");
  const prevBtn = document.getElementById("dailyPrev");
  const nextBtn = document.getElementById("dailyNext");
  const summaryEl = document.getElementById("dailySummary");
  const totalsEl = document.getElementById("dailyTotals");
  const bodyEl = document.getElementById("dailyBody");
  const copyBtn = document.getElementById("dailyCopy");
  const INITIAL = 40;

  function badge(key) {
    const name = lineName(key);
    const c = LC[key];
    const st = c ? ` style="background:${c[0]};color:${c[1]}"` : "";
    return `<span class="line-badge"${st}>${esc(name)}</span>`;
  }
  function fmtN(v) {
    if (v == null || v === "") return "—";
    return Number(v).toLocaleString("de-DE");
  }

  const singles = [
    { key: "lines", title: "Linien", cols: [{ k: "key", label: "Linie", line: true }] },
    { key: "locClasses", title: "Baureihen", cols: [{ k: "key", label: "Baureihe", lbl: true }] },
    {
      key: "vehicles", title: "Fahrzeuge", cols: [
        { k: "key", label: "Wagen", lbl: true },
        { k: "locClass", label: "Baureihe", lbl: true },
      ]
    },
    {
      key: "topVehicleInClass", title: "Top-Wagen der Baureihe", cols: [
        { k: "vehicle", label: "Wagen", lbl: true },
        { k: "km", label: "km", num: true },
        { k: "count", label: "Fahrten", num: true },
        { k: "locClass", label: "Baureihe", lbl: true },
        { k: "prevVehicle", label: "zuvor", lbl: true },
        { k: "prevKm", label: "km zuvor", num: true },
        { k: "prevCount", label: "Fahrten zuvor", num: true },
      ]
    },
    {
      key: "edges", title: "Kanten", cols: [
        { k: "from", label: "Von", lbl: true },
        { k: "to", label: "Nach", lbl: true },
      ]
    },
    { key: "stationsUsed", title: "Benutzte Stationen", cols: [{ k: "key", label: "Station", lbl: true }] },
    { key: "stationsThrough", title: "Gehaltene Stationen", cols: [{ k: "key", label: "Station", lbl: true }] },
    { key: "stationsPassed", title: "Physische Durchfahrten", cols: [{ k: "key", label: "Station", lbl: true }] },
  ];
  const combos = [
    {
      key: "lineVehicle", title: "Fahrzeug × Linie", cols: [
        { k: "vehicle", label: "Wagen", lbl: true },
        { k: "locClass", label: "Baureihe", lbl: true },
        { k: "line", label: "Linie", line: true },
      ]
    },
    {
      key: "lineLocClass", title: "Baureihe × Linie", cols: [
        { k: "locClass", label: "Baureihe", lbl: true },
        { k: "line", label: "Linie", line: true },
      ]
    },
    {
      key: "vehEdge", title: "Fahrzeug × Kante", cols: [
        { k: "vehicle", label: "Wagen", lbl: true },
        { k: "locClass", label: "Baureihe", lbl: true },
        { k: "from", label: "Von", lbl: true },
        { k: "to", label: "Nach", lbl: true },
      ]
    },
    {
      key: "lineEdge", title: "Linie × Kante", cols: [
        { k: "line", label: "Linie", line: true },
        { k: "from", label: "Von", lbl: true },
        { k: "to", label: "Nach", lbl: true },
      ]
    },
    {
      key: "locEdge", title: "Baureihe × Kante", cols: [
        { k: "locClass", label: "Baureihe", lbl: true },
        { k: "from", label: "Von", lbl: true },
        { k: "to", label: "Nach", lbl: true },
      ]
    },
    {
      key: "stationLine", title: "Station × Linie", cols: [
        { k: "station", label: "Station", lbl: true },
        { k: "line", label: "Linie", line: true },
      ]
    },
    {
      key: "vehEdgeLine", title: "Fahrzeug × Kante × Linie", cols: [
        { k: "vehicle", label: "Wagen", lbl: true },
        { k: "locClass", label: "Baureihe", lbl: true },
        { k: "from", label: "Von", lbl: true },
        { k: "to", label: "Nach", lbl: true },
        { k: "line", label: "Linie", line: true },
      ]
    },
    {
      key: "locEdgeLine", title: "Baureihe × Kante × Linie", cols: [
        { k: "locClass", label: "Baureihe", lbl: true },
        { k: "from", label: "Von", lbl: true },
        { k: "to", label: "Nach", lbl: true },
        { k: "line", label: "Linie", line: true },
      ]
    },
  ];

  // Gegenstück zu pack_daily in build_dashboard.py: Index-Listen zurück zu
  // Objekten mit den Feldnamen der Kategorie. Ergebnis je Payload gecacht.
  const unpacked = new WeakMap();
  function unpackDaily(packed) {
    if (!packed || !packed.days) return {};
    if (unpacked.has(packed)) return unpacked.get(packed);
    const fields = packed.fields || {}, strings = packed.strings || [];
    const out = {};
    Object.keys(packed.days).forEach(date => {
      const day = packed.days[date], o = {};
      Object.keys(day).forEach(cat => {
        const f = fields[cat];
        o[cat] = f ? day[cat].map(r => {
          const x = {};
          f.forEach((k, i) => { x[k] = strings[r[i]]; });
          return x;
        }) : day[cat];
      });
      out[date] = o;
    });
    unpacked.set(packed, out);
    return out;
  }

  function syncDaily(keep) {
    const src = D();
    DF = unpackDaily(src.dailyFirsts);
    DR = unpackDaily(src.dailyRepeats);
    dates = [...new Set(Object.keys(DF).concat(Object.keys(DR)))].sort();
    LC = src.lineColors || {};
    const k = src.kpis || {};
    bindDateInput(dateEl, k.first, k.last);
    if (!keep || dates.indexOf(dateEl.value) < 0) {
      dateEl.value = dates.length ? dates[dates.length - 1] : (k.last || "");
    }
  }

  function idxOf(d) { return dates.indexOf(d); }

  function edgeKey(r) {
    return (r && r.from != null && r.to != null) ? String(r.from) + "\0" + String(r.to) : "";
  }
  function vehId(r) {
    if (!r) return "";
    const n = (r.vehicle != null && r.vehicle !== "") ? r.vehicle : (r.key || "");
    if (!n) return "";
    return (r.locClass || "") + "\0" + n;
  }
  function keysOf(arr, fn) {
    const s = new Set();
    (arr || []).forEach(r => { const k = fn(r); if (k) s.add(k); });
    return s;
  }
  function cascadeSets(buckets) {
    return {
      lines: keysOf(buckets.lines, r => r.key),
      locs: keysOf(buckets.locClasses, r => r.key),
      vehs: keysOf(buckets.vehicles, vehId),
      stationsUsed: keysOf(buckets.stationsUsed, r => r.key),
      stationsThrough: keysOf(buckets.stationsThrough, r => r.key),
      stationsPassed: keysOf(buckets.stationsPassed, r => r.key),
      edges: keysOf(buckets.edges, edgeKey),
      vehLine: keysOf(buckets.lineVehicle, r => vehId(r) + "\0" + r.line),
      locLine: keysOf(buckets.lineLocClass, r => r.locClass + "\0" + r.line),
      vehEdge: keysOf(buckets.vehEdge, r => vehId(r) + "\0" + r.from + "\0" + r.to),
      lineEdge: keysOf(buckets.lineEdge, r => r.line + "\0" + r.from + "\0" + r.to),
      locEdge: keysOf(buckets.locEdge, r => r.locClass + "\0" + r.from + "\0" + r.to),
    };
  }
  function isImplied(specKey, row, S) {
    if (!S || !row) return false;
    const ek = edgeKey(row);
    if (specKey === "lineVehicle") return S.vehs.has(vehId(row)) || S.lines.has(row.line);
    if (specKey === "lineLocClass") return S.locs.has(row.locClass) || S.lines.has(row.line);
    if (specKey === "vehEdge") return S.vehs.has(vehId(row)) || S.edges.has(ek);
    if (specKey === "lineEdge") return S.lines.has(row.line) || S.edges.has(ek);
    if (specKey === "locEdge") return S.locs.has(row.locClass) || S.edges.has(ek);
    if (specKey === "stationLine") return S.stationsUsed.has(row.station) || S.stationsThrough.has(row.station) || S.lines.has(row.line);
    if (specKey === "vehEdgeLine") return S.vehs.has(vehId(row)) || S.edges.has(ek) || S.lines.has(row.line)
      || S.vehLine.has(vehId(row) + "\0" + row.line)
      || S.vehEdge.has(vehId(row) + "\0" + row.from + "\0" + row.to)
      || S.lineEdge.has(row.line + "\0" + row.from + "\0" + row.to);
    if (specKey === "locEdgeLine") return S.locs.has(row.locClass) || S.edges.has(ek) || S.lines.has(row.line)
      || S.locLine.has(row.locClass + "\0" + row.line)
      || S.locEdge.has(row.locClass + "\0" + row.from + "\0" + row.to)
      || S.lineEdge.has(row.line + "\0" + row.from + "\0" + row.to);
    return false;
  }
  function prepareRows(spec, rows, sets) {
    const tagged = rows.map(r => ({ r, implied: isImplied(spec.key, r, sets) }));
    tagged.sort((a, b) => (a.implied ? 1 : 0) - (b.implied ? 1 : 0));
    return tagged;
  }

  function renderTable(spec, rows, sets, impliedTitle) {
    if (!rows || !rows.length) return "";
    const tagged = prepareRows(spec, rows, sets);
    const impliedN = tagged.reduce((n, x) => n + (x.implied ? 1 : 0), 0);
    const wrap = document.createElement("div");
    wrap.className = "panel stats-scroll";
    wrap.style.marginBottom = "16px";
    let showAll = tagged.length <= INITIAL;
    const hint = impliedTitle || "folgt aus anderem Erstvorkommen";
    function paint() {
      const data = showAll ? tagged : tagged.slice(0, INITIAL);
      const remaining = tagged.length - data.length;
      const head = spec.cols.map(c => `<th class="${c.lbl || c.line ? "lbl" : ""}">${esc(c.label)}</th>`).join("");
      const body = data.map(({ r, implied }) => {
        const cls = implied ? ' class="daily-implied" title="' + esc(hint) + '"' : "";
        return "<tr" + cls + ">" + spec.cols.map(c => {
          const v = r[c.k];
          if (c.line) return `<td class="lbl">${badge(v)}</td>`;
          if (c.num) return `<td>${esc(fmtN(v))}</td>`;
          return `<td class="lbl">${esc(v == null || v === "" ? "—" : v)}</td>`;
        }).join("") + "</tr>";
      }).join("");
      const more = remaining > 0
        ? `<button type="button" class="stats-more">Mehr laden (${fmtN(remaining)} weitere, ${fmtN(tagged.length)} gesamt)</button>`
        : "";
      const count = impliedN
        ? `(${fmtN(tagged.length)}, davon ${fmtN(impliedN)} kaskadiert)`
        : `(${fmtN(tagged.length)})`;
      wrap.innerHTML = `<h3 style="margin:0 0 8px;font-size:14px">${esc(spec.title)} <span class="muted" style="font-weight:400">${count}</span></h3>
        <table class="stats"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>${more}`;
      const moreBtn = wrap.querySelector(".stats-more");
      if (moreBtn) moreBtn.onclick = () => { showAll = true; paint(); };
    }
    paint();
    return wrap;
  }

  function dayTravel(d) {
    let km = 0, min = 0, hasDur = false;
    (D().trips || []).forEach(t => {
      if ((t.date || "").slice(0, 10) !== d) return;
      km += Number(t.distanceKm) || 0;
      if (t.durationMin != null && t.durationMin !== "") {
        min += Number(t.durationMin) || 0;
        hasDur = true;
      }
    });
    return { km: Math.round(km * 10) / 10, min: Math.round(min), hasDur };
  }

  // Fahrten des Tages in Reihenfolge des Check-ins (createdAt), sonst Abfahrt.
  function dayTripsOf(d) {
    const out = [];
    (D().trips || []).forEach((t, i) => {
      if ((t.date || "").slice(0, 10) === d) out.push({ ...t, _i: i });
    });
    const key = t => t.createdAt || t.date || "";
    return out.sort((x, y) => key(x).localeCompare(key(y)) || x._i - y._i);
  }
  function appendDayTrips(d) {
    const list = dayTripsOf(d);
    if (!list.length) return;
    const cell = v => `<td class="lbl">${esc(v == null || v === "" ? "—" : v)}</td>`;
    const body = list.map((t, i) => "<tr>" +
      `<td>${i + 1}</td><td class="lbl">${badge(t.line)}</td>` +
      cell(t.vehicles) + cell(t.locClass) + cell(t.from) + cell(t.to) + cell(t.operator) +
      "</tr>").join("");
    const g = document.createElement("div");
    g.className = "daily-group";
    g.innerHTML = `<h3>Fahrten <span class="muted" style="font-weight:400">(${fmtN(list.length)})</span></h3>
      <div class="panel stats-scroll" style="margin-bottom:16px"><table class="stats">
      <thead><tr><th>#</th><th class="lbl">Linie</th><th class="lbl">Fahrzeug</th><th class="lbl">Baureihe</th>
      <th class="lbl">Von</th><th class="lbl">Nach</th><th class="lbl">Operator</th></tr></thead>
      <tbody>${body}</tbody></table></div>`;
    bodyEl.appendChild(g);
  }

  function render() {
    const d = dateEl.value;
    const travel = dayTravel(d);
    const kmStr = travel.km.toLocaleString("de-DE", { maximumFractionDigits: 1 });
    const durStr = travel.hasDur ? fmtDuration(travel.min) : "—";
    if (totalsEl) totalsEl.textContent = d ? ("· " + kmStr + " km · " + durStr) : "";
    const buckets = DF[d] || {};
    const repeatBuckets = DR[d] || {};
    const sets = cascadeSets(buckets);
    const repeatSets = cascadeSets(repeatBuckets);
    prevBtn.disabled = !dates.some(x => x < d);
    nextBtn.disabled = !dates.some(x => x > d);

    const chips = [];
    singles.concat(combos).forEach(s => {
      const n = (buckets[s.key] || []).length;
      if (n) chips.push(`<span>${esc(s.title)}: ${fmtN(n)}</span>`);
    });
    const hasRepeats = singles.concat(combos).some(s => (repeatBuckets[s.key] || []).length);
    summaryEl.textContent = dates.length
      ? (chips.length || hasRepeats ? "" : `Keine Fahrten am ${d || "—"}`)
      : "Keine Fahrten in den Daten";

    bodyEl.innerHTML = "";
    if (chips.length) {
      const chipRow = document.createElement("div");
      chipRow.className = "daily-chips";
      chipRow.innerHTML = chips.join("");
      bodyEl.appendChild(chipRow);
    }

    function appendGroup(title, specs, srcBuckets, srcSets, impliedTitle) {
      const parts = [];
      specs.forEach(s => {
        const rows = srcBuckets[s.key] || [];
        if (!rows.length) return;
        parts.push(renderTable(s, rows, srcSets, impliedTitle));
      });
      if (!parts.length) return;
      const g = document.createElement("div");
      g.className = "daily-group";
      g.innerHTML = `<h3>${esc(title)}</h3>`;
      parts.forEach(p => g.appendChild(p));
      bodyEl.appendChild(g);
    }
    appendDayTrips(d);
    if (!chips.length && hasRepeats) {
      const note = document.createElement("p");
      note.className = "daily-none";
      note.textContent = "Neu: keine Erstvorkommen an diesem Tag (alles schon früher gefahren).";
      bodyEl.appendChild(note);
    }
    appendGroup("Neu", singles, buckets, sets);
    appendGroup("Erste Kombis", combos, buckets, sets);
    appendGroup("Wiederholungen", singles.filter(s => s.key !== "topVehicleInClass"),
      repeatBuckets, repeatSets, "folgt aus anderer Wiederholung");
    appendGroup("Wiederholte Kombis", combos, repeatBuckets, repeatSets,
      "folgt aus anderer Wiederholung");
  }

  function step(dir) {
    const d = dateEl.value;
    let i = idxOf(d);
    if (i < 0) {
      const cand = dir < 0
        ? dates.filter(x => x < d).pop()
        : dates.find(x => x > d);
      if (cand) { dateEl.value = cand; render(); }
      return;
    }
    const ni = i + dir;
    if (ni >= 0 && ni < dates.length) { dateEl.value = dates[ni]; render(); }
  }

  function cellVal(col, row) {
    const v = row[col.k];
    if (col.line) {
      const name = lineName(v);
      return name === "" ? "—" : name;
    }
    if (col.num) return fmtN(v);
    return (v == null || v === "") ? "—" : v;
  }
  function blockTsv(spec, rows, sets, withCascade) {
    if (!rows || !rows.length) return "";
    const tagged = prepareRows(spec, rows, sets);
    const headers = spec.cols.map(c => c.label);
    if (withCascade) headers.push("Kaskadiert");
    const lines = [spec.title, headers.join("\t")];
    tagged.forEach(({ r, implied }) => {
      const row = spec.cols.map(c => cellVal(c, r));
      if (withCascade) row.push(implied ? "ja" : "nein");
      lines.push(row.map(tsvCell).join("\t"));
    });
    return lines.join("\n");
  }
  function copyTsv() {
    const d = dateEl.value;
    const dateLabel = fmtDate(d) || d || "—";
    const dayTrips = dayTripsOf(d);
    const parts = [dateLabel, tripsTsv(dayTrips, { route: true, number: true })];
    const buckets = DF[d] || {};
    const sets = cascadeSets(buckets);
    const blocks = [];
    singles.forEach(s => {
      const t = blockTsv(s, buckets[s.key] || [], sets, false);
      if (t) blocks.push(t);
    });
    let hasCascade = false;
    combos.forEach(s => {
      const t = blockTsv(s, buckets[s.key] || [], sets, true);
      if (t) { blocks.push(t); hasCascade = true; }
    });
    if (hasCascade) {
      blocks.push("Kaskadiert: Ja = folgt aus einem einfacheren Erstvorkommen am selben Tag. Nein = Ist neu.");
    }
    if (blocks.length) parts.push(blocks.join("\n\n"));
    else if (dayTrips.length) {
      parts.push("Neu: keine Erstvorkommen an diesem Tag (alles schon früher gefahren).");
    }
    copyText(parts.join("\n\n"), copyBtn);
  }

  prevBtn.onclick = () => step(-1);
  nextBtn.onclick = () => step(1);
  dateEl.onchange = render;
  dateEl.oninput = render;
  copyBtn.onclick = copyTsv;
  syncDaily(false);
  render();
  onHomeChange(() => { syncDaily(true); render(); });
})();
