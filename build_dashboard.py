#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Erzeugt aus statuses.json + stations.json ein in sich geschlossenes HTML-Dashboard.

Auswertungen:
- Kennzahlen-Übersicht (Check-ins, km, Reisezeit, Punkte, Stationen/Linien, Zeitraum)
- Statistiken zu Linien, Baureihen, Fahrzeugen und Stationen
  (Ein-/Ausstieg/gehalten/physische Durchfahrt, Rankings, Unique-Kombis, Kreuztabellen)
- Geo-Karte mit Heatmap gerichteter Kanten: pro Fahrt werden die tatsächlich
  befahrenen aufeinanderfolgenden Zwischenhalte als gerichtete Segmente gezählt;
  häufiger befahrene Segmente werden dicker/röter dargestellt.
- Durchsuch- und sortierbare Fahrten-Tabelle mit aufklappbarer Detailansicht
  (alle befahrenen Zwischenhalte mit Zeiten, Gleis, Verspätung).

Nur Standardbibliothek. Die Karte lädt Leaflet + OpenStreetMap-Kacheln (Internet);
der Rest funktioniert offline.

Beispiel:
    python3 build_dashboard.py --open
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

from boarding_patches import apply_to_statuses as apply_boarding_patches
from boarding_patches import learn_edge_measures
from boarding_patches import load_patches as load_boarding_patches
from edge_patches import expand_edge_stopovers, load_patches
from line_color_patches import apply_to_statuses as apply_line_color_patches
from line_color_patches import load_patches as load_line_color_patches
from line_color_patches import status_id as color_status_id
from line_color_patches import to_css_pair
from home_region import filter_statuses, load_operators
from line_patches import apply_to_statuses as apply_line_patches
from line_patches import load_patches as load_line_patches
from line_patches import resolved_labels as resolved_line_labels
from operator_line_patches import apply_to_statuses as apply_operator_line_patches
from operator_line_patches import load_rules as load_operator_line_rules
from vehicle_roster import load_roster
from station_patches import apply_station_patches
from station_patches import load_patches as load_station_patches
from version import __version__


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def pack_daily(daily):
    """Kompakte Form von dailyFirsts/dailyRepeats für das eingebettete JSON.

    Kategorien, deren Zeilen überall dieselben Felder mit nur Strings haben,
    werden zu Index-Listen in eine gemeinsame String-Tabelle; die übrigen
    bleiben als Objekte. daily.js (`unpackDaily`) baut dieselben Objekte
    zurück. Form: {fields: {kat: [feld, …]}, strings: [...], days: {datum:
    {kat: [[idx, …], …] | [obj, …]}}}.
    """
    # Tage und Kategorien sortiert, damit die String-Tabelle unabhängig von
    # der Einfügereihenfolge der Dicts ist (deterministischer Build).
    shape = {}  # kat -> Feldliste oder None (nicht packbar)
    for date in sorted(daily):
        for cat in sorted(daily[date]):
            for row in daily[date][cat]:
                fields = list(row) if isinstance(row, dict) else None
                ok = fields is not None and all(
                    isinstance(v, str) for v in row.values()
                )
                if cat not in shape:
                    shape[cat] = fields if ok else None
                elif shape[cat] is not None and (not ok or shape[cat] != fields):
                    shape[cat] = None
    strings = []
    index = {}

    def string_id(value):
        i = index.get(value)
        if i is None:
            i = len(strings)
            index[value] = i
            strings.append(value)
        return i

    days = {}
    for date in sorted(daily):
        out = {}
        for cat in sorted(daily[date]):
            rows = daily[date][cat]
            fields = shape.get(cat)
            if fields:
                out[cat] = [[string_id(row[f]) for f in fields] for row in rows]
            else:
                out[cat] = rows
        days[date] = out
    return {
        "fields": {cat: f for cat, f in shape.items() if f},
        "strings": strings,
        "days": days,
    }


def json_for_script(data):
    """Serialisiert `data` so, dass es sicher in einen <script>-Block passt.

    `json.dumps` escapt `<`/`>` nicht, ein `</script>` in einem Freitextfeld (Body,
    Tag-Wert, Stationsname) würde also aus dem Script-Tag ausbrechen. `\\uXXXX` ist
    gültige JSON-String-Syntax, der geparste Wert bleibt unverändert. U+2028/U+2029
    sind dabei, weil `ensure_ascii=False` sie roh durchlässt und sie in
    JS-String-Literalen erst ab ES2019 erlaubt sind.
    """
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for raw, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        blob = blob.replace(raw, escaped)
    return blob


def dubi_flags(tags):
    """Origin/Destination nur gehalten: Tag ``dubi=start`` bzw. ``dubi=ende``.

    Format: Schlüssel ``dubi`` mit Wert ``start``/``ende``, oder der ganze
    String als Schlüssel. Groß/Kleinschreibung egal. Rückgabe: (start, ende).
    """
    start = False
    ende = False
    for t in tags or []:
        key = (t.get("key") or "").strip().lower()
        val = (t.get("value") or "").strip().lower()
        if key == "dubi=start" or (key == "dubi" and val == "start"):
            start = True
        elif key == "dubi=ende" or (key == "dubi" and val == "ende"):
            ende = True
    return start, ende


def next_checkin_pairs(statuses):
    """(statusId, nächste statusId) in Reihenfolge der Check-in-Zeit (createdAt)."""
    timed = []
    for s in statuses:
        created = parse_dt(s.get("createdAt"))
        if created is not None and s.get("id") is not None:
            timed.append((created.timestamp(), s.get("id")))
    timed.sort()
    return [(a[1], b[1]) for a, b in zip(timed, timed[1:])]


def parse_dt(value):
    """Parst einen ISO-8601-Zeitstempel (auch mit 'Z') oder gibt None zurück."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def trip_datetime(status):
    """Liefert (datetime|None, raw_iso) für den Reisezeitpunkt eines Status.

    Reihenfolge: departureReal → manualDeparture → departurePlanned → createdAt.
    HAFAS liefert gelegentlich geplante Zeiten ein Jahr in der Zukunft; weicht der
    Kandidat um mehr als 2 Tage *nach* createdAt ab, wird er verworfen und der
    nächste versucht (typisch: Fallback auf createdAt).
    """
    checkin = status.get("checkin") or {}
    origin = checkin.get("origin") or {}
    created_raw = status.get("createdAt")
    created_dt = parse_dt(created_raw)
    candidates = [
        origin.get("departureReal"),
        checkin.get("manualDeparture"),
        origin.get("departurePlanned"),
        created_raw,
    ]
    for raw in candidates:
        dt = parse_dt(raw)
        if not dt:
            continue
        if created_dt and (dt.date() - created_dt.date()).days > 2:
            continue
        return dt, raw
    return created_dt, created_raw


def delay_minutes(planned, real):
    """Verspätung in Minuten (real − planned) oder None."""
    p, r = parse_dt(planned), parse_dt(real)
    if not p or not r:
        return None
    return round((r - p).total_seconds() / 60)


DELAY_BUCKETS = ("früh", "0–5", "6–15", "16–30", "31–60", ">60", "unbekannt")
WEEKDAY_KEYS = ("0", "1", "2", "3", "4", "5", "6")  # Mo … So
# Trennt Linienname und Operator im internen Linien-Schlüssel (nicht anzeigen).
LINE_SEP = "\x1f"


def line_key(line_name, operator):
    """Interner Linien-Schlüssel: Name + Operator, damit gleichnamige Linien
    verschiedener Betreiber nicht zusammenfallen."""
    return f"{line_name or ''}{LINE_SEP}{operator or ''}"


def split_line_key(lk):
    """Zerlegt den internen Linien-Schlüssel in (name, operator)."""
    s = lk or ""
    i = s.find(LINE_SEP)
    if i < 0:
        return s, ""
    return s[:i], s[i + 1:]


def vehicle_identity(loc_class, number):
    """Fahrzeug intern als (Baureihe, Nummer).

    Gleiche Nummern verschiedener Baureihen bleiben getrennte Fahrzeuge.
    Fehlende Baureihe ist ein eigener Schlüssel und fällt nicht mit einer
    getaggten Baureihe derselben Nummer zusammen.
    """
    return (loc_class or "", number or "")


def vehicle_cross_key(loc_class, number):
    """Zeilenschlüssel der Fahrzeug-Kreuztabellen.

    Nummer + Baureihe, damit gleiche Nummern nicht zusammenfallen. Die
    Baureihe hängt hinter ``LINE_SEP`` und wird nicht mit angezeigt.
    """
    num = number or ""
    loc = loc_class or ""
    if num and loc:
        return f"{num}{LINE_SEP}{loc}"
    return num or loc


def delay_bucket(delay):
    """Verspätungs-Bucket-Label für Kreuztabellen."""
    if delay is None:
        return "unbekannt"
    if delay < 0:
        return "früh"
    if delay <= 5:
        return "0–5"
    if delay <= 15:
        return "6–15"
    if delay <= 30:
        return "16–30"
    if delay <= 60:
        return "31–60"
    return ">60"


class EntityAgg:
    """Akkumulator für Linie / Baureihe / Fahrzeug (einheitliches Metric-Schema)."""

    __slots__ = (
        "count", "distance_km", "duration_min", "points", "segments",
        "delay_sum", "delay_n", "on_time",
        "first", "last", "dates",
        "min_distance_km", "max_distance_km", "min_route", "max_route",
        "vehicles", "loc_classes", "lines", "routes", "weekdays", "months",
        "loc_class_counts", "loc_class_km",
    )

    def __init__(self):
        self.count = 0
        self.distance_km = 0.0
        self.duration_min = 0
        self.points = 0
        self.segments = 0
        self.delay_sum = 0
        self.delay_n = 0
        self.on_time = 0
        self.first = None
        self.last = None
        self.dates = set()
        self.min_distance_km = None
        self.max_distance_km = None
        self.min_route = ""
        self.max_route = ""
        self.vehicles = set()
        self.loc_classes = set()
        self.lines = set()
        self.routes = set()
        self.weekdays = set()
        self.months = set()
        self.loc_class_counts = Counter()
        self.loc_class_km = Counter()

    def add(self, *, distance_km, duration_min, points, segments, delay,
            date_str, route, weekday=None, month="",
            line="", loc_class="", vehicles=None):
        self.count += 1
        self.distance_km += distance_km
        self.duration_min += duration_min
        self.points += points
        self.segments += segments
        if delay is not None:
            self.delay_sum += delay
            self.delay_n += 1
            if delay <= 5:
                self.on_time += 1
        if date_str:
            self.dates.add(date_str)
            if self.first is None or date_str < self.first:
                self.first = date_str
            if self.last is None or date_str > self.last:
                self.last = date_str
        if self.min_distance_km is None or distance_km < self.min_distance_km:
            self.min_distance_km = distance_km
            self.min_route = route
        if self.max_distance_km is None or distance_km > self.max_distance_km:
            self.max_distance_km = distance_km
            self.max_route = route
        if route:
            self.routes.add(route)
        if weekday is not None:
            self.weekdays.add(str(weekday))
        if month:
            self.months.add(month)
        if line:
            self.lines.add(line)
        if loc_class:
            self.loc_classes.add(loc_class)
            self.loc_class_counts[loc_class] += 1
            self.loc_class_km[loc_class] += distance_km
        for v in vehicles or ():
            # Identität ist (Baureihe, Nummer); nackte Nummern zählen als ohne BR.
            if isinstance(v, tuple):
                loc, num = v[0], v[1] if len(v) > 1 else ""
                if num:
                    self.vehicles.add((loc or "", num))
            elif v:
                self.vehicles.add(("", v))

    def to_row(self, key, kind):
        """Serialisiert den Bucket; `kind` steuert die unique-*-Felder."""
        n = self.count or 1
        row = {
            "key": key,
            "count": self.count,
            "distanceKm": round(self.distance_km, 1),
            "durationMin": self.duration_min,
            "points": self.points,
            "avgDistanceKm": round(self.distance_km / n, 1),
            "avgDurationMin": round(self.duration_min / n, 1),
            "first": self.first,
            "last": self.last,
            "minDistanceKm": self.min_distance_km,
            "maxDistanceKm": self.max_distance_km,
            "minRoute": self.min_route,
            "maxRoute": self.max_route,
            "avgDelay": (
                round(self.delay_sum / self.delay_n, 1) if self.delay_n else None
            ),
            "onTimePct": (
                round(100.0 * self.on_time / self.delay_n, 1) if self.delay_n else None
            ),
            "segments": self.segments,
            "uniqueRoutes": len(self.routes),
            "uniqueWeekdays": len(self.weekdays),
            "uniqueMonths": len(self.months),
        }
        if kind == "line":
            row["uniqueVehicles"] = len(self.vehicles)
            row["uniqueLocClasses"] = len(self.loc_classes)
        elif kind == "locClass":
            row["uniqueVehicles"] = len(self.vehicles)
            row["uniqueLines"] = len(self.lines)
        elif kind == "vehicle":
            row["uniqueLines"] = len(self.lines)
        return row


def _finalize_entities(aggs, kind, limit=None):
    rows = []
    for k, a in aggs.items():
        if kind == "vehicle":
            loc, num = k
            if not num:
                continue
            row = a.to_row(num, kind)
            row["locClass"] = loc or ""
            rows.append(row)
        elif k:
            rows.append(a.to_row(k, kind))
    rows.sort(key=lambda r: (
        -r["distanceKm"], -r["count"], str(r["key"]), str(r.get("locClass") or ""),
    ))
    if limit is not None:
        rows = rows[:limit]
    return rows


class EdgeAgg:
    """Akkumulator für eine gerichtete Segmentkante (fromId → toId)."""

    __slots__ = (
        "from_name", "to_name", "count", "first", "last", "dates",
        "vehicles", "lines", "loc_classes",
    )

    def __init__(self, from_name, to_name):
        self.from_name = from_name or ""
        self.to_name = to_name or ""
        self.count = 0
        self.first = None
        self.last = None
        self.dates = set()
        self.vehicles = set()
        self.lines = set()
        self.loc_classes = set()

    def add(self, date_str, *, line="", loc_class="", vehicles=None):
        self.count += 1
        if date_str:
            self.dates.add(date_str)
            if self.first is None or date_str < self.first:
                self.first = date_str
            if self.last is None or date_str > self.last:
                self.last = date_str
        if line:
            self.lines.add(line)
        if loc_class:
            self.loc_classes.add(loc_class)
        for v in vehicles or ():
            if isinstance(v, tuple):
                loc, num = v[0], v[1] if len(v) > 1 else ""
                if num:
                    self.vehicles.add((loc or "", num))
            elif v:
                self.vehicles.add(("", v))

    def to_row(self):
        return {
            "from": self.from_name,
            "to": self.to_name,
            "count": self.count,
            "first": self.first,
            "last": self.last,
            "uniqueVehicles": len(self.vehicles),
            "uniqueLines": len(self.lines),
            "uniqueLocClasses": len(self.loc_classes),
        }


class StationAgg:
    """Akkumulator für Stationen: Einstieg / Ausstieg / gehalten / physisch."""

    __slots__ = (
        "name", "boarded", "alighted", "through", "passed",
        "first", "last", "dates", "lines",
        "first_used", "dates_used", "first_through", "dates_through",
        "first_pass", "dates_pass",
    )

    def __init__(self, name=""):
        self.name = name or ""
        self.boarded = 0
        self.alighted = 0
        self.through = 0
        self.passed = 0
        self.first = None
        self.last = None
        self.dates = set()
        self.lines = set()
        self.first_used = None
        self.dates_used = set()
        self.first_through = None
        self.dates_through = set()
        self.first_pass = None
        self.dates_pass = set()

    def _touch(self, date_str, line=""):
        if date_str:
            self.dates.add(date_str)
            if self.first is None or date_str < self.first:
                self.first = date_str
            if self.last is None or date_str > self.last:
                self.last = date_str
        if line:
            self.lines.add(line)

    def _touch_used(self, date_str):
        if not date_str:
            return
        self.dates_used.add(date_str)
        if self.first_used is None or date_str < self.first_used:
            self.first_used = date_str

    def _touch_through(self, date_str):
        if not date_str:
            return
        self.dates_through.add(date_str)
        if self.first_through is None or date_str < self.first_through:
            self.first_through = date_str

    def add_boarded(self, date_str, line=""):
        self.boarded += 1
        self._touch(date_str, line)
        self._touch_used(date_str)

    def add_alighted(self, date_str, line=""):
        self.alighted += 1
        self._touch(date_str, line)
        self._touch_used(date_str)

    def add_through(self, date_str, line=""):
        self.through += 1
        self._touch(date_str, line)
        self._touch_through(date_str)

    def _touch_pass(self, date_str):
        if not date_str:
            return
        self.dates_pass.add(date_str)
        if self.first_pass is None or date_str < self.first_pass:
            self.first_pass = date_str

    def add_pass(self, date_str, line=""):
        self.passed += 1
        self._touch(date_str, line)
        self._touch_pass(date_str)

    def to_row(self):
        return {
            "key": self.name,
            "boarded": self.boarded,
            "alighted": self.alighted,
            "through": self.through,
            "passed": self.passed,
            "total": self.boarded + self.alighted + self.through + self.passed,
            "first": self.first,
            "last": self.last,
            "uniqueLines": len(self.lines),
        }


class DatedCombo:
    """Zähler mit first/last für Kombi-Listen (Fahrzeug×Kante×…)."""

    __slots__ = ("count", "first", "last", "dates", "lines")

    def __init__(self):
        self.count = 0
        self.first = None
        self.last = None
        self.dates = set()
        self.lines = set()

    def add(self, date_str, line=""):
        self.count += 1
        if date_str:
            self.dates.add(date_str)
            if self.first is None or date_str < self.first:
                self.first = date_str
            if self.last is None or date_str > self.last:
                self.last = date_str
        if line:
            self.lines.add(line)


def _get_dated(mapping, key):
    a = mapping.get(key)
    if a is None:
        a = DatedCombo()
        mapping[key] = a
    return a


class LineStationRole:
    """Rollen einer Station auf einer Linie (Ein/Aus/gehalten/Durchfahrt)."""

    __slots__ = ("boarded", "alighted", "through", "passed")

    def __init__(self):
        self.boarded = 0
        self.alighted = 0
        self.through = 0
        self.passed = 0


# Perlschnur-Flags je Halt: global Ein/Aus, Ein/Aus auf der Linie, nur Durchfahrt.
ROUTE_FLAG_USED = 1
ROUTE_FLAG_USED_LINE = 2
ROUTE_FLAG_PASS_ONLY = 4


def cover_directed_paths(edge_counts, name_of, seq_counts=None):
    """Überdeckt gerichtete Kanten durch häufigste Kantenfolgen (greedy).

    An Verzweigungen gewinnt die Folge, die am oftsten hintereinander befahren
    wurde (A→B→C häufiger als A→B→D). Bei gleicher Folgehäufigkeit der längere
    Restweg. Unverzweigte Ketten werden mitgenommen. Unmittelbare Gegenkante
    (nach A→B nicht B→A) ist unzulässig; Stationen dürfen sonst wieder
    vorkommen (Rundfahrt). Kanten auf dem aktuellen Pfad nicht wiederholen.
    `edge_counts`: {(a_id, b_id): count}. `seq_counts`: {(a, b, c): count}.
    `name_of(id)` für den Tie-Break. Rückgabe: [{"loop", "ids", "counts"}, ...]
    """
    unused = dict(edge_counts)
    seq = seq_counts or {}
    routes = []

    def adj():
        fwd, rev = {}, {}
        for a, b in unused:
            fwd.setdefault(a, []).append(b)
            rev.setdefault(b, []).append(a)
        return fwd, rev

    def path_edge(u, v, forward):
        return (u, v) if forward else (v, u)

    def max_chain(start, prev, used_edges, nbrs, forward):
        best = 0

        def dfs(u, prev_u, n, used):
            nonlocal best
            if n > best:
                best = n
            for w in nbrs.get(u, ()):
                if w == prev_u:
                    continue
                edge = path_edge(u, w, forward)
                if edge in used or edge not in unused:
                    continue
                dfs(w, u, n + 1, used | {edge})

        dfs(start, prev, 0, used_edges)
        return best

    def pick_hop(path, used_edges, forward, fwd, rev):
        if forward:
            u = path[-1]
            pred = path[-2] if len(path) >= 2 else None
            nbrs = fwd
        else:
            u = path[0]
            pred = path[1] if len(path) >= 2 else None
            nbrs = rev
        cands = []
        for v in nbrs.get(u, ()):
            if pred is not None and v == pred:
                continue
            edge = path_edge(u, v, forward)
            if edge in used_edges or edge not in unused:
                continue
            cands.append(v)
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]

        def key(v):
            edge = path_edge(u, v, forward)
            extra = used_edges | {edge}
            if pred is None:
                freq = unused.get(edge, 0)
            elif forward:
                freq = seq.get((pred, u, v), 0)
            else:
                freq = seq.get((v, u, pred), 0)
            return (
                -freq,
                -max_chain(v, u, extra, nbrs, forward),
                name_of(v),
            )

        cands.sort(key=key)
        return cands[0]

    def grow():
        if not unused:
            return None
        seed = None
        for (a, b), n in unused.items():
            cand = (n, name_of(a), name_of(b), a, b)
            if seed is None or cand[0] > seed[0] or (
                cand[0] == seed[0] and cand[1:3] < seed[1:3]
            ):
                seed = cand
        path = [seed[3], seed[4]]
        used_edges = {(seed[3], seed[4])}
        while True:
            fwd, rev = adj()
            nxt = pick_hop(path, used_edges, True, fwd, rev)
            if nxt is None:
                break
            used_edges.add((path[-1], nxt))
            path.append(nxt)
        while True:
            fwd, rev = adj()
            prv = pick_hop(path, used_edges, False, fwd, rev)
            if prv is None:
                break
            used_edges.add((prv, path[0]))
            path.insert(0, prv)
        return path, used_edges

    while unused:
        grown = grow()
        if not grown:
            break
        path, used_edges = grown
        if not path or len(path) < 2:
            break
        counts = [unused[(a, b)] for a, b in zip(path, path[1:])]
        for a, b in zip(path, path[1:]):
            unused.pop((a, b), None)
        loop = False
        if len(path) > 2 and path[0] == path[-1]:
            loop = True
        elif len(path) > 2:
            close = (path[-1], path[0])
            last_rev = (path[-1], path[-2])
            if close in unused and close not in used_edges and close != last_rev:
                counts.append(unused.pop(close))
                loop = True
        routes.append({"loop": loop, "ids": path, "counts": counts})
    return routes


def _bump_cross(store, row, col, km):
    """store[(row,col)] = [count, distanceKm]."""
    if row is None or col is None or row == "" or col == "":
        return
    cell = store.get((row, col))
    if cell is None:
        store[(row, col)] = [1, km]
    else:
        cell[0] += 1
        cell[1] += km


def _pack_cross(store, row_order=None, col_order=None, row_limit=None, col_limit=None):
    """Baut {rows, cols, counts, kms} aus dem Cross-Store."""
    if not store:
        return {"rows": [], "cols": [], "counts": [], "kms": []}
    row_tot = Counter()
    col_tot = Counter()
    for (r, c), (n, _km) in store.items():
        row_tot[r] += n
        col_tot[c] += n
    if row_order is None:
        rows = [r for r, _ in row_tot.most_common()]
    else:
        rows = [r for r in row_order if r in row_tot]
        rows += [r for r, _ in row_tot.most_common() if r not in rows]
    if col_order is None:
        cols = [c for c, _ in col_tot.most_common()]
    else:
        cols = [c for c in col_order if c in col_tot]
        cols += [c for c, _ in col_tot.most_common() if c not in cols]
    if row_limit is not None:
        rows = rows[:row_limit]
    if col_limit is not None:
        cols = cols[:col_limit]
    row_set, col_set = set(rows), set(cols)
    counts = [[0] * len(cols) for _ in rows]
    kms = [[0.0] * len(cols) for _ in rows]
    ri = {r: i for i, r in enumerate(rows)}
    ci = {c: i for i, c in enumerate(cols)}
    for (r, c), (n, km) in store.items():
        if r not in row_set or c not in col_set:
            continue
        counts[ri[r]][ci[c]] = n
        kms[ri[r]][ci[c]] = round(km, 1)
    return {"rows": rows, "cols": cols, "counts": counts, "kms": kms}


def traveled_stopovers(status):
    """Gibt das tatsächlich befahrene Teilstück der Trip-Stopovers zurück.

    Slice von `checkin.origin` bis `checkin.destination` (inklusive). Bevorzugt
    `stopoverId` (eindeutig bei Ringfahrten); Fallback: erste Origin-Station,
    dann erste Ziel-Station mit Index strikt nach dem Start. Fällt auf die
    komplette Stopover-Liste bzw. origin/destination zurück, wenn die Grenzen
    nicht eindeutig bestimmbar sind. Gibt eine Liste von Stopover-Dicts zurück.

    ACHTUNG: identische Kopie in download_statuses.py – Änderungen dort
    mitpflegen (die beiden Skripte importieren sich bewusst nicht gegenseitig).
    """
    checkin = status.get("checkin") or {}
    trip = status.get("trip") or {}
    stopovers = trip.get("stopovers") or []
    origin = checkin.get("origin") or {}
    destination = checkin.get("destination") or {}

    if not stopovers:
        # Kein Trip: nur Start/Ziel aus dem Checkin (haben keine Koordinaten,
        # liefern aber wenigstens die IDs für die Fallback-Kante).
        return [s for s in (origin, destination) if s.get("id") is not None]

    def index_of_stopover_id(stopover_id):
        if stopover_id is None:
            return None
        for i, s in enumerate(stopovers):
            if s.get("stopoverId") == stopover_id:
                return i
        return None

    def index_of_station(station_id, after=-1):
        if station_id is None:
            return None
        for i, s in enumerate(stopovers):
            if i > after and s.get("id") == station_id:
                return i
        return None

    i_start = index_of_stopover_id(origin.get("stopoverId"))
    if i_start is None:
        i_start = index_of_station(origin.get("id"))

    i_end = index_of_stopover_id(destination.get("stopoverId"))
    if i_end is None and i_start is not None:
        # strikt nach Start: Ringfahrten (gleiche Stations-id) treffen den Rückkehr-Halt
        i_end = index_of_station(destination.get("id"), after=i_start)
    if i_end is None:
        i_end = index_of_station(destination.get("id"))

    if i_start is None or i_end is None or i_start > i_end:
        return stopovers
    return stopovers[i_start : i_end + 1]


def consecutive_station_pairs(stopovers):
    """Gerichtete Paare aufeinanderfolgender, nicht-cancelled Stationen.

    Ausgefallene Zwischenhalte werden übersprungen (A→B✕→C wird zu A→C).
    Paare ohne IDs oder mit identischer Station entfallen. Rückgabe:
    [(a_id, b_id, a_name, b_name), ...].
    """
    kept = [s for s in (stopovers or []) if not s.get("cancelled")]
    out = []
    for a, b in zip(kept, kept[1:]):
        a_id, b_id = a.get("id"), b.get("id")
        if a_id is None or b_id is None or a_id == b_id:
            continue
        out.append((
            a_id,
            b_id,
            a.get("name") or "",
            b.get("name") or "",
        ))
    return out


def load_loc_class_families(path):
    """Liest die manuelle Baureihe→Familie-Zuordnung.

    JSON-Objekt-Syntax, in der derselbe Schlüssel mehrfach vorkommen darf
    (eine Baureihe in mehreren Familien). `json.load` würde sonst nur den
    letzten Wert behalten, daher `object_pairs_hook`. Schlüssel mit
    führendem '_' werden übersprungen. Fehlt die Datei, wird {} zurückgegeben.

    Rückgabe: {Baureihe: [Familie, ...]} in Dateireihenfolge, ohne
    doppelte Familie je Baureihe.
    """
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            pairs = json.load(f, object_pairs_hook=list)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Baureihenfamilien {path} nicht lesbar: {e}") from e
    if not isinstance(pairs, list):
        raise ValueError(
            f"Baureihenfamilien {path}: erwartet ein Objekt "
            f"{{Baureihe: Familie}}, bekommen {type(pairs).__name__}."
        )

    out = {}
    for item in pairs:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise ValueError(
                f"Baureihenfamilien {path}: ungültiger Eintrag {item!r}."
            )
        key, value = item
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"Baureihenfamilien {path}: Wert für {key!r} muss "
                f"ein nicht-leerer String sein."
            )
        families = out.setdefault(key, [])
        if value not in families:
            families.append(value)
    return out


def pack_loc_class_families(mapping, variants):
    """Invertiert Baureihe→Familie(n) zu Familie→[Baureihen], nur Treffer in variants."""
    if not mapping:
        return {}
    present = {v[1] for v in variants if len(v) > 1 and v[1]}
    members = {}
    for loc_class, families in mapping.items():
        if loc_class not in present:
            continue
        if isinstance(families, str):
            families = [families]
        for family in families:
            bucket = members.setdefault(family, [])
            if loc_class not in bucket:
                bucket.append(loc_class)
    return {fam: sorted(cls) for fam, cls in sorted(members.items())}


def build_data(statuses, stations, ignore_plus=False, loc_class_families=None,
               edge_patches=None, station_patches=None, line_color_patches=None,
               boarding_patches=None, edge_measures=None):
    """Berechnet KPIs, Tabellenzeilen und Kanten für das Dashboard.

    `ignore_plus`: wenn True, werden Wagennummern-Tags nicht am '+' getrennt
    (Doppeltraktion wie "463001+463501" bleibt ein Fahrzeug); Standard trennt.
    `loc_class_families`: optionales Mapping Baureihe → [Familie, ...] für den
    Kartenfilter (nur Familien mit mindestens einem Treffer in den Daten).
    `edge_patches`: optionale Default-/Override-Via-Stationen (siehe
    edge_patches.py); fehlend = keine Expansion.
    `station_patches`: optionale Koordinaten-Moves und ID-Merges (siehe
    station_patches.py); werden vor traveled_stopovers / Edge-Patches angewandt.
    `line_color_patches`: optionale routeColor-Overrides je Status (siehe
    line_color_patches.py); gelten vor der Linienfarben-Sammlung.
    `boarding_patches`: optionale Einstiege je Status (siehe boarding_patches.py).
    Gelten nach den Stations-Patches und vor der Linienfarbe. `edge_measures`
    sind Kantenkilometer aus allen Statuses; fehlen sie, werden sie aus den
    hier übergebenen Statuses gelernt.
    """
    sp = station_patches or {}
    if (sp.get("moves") or sp.get("merges")):
        stations, statuses, edge_patches = apply_station_patches(
            station_patches, stations, statuses, edge_patches
        )
    board_over = (boarding_patches or {}).get("overrides") or {}
    if board_over:
        if edge_measures is None:
            edge_measures = learn_edge_measures(statuses)
        statuses = apply_boarding_patches(
            boarding_patches, statuses, edge_measures
        )
    color_over = (line_color_patches or {}).get("overrides") or {}
    if color_over:
        statuses = apply_line_color_patches(line_color_patches, statuses)
    total_distance_m = 0
    total_duration_min = 0
    total_points = 0
    lines = set()
    station_ids = set()
    dates = []

    trips = []
    vehicle_records = []           # ein Datensatz je getaggter Wagennummer x Fahrt
    edge_counter = Counter()       # gerichtetes (id_from, id_to) -> Anzahl (je Fahrtrichtung)
    node_counter = Counter()       # station_id -> Befahrungen (eine je Station pro Fahrt)
    edge_names = {}                # id -> Name (für Anzeige)
    line_colors = {}               # lineKey (Name+Operator) -> [bg, fg]
    patched_line_keys = set()      # Linien, deren Farbe aus einem Status-Patch kommt
    # Nach Fahrt-Attributen (Linie, Baureihe, Kategorie, Operator, Jahr)
    # aufgeschlüsselte Zähler für die filterbare Karte; JS summiert später die zum
    # Filter passenden Buckets auf. Die Attribut-Kombis werden beim Serialisieren
    # zu einer variants-Liste dedupliziert, damit das JSON kompakt bleibt.
    edge_lc = Counter()            # (id_from, id_to, *variant) -> Anzahl
    node_lc = Counter()            # (station_id, *variant) -> Befahrungen
    node_lc_used = Counter()       # (station_id, *variant) -> Ein-/Ausstiege
    node_lc_held = Counter()       # (station_id, *variant) -> gehalten (sitzegeblieben)
    node_lc_pass = Counter()       # (station_id, *variant) -> physische Durchfahrt
    # Fahrzeug-Buckets separat: Mehrfachwagen würden in der Variante beim
    # Aufsummieren von "Alle" doppelt zählen. Bei Fahrzeug-Filter greift JS
    # auf diese Buckets zu; ohne Filter bleiben edge_lc/node_lc maßgeblich.
    edge_veh = Counter()           # (id_from, id_to, veh_key, *variant) -> Anzahl
    # Gefahrene Kantenfolgen A→S→B innerhalb einer Fahrt, für die Kurven der
    # Karten-Pfadmodi (welche Kante an S tatsächlich auf welche folgt).
    seq_lc = Counter()             # (a_id, s_id, b_id, *variant) -> Anzahl
    seq_veh = Counter()            # (a_id, s_id, b_id, veh_key, *variant) -> Anzahl
    # Durchbindung mit Linienwechsel bei gleicher Baureihe: nur für den
    # Baureihen-Modus, nicht für die Linien.
    seq_loc = Counter()            # (a_id, s_id, b_id, *variant) -> Anzahl
    # Erste/letzte Kante je dubi-Fahrt, um Durchbindungen (dubi=ende → dubi=start)
    # nach der Schleife als gefahrene Folge über zwei Check-ins zu zählen.
    dubi_ends = {}  # statusId -> (dubi_start, dubi_ende, erste, letzte, lk, variant, veh_keys)
    node_veh = Counter()           # (station_id, veh_key, *variant) -> Befahrungen
    # Entdeckte Kanten je Fahrt/Variante (volle Stopovers minus Check-in dieser
    # Fahrt). Nicht global abziehen: eine auf Linie A befahrene Kante bleibt
    # für Linie B entdeckt, wenn B sie nur im Laufweg gesehen hat.
    discovered_candidates = []     # (a_id, b_id, variant, veh_keys)
    node_veh_used = Counter()      # (station_id, veh_key, *variant) -> Ein-/Ausstiege
    node_veh_held = Counter()      # (station_id, veh_key, *variant) -> gehalten
    node_veh_pass = Counter()      # (station_id, veh_key, *variant) -> physische Durchfahrt

    # Statistik-Akkumulatoren (Linie / Baureihe / Fahrzeug + Kreuztabellen).
    line_aggs = {}
    loc_aggs = {}
    veh_aggs = {}  # (locClass, number) -> EntityAgg
    cross_stores = {
        "crossLocClassLine": {},
        "crossVehicleLine": {},
        "crossLineMonth": {},
        "crossLineWeekday": {},
        "crossLocClassMonth": {},
        "crossLocClassWeekday": {},
        "crossLocClassCategory": {},
        "crossLineCategory": {},
        "crossLineDelay": {},
        "crossLocClassDelay": {},
        "crossVehicleMonth": {},
    }
    # Unique-Kombis mit first/last (für Tagesziele + KPI-Zähler).
    line_vehicle = {}  # (lineKey, locClass, veh) -> DatedCombo
    line_loc = {}  # (lineKey, locClass) -> DatedCombo
    vehicle_loc = {}  # (veh, locClass) -> DatedCombo
    uniq_routes = set()
    station_line = {}  # (station_id, lineKey) -> DatedCombo
    line_station_role = {}  # (station_id, lineKey) -> LineStationRole
    # Pro Check-in für den Datumsfilter der Linienseite:
    # [date, lineKey, locClass, km, [[sid, rollenBits], ...]]
    line_rides = []
    line_stop_names = {}
    # Parallel zu `trips` für den Datumsfilter der Statistikseite.
    # Kanten: [[fromId, toId], ...] je Fahrt (nach Via-Patches).
    # Stationen: [[sid, rollenBits], ...] — 1 Ein, 2 Aus, 4 gehalten, 8 physisch.
    stat_edges = []
    stat_stops = []
    trips_with_loc = 0
    trips_with_veh = 0
    min_trip = None  # {line, from, to, km, date}
    max_trip = None
    # Segmentkanten + Wiederholungs-Kombis für Statistik-Tab.
    edge_aggs = {}  # (from_id, to_id) -> EdgeAgg
    veh_edge = {}  # (locClass, veh, from_id, to_id) -> DatedCombo
    veh_edge_line = {}  # (locClass, veh, from_id, to_id, lineKey) -> DatedCombo
    line_edge = {}  # (lineKey, from_id, to_id) -> DatedCombo
    line_seq = Counter()  # (lineKey, a_id, b_id, c_id) -> aufeinanderfolgende Kanten
    loc_edge = {}  # (locClass, from_id, to_id) -> DatedCombo
    loc_edge_line = {}  # (locClass, from_id, to_id, lineKey) -> DatedCombo
    station_aggs = {}  # station_id -> StationAgg (Ein/Aus/gehalten/physisch)

    def get_agg(mapping, key):
        a = mapping.get(key)
        if a is None:
            a = EntityAgg()
            mapping[key] = a
        return a

    def get_edge(a_id, b_id, a_name, b_name):
        key = (a_id, b_id)
        e = edge_aggs.get(key)
        if e is None:
            e = EdgeAgg(a_name, b_name)
            edge_aggs[key] = e
        return e

    def get_station(sid, name=""):
        a = station_aggs.get(sid)
        if a is None:
            a = StationAgg(name)
            station_aggs[sid] = a
        elif name and (not a.name or a.name == str(sid)):
            a.name = name
        return a

    ride_bits = {}
    _ROLE_BITS = {"boarded": 1, "alighted": 2, "through": 4, "passed": 8}

    def mark_line_role(sid, lk, attr):
        if sid is None or not lk:
            return
        key = (sid, lk)
        r = line_station_role.get(key)
        if r is None:
            r = LineStationRole()
            line_station_role[key] = r
        setattr(r, attr, getattr(r, attr) + 1)
        bit = _ROLE_BITS.get(attr)
        if bit:
            ride_bits[sid] = ride_bits.get(sid, 0) | bit

    for status in statuses:
        ride_bits.clear()
        trip_edges = []
        trip_stop_bits = {}
        trip_stop_order = []

        def note_stop(sid, bit):
            if sid is None:
                return
            cur = trip_stop_bits.get(sid)
            if cur is None:
                trip_stop_order.append(sid)
                trip_stop_bits[sid] = bit
            else:
                trip_stop_bits[sid] = cur | bit

        checkin = status.get("checkin") or {}
        origin = checkin.get("origin") or {}
        destination = checkin.get("destination") or {}

        distance = checkin.get("distance") or 0
        duration = checkin.get("duration") or 0
        points = checkin.get("points")
        if points is None:
            points = status.get("points") or 0

        total_distance_m += distance
        total_duration_min += duration
        total_points += points

        dt, dep = trip_datetime(status)
        if dt:
            dates.append(dt)

        # Linie, Baureihe und Wagennummern der Fahrt. Baureihe/Wagennummer stammen
        # aus den Träwelling-Tags (nur bei selbst getaggten Fahrten gesetzt) und
        # werden für die Karte, die Fahrtenliste und die Fahrzeug-Datensätze genutzt.
        line_name = checkin.get("lineName") or ""
        category = checkin.get("category") or ""
        operator = (checkin.get("operator") or {}).get("name") or ""
        # Intern immer Name+Operator, damit z.B. zwei „S1“ nicht zusammenfallen.
        lk = line_key(line_name, operator) if line_name or operator else ""
        if lk:
            lines.add(lk)
        # Linienfarbe (Hintergrund/Text) aus dem Check-in; je (Linie, Operator)
        # einmal. Ein lokaler Status-Patch überschreibt die HAFAS-Farbe der Linie.
        pair = to_css_pair(
            checkin.get("routeColor"), checkin.get("routeTextColor")
        )
        if lk and pair:
            sid = color_status_id(status.get("id"))
            is_patch = sid is not None and sid in color_over
            if is_patch:
                if lk not in patched_line_keys:
                    line_colors[lk] = pair
                    patched_line_keys.add(lk)
            elif lk not in patched_line_keys and lk not in line_colors:
                line_colors[lk] = pair
        tags = status.get("tags") or []
        loc_class = next(
            (
                t.get("value")
                for t in tags
                if t.get("key") == "trwl:locomotive_class" and t.get("value")
            ),
            "",
        )
        veh_values = [
            t.get("value")
            for t in tags
            if t.get("key") == "trwl:vehicle_number" and t.get("value")
        ]
        # Mehrfachwagen (z.B. "381,382") defensiv trennen. Ein "+" (Doppeltraktion,
        # z.B. "463001+463501") wird standardmäßig ebenfalls als Trenner behandelt
        # und ergibt zwei Fahrzeuge; --ignore-plus (ignore_plus) schaltet das ab.
        sep = r"[,;]" if ignore_plus else r"[,;+]"
        veh_numbers = [
            n.strip() for v in veh_values for n in re.split(sep, v) if n.strip()
        ]
        # Statistik zählt Fahrzeuge als (Baureihe, Nummer), nicht nur die Nummer.
        veh_ids = [vehicle_identity(loc_class, n) for n in veh_numbers]

        # Attribut-Variante dieser Fahrt für die filterbaren Karten-Buckets:
        # (Linien-Key, Baureihe, Kategorie, Operator, Datum). Das Jahr-Dropdown
        # leitet JS aus dem Datum ab (Präfix YYYY).
        date_str = dt.date().isoformat() if dt else (dep or "")[:10]
        variant = (lk, loc_class, category, operator, date_str)

        segment = traveled_stopovers(status)
        # Fahrzeug-Schlüssel dieser Fahrt: je getaggter Wagennummer (Baureihe, Nr.),
        # sonst ein Leer-Schlüssel für "(ohne Fahrzeug)" auf der Karte.
        veh_keys = [(loc_class, n) for n in veh_numbers] or [("", "")]
        # je Fahrt zählt jede Station nur einmal (trip-lokales Set); über mehrere
        # Fahrten hinweg summieren sich die Befahrungen aber weiter auf.
        seen_in_trip = set()

        def touch_visit(sid, name=None):
            if sid is None:
                return
            station_ids.add(sid)
            if name:
                edge_names[sid] = name
            elif sid not in edge_names:
                edge_names[sid] = str(sid)
            if sid not in seen_in_trip:
                seen_in_trip.add(sid)
                node_counter[sid] += 1
                node_lc[(sid, *variant)] += 1
                for vk in veh_keys:
                    node_veh[(sid, vk, *variant)] += 1

        def mark_used_node(sid):
            """Ein-/Ausstieg dieser Fahrt für die Karten-Marker (filtertreu)."""
            node_lc_used[(sid, *variant)] += 1
            for vk in veh_keys:
                node_veh_used[(sid, vk, *variant)] += 1

        def mark_held_node(sid):
            node_lc_held[(sid, *variant)] += 1
            for vk in veh_keys:
                node_veh_held[(sid, vk, *variant)] += 1

        def mark_pass_node(sid):
            node_lc_pass[(sid, *variant)] += 1
            for vk in veh_keys:
                node_veh_pass[(sid, vk, *variant)] += 1

        # Entfällt-Zwischenhalte weder als Knoten noch als gehalten. Ein- und
        # Ausstieg bleiben, auch wenn der Halt selbst cancelled ist.
        n_seg = len(segment)
        for i, s in enumerate(segment):
            if s.get("cancelled") and 0 < i < n_seg - 1:
                continue
            sid = s.get("id")
            if sid is None:
                continue
            touch_visit(sid, s.get("name"))

        # Station-Rollen auf der *Original*-Haltfolge: Einstieg = segment[0],
        # Ausstieg = segment[-1], gehalten = nicht-cancelled Träwelling-Zwischenhalte.
        # dubi=start / dubi=ende: Origin/Destination zählen als gehalten, nicht genutzt.
        # len==1: dieselbe Station zählt als Ein und Aus, nicht als gehalten
        # (außer wenn dubi das jeweilige Ende auf gehalten umbiegt).
        dubi_start, dubi_ende = dubi_flags(tags)
        if segment:
            s0 = segment[0]
            sid0 = s0.get("id")
            seen_through = set()

            def mark_held_once(sid, name=""):
                if sid is None or sid in seen_through:
                    return
                seen_through.add(sid)
                get_station(
                    sid, name or edge_names.get(sid) or str(sid)
                ).add_through(date_str, line=lk)
                note_stop(sid, 4)
                mark_held_node(sid)
                mark_line_role(sid, lk, "through")

            if sid0 is not None:
                name0 = s0.get("name") or edge_names.get(sid0) or str(sid0)
                if dubi_start:
                    mark_held_once(sid0, name0)
                else:
                    get_station(sid0, name0).add_boarded(date_str, line=lk)
                    note_stop(sid0, 1)
                    mark_used_node(sid0)
                    mark_line_role(sid0, lk, "boarded")
                if lk:
                    _get_dated(station_line, (sid0, lk)).add(date_str)
            if len(segment) == 1:
                if sid0 is not None:
                    if dubi_ende:
                        name0 = s0.get("name") or edge_names.get(sid0) or str(sid0)
                        mark_held_once(sid0, name0)
                    else:
                        get_station(sid0).add_alighted(date_str, line=lk)
                        note_stop(sid0, 2)
                        mark_line_role(sid0, lk, "alighted")
                        if dubi_start:
                            mark_used_node(sid0)
            else:
                sn = segment[-1]
                sidn = sn.get("id")
                if sidn is not None:
                    namen = sn.get("name") or edge_names.get(sidn) or str(sidn)
                    if dubi_ende:
                        mark_held_once(sidn, namen)
                    else:
                        get_station(sidn, namen).add_alighted(date_str, line=lk)
                        note_stop(sidn, 2)
                        mark_line_role(sidn, lk, "alighted")
                        if sidn != sid0 or dubi_start:
                            mark_used_node(sidn)
                    if lk:
                        _get_dated(station_line, (sidn, lk)).add(date_str)
                for s in segment[1:-1]:
                    if s.get("cancelled"):
                        continue
                    sid = s.get("id")
                    if sid is None or sid in seen_through:
                        continue
                    mark_held_once(
                        sid, s.get("name") or edge_names.get(sid) or str(sid)
                    )
                    if lk:
                        _get_dated(station_line, (sid, lk)).add(date_str)
        # gerichtete Kanten zwischen aufeinanderfolgenden *nicht* cancelled Halten:
        # ausgefallene Zwischenhalte überspringen (A→B✕→C wird zu A→C).
        # Via-Patches splitten grobe A→B in topologische Teilstücke.
        edge_segment = [s for s in segment if not s.get("cancelled")]
        expanded = expand_edge_stopovers(
            edge_segment, status.get("id"), edge_patches, stations
        )
        # Check-in dieser Fahrt (unpatcht + Via-Teilstücke): gilt nicht als entdeckt.
        trip_covered = {
            (a_id, b_id)
            for a_id, b_id, _, _ in consecutive_station_pairs(segment)
        }
        seen_pass = set()
        for s in expanded:
            if not s.get("physicalThrough"):
                continue
            sid = s.get("id")
            if sid is None or sid in seen_pass:
                continue
            seen_pass.add(sid)
            touch_visit(sid, s.get("name"))
            get_station(
                sid, s.get("name") or edge_names.get(sid) or str(sid)
            ).add_pass(date_str, line=lk)
            note_stop(sid, 8)
            mark_pass_node(sid)
            mark_line_role(sid, lk, "passed")
            # Physische Durchfahrt: kein Halt, also kein Tagesziel Station × Linie.
        prev_edge = None
        first_edge = None
        for a, b in zip(expanded, expanded[1:]):
            a_id, b_id = a.get("id"), b.get("id")
            if a_id is None or b_id is None or a_id == b_id:
                continue
            if prev_edge is not None and prev_edge[1] == a_id:
                if lk:
                    line_seq[(lk, prev_edge[0], a_id, b_id)] += 1
                seq_lc[(prev_edge[0], a_id, b_id, *variant)] += 1
                for vk in veh_keys:
                    seq_veh[(prev_edge[0], a_id, b_id, vk, *variant)] += 1
            prev_edge = (a_id, b_id)
            if first_edge is None:
                first_edge = prev_edge
            edge_counter[(a_id, b_id)] += 1
            edge_lc[(a_id, b_id, *variant)] += 1
            trip_covered.add((a_id, b_id))
            for vk in veh_keys:
                edge_veh[(a_id, b_id, vk, *variant)] += 1
            a_name = a.get("name") or edge_names.get(a_id) or str(a_id)
            b_name = b.get("name") or edge_names.get(b_id) or str(b_id)
            get_edge(a_id, b_id, a_name, b_name).add(
                date_str, line=lk, loc_class=loc_class, vehicles=veh_ids
            )
            trip_edges.append([a_id, b_id])
            if lk:
                _get_dated(line_edge, (lk, a_id, b_id)).add(date_str)
            if loc_class:
                _get_dated(loc_edge, (loc_class, a_id, b_id)).add(date_str)
                if lk:
                    _get_dated(loc_edge_line, (loc_class, a_id, b_id, lk)).add(date_str)
            for loc, num in veh_ids:
                ve = _get_dated(veh_edge, (loc, num, a_id, b_id))
                ve.add(date_str, line=lk)
                if lk:
                    _get_dated(veh_edge_line, (loc, num, a_id, b_id, lk)).add(date_str)
        if first_edge is not None and (dubi_start or dubi_ende):
            dubi_ends[status.get("id")] = (
                dubi_start, dubi_ende, first_edge, prev_edge, lk, variant, veh_keys
            )
        if lk:
            path = []
            for s in expanded:
                sid = s.get("id")
                if sid is None:
                    continue
                if path and path[-1][0] == sid:
                    continue
                path.append([sid, ride_bits.get(sid, 0)])
                label = s.get("name") or edge_names.get(sid) or str(sid)
                prev_name = line_stop_names.get(str(sid))
                if not prev_name or prev_name == str(sid):
                    line_stop_names[str(sid)] = label
            line_rides.append([
                date_str,
                lk,
                loc_class,
                round(distance / 1000, 1),
                path,
            ])

        # Entdeckte Kanten: Folgepaare der vollen Trip-Stopovers außerhalb
        # des Check-ins dieser Fahrt. Andere Fahrten (andere Linie/Datum/…)
        # schließen sie nicht aus – das macht der Kartenfilter clientseitig.
        full_stopovers = (status.get("trip") or {}).get("stopovers") or []
        for a_id, b_id, a_name, b_name in consecutive_station_pairs(full_stopovers):
            if (a_id, b_id) in trip_covered:
                continue
            if a_name:
                edge_names[a_id] = a_name
            if b_name:
                edge_names[b_id] = b_name
            discovered_candidates.append(
                (a_id, b_id, variant, tuple(veh_keys))
            )

        # Detail-Stopovers für die Tabelle
        detail = [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "arrivalPlanned": s.get("arrivalPlanned"),
                "arrivalReal": s.get("arrivalReal"),
                "departurePlanned": s.get("departurePlanned"),
                "departureReal": s.get("departureReal"),
                "platform": s.get("departurePlatformReal")
                or s.get("departurePlatformPlanned")
                or s.get("platform"),
                "cancelled": s.get("cancelled", False),
            }
            for s in segment
        ]

        stat_edges.append(trip_edges)
        stat_stops.append(
            [[sid, trip_stop_bits[sid]] for sid in trip_stop_order]
        )
        trips.append(
            {
                "date": dep,
                "line": lk,
                "operator": operator,
                "category": checkin.get("category") or "",
                "locClass": loc_class,
                "vehicles": ", ".join(veh_numbers),
                "from": origin.get("name") or "",
                "to": destination.get("name") or "",
                "depPlanned": origin.get("departurePlanned") or None,
                "depReal": origin.get("departureReal")
                or checkin.get("manualDeparture")
                or None,
                "arrPlanned": destination.get("arrivalPlanned") or None,
                "arrReal": destination.get("arrivalReal")
                or checkin.get("manualArrival")
                or None,
                "viaStops": sum(
                    1 for s in segment[1:-1] if not s.get("cancelled")
                ),
                "distanceKm": round(distance / 1000, 1),
                "durationMin": duration,
                "delay": delay_minutes(
                    destination.get("arrivalPlanned"), destination.get("arrivalReal")
                ),
                "points": points,
                "body": status.get("body") or "",
                "createdAt": status.get("createdAt") or None,
                "stopovers": detail,
            }
        )

        # --- Statistik: Linie / Baureihe / Fahrzeug ---
        distance_km = round(distance / 1000, 1)
        from_name = origin.get("name") or ""
        to_name = destination.get("name") or ""
        route = f"{from_name} → {to_name}" if (from_name or to_name) else ""
        # date_str schon oben für Kanten-Stats gesetzt
        weekday = dt.weekday() if dt else None
        month = date_str[:7] if len(date_str) >= 7 else ""
        delay = delay_minutes(
            destination.get("arrivalPlanned"), destination.get("arrivalReal")
        )
        n_segments = max(0, len(edge_segment) - 1)
        d_bucket = delay_bucket(delay)
        wd_key = str(weekday) if weekday is not None else ""

        if route:
            uniq_routes.add(route)
        trip_info = {
            "line": line_name,
            "from": from_name,
            "to": to_name,
            "km": distance_km,
            "date": date_str,
        }
        if min_trip is None or distance_km < min_trip["km"]:
            min_trip = trip_info
        if max_trip is None or distance_km > max_trip["km"]:
            max_trip = trip_info

        if loc_class:
            trips_with_loc += 1
        if veh_numbers:
            trips_with_veh += 1

        if lk:
            get_agg(line_aggs, lk).add(
                distance_km=distance_km,
                duration_min=duration,
                points=points,
                segments=n_segments,
                delay=delay,
                date_str=date_str,
                route=route,
                weekday=weekday,
                month=month,
                loc_class=loc_class,
                vehicles=veh_ids,
            )
            _bump_cross(cross_stores["crossLineMonth"], lk, month, distance_km)
            _bump_cross(cross_stores["crossLineWeekday"], lk, wd_key, distance_km)
            _bump_cross(
                cross_stores["crossLineCategory"], lk, category, distance_km
            )
            _bump_cross(
                cross_stores["crossLineDelay"], lk, d_bucket, distance_km
            )

        if loc_class:
            get_agg(loc_aggs, loc_class).add(
                distance_km=distance_km,
                duration_min=duration,
                points=points,
                segments=n_segments,
                delay=delay,
                date_str=date_str,
                route=(f"{line_name}: {route}" if line_name else route),
                weekday=weekday,
                month=month,
                line=lk,
                vehicles=veh_ids,
            )
            _bump_cross(
                cross_stores["crossLocClassLine"], loc_class, lk, distance_km
            )
            _bump_cross(
                cross_stores["crossLocClassMonth"], loc_class, month, distance_km
            )
            _bump_cross(
                cross_stores["crossLocClassWeekday"], loc_class, wd_key, distance_km
            )
            _bump_cross(
                cross_stores["crossLocClassCategory"],
                loc_class,
                category,
                distance_km,
            )
            _bump_cross(
                cross_stores["crossLocClassDelay"], loc_class, d_bucket, distance_km
            )
            if lk:
                _get_dated(line_loc, (lk, loc_class)).add(date_str)

        for loc, num in veh_ids:
            get_agg(veh_aggs, (loc, num)).add(
                distance_km=distance_km,
                duration_min=duration,
                points=points,
                segments=n_segments,
                delay=delay,
                date_str=date_str,
                route=(f"{line_name}: {route}" if line_name else route),
                weekday=weekday,
                month=month,
                line=lk,
            )
            label = vehicle_cross_key(loc, num)
            _bump_cross(
                cross_stores["crossVehicleLine"], label, lk, distance_km
            )
            _bump_cross(
                cross_stores["crossVehicleMonth"], label, month, distance_km
            )
            if lk:
                _get_dated(line_vehicle, (lk, loc, num)).add(date_str)
            _get_dated(vehicle_loc, (num, loc)).add(date_str)

        # Fahrzeug-Datensätze aus den Träwelling-Tags (sofern vorhanden).
        # loc_class und veh_numbers sind oben schon ermittelt.
        for num in veh_numbers:
            vehicle_records.append(
                {
                    "vehicleNumber": num,
                    "locClass": loc_class,
                    "line": lk,
                    "category": checkin.get("category") or "",
                    "operator": operator,
                    "date": dt.date().isoformat() if dt else (dep or "")[:10],
                    "from": origin.get("name") or "",
                    "to": destination.get("name") or "",
                    "depTime": dep,
                    "arrTime": destination.get("arrivalReal")
                    or destination.get("arrivalPlanned"),
                    "distanceKm": round(distance / 1000, 1),
                    "durationMin": duration,
                    "points": points,
                    "segments": max(0, len(expanded) - 1),
                }
            )

    discovered_lc = Counter()      # (id_from, id_to, *variant) -> Sichtungen
    discovered_veh = Counter()     # (id_from, id_to, veh_key, *variant) -> Sichtungen
    for a_id, b_id, variant, vks in discovered_candidates:
        discovered_lc[(a_id, b_id, *variant)] += 1
        for vk in vks:
            discovered_veh[(a_id, b_id, vk, *variant)] += 1

    # Segment-Rangliste für die Übersicht: global über alle Fahrten, ungefiltert.
    segment_ranking = []
    for (a_id, b_id), count in edge_counter.items():
        a = stations.get(str(a_id)) or stations.get(a_id) or {}
        b = stations.get(str(b_id)) or stations.get(b_id) or {}
        a_name = a.get("name") or edge_names.get(a_id) or str(a_id)
        b_name = b.get("name") or edge_names.get(b_id) or str(b_id)
        segment_ranking.append({"from": a_name, "to": b_name, "count": count})
    segment_ranking.sort(key=lambda x: x["count"], reverse=True)

    # Karten-Daten nach (Linie, Baureihe) aufgeschlüsselt. Die Koordinaten liegen
    # einmalig in map_stations; Kanten/Knoten referenzieren nur Station-IDs, damit
    # das eingebettete JSON kompakt bleibt. JS summiert die zum Filter passenden
    # Buckets auf und berechnet Farb-/Dicken-Skala je Ansicht neu.
    map_stations = {}

    def station_coord(sid):
        """[lat, lon, name] der Station oder None, falls Koordinaten fehlen."""
        st = stations.get(str(sid)) or stations.get(sid) or {}
        if st.get("latitude") is None or st.get("longitude") is None:
            return None
        name = st.get("name") or edge_names.get(sid) or str(sid)
        return [st["latitude"], st["longitude"], name]

    # Attribut-Varianten deduplizieren: jede eindeutige (line, loc, category,
    # operator, date) erhält einen Index; edges/nodes referenzieren nur den Index,
    # damit sich die Strings im JSON nicht pro Kante/Knoten wiederholen.
    variants = []
    variant_index = {}

    def variant_id(v):
        idx = variant_index.get(v)
        if idx is None:
            idx = len(variants)
            variant_index[v] = idx
            variants.append(list(v))
        return idx

    map_edges = []
    for key, count in edge_lc.items():
        a_id, b_id = key[0], key[1]
        ca, cb = station_coord(a_id), station_coord(b_id)
        if ca is None or cb is None:
            continue
        map_stations[str(a_id)] = ca
        map_stations[str(b_id)] = cb
        map_edges.append([a_id, b_id, variant_id(key[2:]), count])

    map_nodes = []
    for key, cnt in node_lc.items():
        sid = key[0]
        c = station_coord(sid)
        if c is None:
            continue
        map_stations[str(sid)] = c
        map_nodes.append([
            sid, variant_id(key[1:]), cnt,
            node_lc_used.get(key, 0),
            node_lc_held.get(key, 0),
            node_lc_pass.get(key, 0),
        ])

    # Fahrzeug-Liste + Kanten/Knoten mit Fahrzeug-Index (für Karten-Filter).
    map_vehicles = []  # [locClass, number]
    vehicle_index = {}

    def vehicle_id(vk):
        idx = vehicle_index.get(vk)
        if idx is None:
            idx = len(map_vehicles)
            vehicle_index[vk] = idx
            map_vehicles.append(list(vk))
        return idx

    map_veh_edges = []
    for key, count in edge_veh.items():
        a_id, b_id, vk = key[0], key[1], key[2]
        ca, cb = station_coord(a_id), station_coord(b_id)
        if ca is None or cb is None:
            continue
        map_stations[str(a_id)] = ca
        map_stations[str(b_id)] = cb
        map_veh_edges.append(
            [a_id, b_id, variant_id(key[3:]), vehicle_id(vk), count]
        )

    # Durchbindung: folgt auf eine Fahrt mit dubi=ende als nächster Check-in
    # (createdAt, Fahrplanzeiten können falsch sein) eine Fahrt mit dubi=start
    # ab derselben Station, gelten beide als eine durchgehende Fahrt.
    # Linien-Folge nur bei gleicher Linie, Baureihen-Folge bei gleicher
    # Baureihe, Fahrzeug-Folge für die auf beiden getaggten.
    for x_id, y_id in next_checkin_pairs(statuses):
        x, y = dubi_ends.get(x_id), dubi_ends.get(y_id)
        if not x or not y or not x[1] or not y[0]:
            continue
        last, first = x[3], y[2]
        if first[0] != last[1] or first[1] == last[0]:
            continue
        if x[4] and x[4] == y[4]:
            seq_lc[(last[0], last[1], first[1], *x[5])] += 1
        elif x[5][1] == y[5][1]:
            seq_loc[(last[0], last[1], first[1], *x[5])] += 1
        for vk in set(x[6]) & set(y[6]):
            seq_veh[(last[0], last[1], first[1], vk, *x[5])] += 1

    map_edge_seqs = [
        [key[0], key[1], key[2], variant_id(key[3:]), count]
        for key, count in seq_lc.items()
    ]
    map_loc_edge_seqs = [
        [key[0], key[1], key[2], variant_id(key[3:]), count]
        for key, count in seq_loc.items()
    ]
    map_veh_edge_seqs = [
        [key[0], key[1], key[2], variant_id(key[4:]), vehicle_id(key[3]), count]
        for key, count in seq_veh.items()
    ]

    map_disc_edges = []
    for key, count in discovered_lc.items():
        a_id, b_id = key[0], key[1]
        ca, cb = station_coord(a_id), station_coord(b_id)
        if ca is None or cb is None:
            continue
        map_stations[str(a_id)] = ca
        map_stations[str(b_id)] = cb
        map_disc_edges.append([a_id, b_id, variant_id(key[2:]), count])

    map_disc_veh_edges = []
    for key, count in discovered_veh.items():
        a_id, b_id, vk = key[0], key[1], key[2]
        ca, cb = station_coord(a_id), station_coord(b_id)
        if ca is None or cb is None:
            continue
        map_stations[str(a_id)] = ca
        map_stations[str(b_id)] = cb
        map_disc_veh_edges.append(
            [a_id, b_id, variant_id(key[3:]), vehicle_id(vk), count]
        )

    map_veh_nodes = []
    for key, cnt in node_veh.items():
        sid, vk = key[0], key[1]
        c = station_coord(sid)
        if c is None:
            continue
        map_stations[str(sid)] = c
        map_veh_nodes.append(
            [
                sid, variant_id(key[2:]), vehicle_id(vk), cnt,
                node_veh_used.get(key, 0),
                node_veh_held.get(key, 0),
                node_veh_pass.get(key, 0),
            ]
        )

    dates.sort()
    kpis = {
        "count": len(trips),
        "distanceKm": round(total_distance_m / 1000, 1),
        "durationMin": total_duration_min,
        "points": total_points,
        "stations": len(station_ids),
        "lines": len(lines),
        "first": dates[0].date().isoformat() if dates else None,
        "last": dates[-1].date().isoformat() if dates else None,
    }

    n_trips = len(trips) or 1
    # Volle Rankings/Kreuztabellen; die UI zeigt zunächst die initialen Top-N
    # und lädt den Rest per „Mehr laden“.
    by_line = _finalize_entities(line_aggs, "line")
    by_loc = _finalize_entities(loc_aggs, "locClass")
    by_veh = _finalize_entities(veh_aggs, "vehicle")
    cross_loc_line = _pack_cross(cross_stores["crossLocClassLine"])
    cross_veh_line = _pack_cross(cross_stores["crossVehicleLine"])
    months_sorted = sorted(
        {c for (_r, c) in cross_stores["crossLineMonth"]}
        | {c for (_r, c) in cross_stores["crossLocClassMonth"]}
        | {c for (_r, c) in cross_stores["crossVehicleMonth"]}
    )
    cross_line_month = _pack_cross(
        cross_stores["crossLineMonth"], col_order=months_sorted
    )
    cross_line_weekday = _pack_cross(
        cross_stores["crossLineWeekday"],
        col_order=list(WEEKDAY_KEYS),
    )
    cross_loc_month = _pack_cross(
        cross_stores["crossLocClassMonth"], col_order=months_sorted
    )
    cross_loc_weekday = _pack_cross(
        cross_stores["crossLocClassWeekday"], col_order=list(WEEKDAY_KEYS)
    )
    cross_loc_cat = _pack_cross(cross_stores["crossLocClassCategory"])
    cross_line_cat = _pack_cross(cross_stores["crossLineCategory"])
    cross_line_delay = _pack_cross(
        cross_stores["crossLineDelay"],
        col_order=list(DELAY_BUCKETS),
    )
    cross_loc_delay = _pack_cross(
        cross_stores["crossLocClassDelay"], col_order=list(DELAY_BUCKETS)
    )
    cross_veh_month = _pack_cross(
        cross_stores["crossVehicleMonth"],
        col_order=months_sorted,
    )

    top_line = by_line[0] if by_line else None
    top_veh = by_veh[0] if by_veh else None

    by_station = [a.to_row() for a in station_aggs.values()]
    by_station.sort(
        key=lambda r: (
            -r["total"], -r["boarded"], -r["alighted"], -r["through"],
            -r["passed"], r["key"],
        )
    )
    stations_boarded_n = sum(1 for a in station_aggs.values() if a.boarded)
    stations_alighted_n = sum(1 for a in station_aggs.values() if a.alighted)
    stations_through_n = sum(1 for a in station_aggs.values() if a.through)
    stations_passed_n = sum(1 for a in station_aggs.values() if a.passed)

    # --- Kanten- und Wiederholungs-Statistiken ---
    def edge_names_for(a_id, b_id):
        e = edge_aggs.get((a_id, b_id))
        if e:
            return e.from_name, e.to_name
        a = stations.get(str(a_id)) or stations.get(a_id) or {}
        b = stations.get(str(b_id)) or stations.get(b_id) or {}
        return (
            a.get("name") or edge_names.get(a_id) or str(a_id),
            b.get("name") or edge_names.get(b_id) or str(b_id),
        )

    all_edge_rows = [e.to_row() for e in edge_aggs.values()]
    # Eine Liste; stats.js (edgeLists) sortiert daraus die sechs Kanten-
    # Tabellen. Die Reihenfolge hier entscheidet dort bei Gleichstand.
    def combo_sort_key(r):
        return (-r["count"], r.get("last") or "", r.get("from") or "")

    veh_edge_gt1_all = []
    for (loc, num, a_id, b_id), c in veh_edge.items():
        if c.count <= 1:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        veh_edge_gt1_all.append({
            "vehicle": num,
            "locClass": loc,
            "from": fn,
            "to": tn,
            "count": c.count,
            "first": c.first,
            "last": c.last,
            "lines": len(c.lines),
        })
    veh_edge_gt1_all.sort(key=combo_sort_key)
    veh_edge_repeat_n = len(veh_edge_gt1_all)
    veh_edge_gt1 = veh_edge_gt1_all

    veh_edge_line_gt1 = []
    for (loc, num, a_id, b_id, line), c in veh_edge_line.items():
        if c.count <= 1:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        veh_edge_line_gt1.append({
            "vehicle": num,
            "locClass": loc,
            "from": fn,
            "to": tn,
            "line": line,
            "count": c.count,
            "first": c.first,
            "last": c.last,
        })
    veh_edge_line_gt1.sort(key=combo_sort_key)

    line_edge_gt1 = []
    for (line, a_id, b_id), c in line_edge.items():
        if c.count <= 1:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        line_edge_gt1.append({
            "line": line,
            "from": fn,
            "to": tn,
            "count": c.count,
            "first": c.first,
            "last": c.last,
        })
    line_edge_gt1.sort(key=combo_sort_key)

    loc_edge_gt1 = []
    for (loc, a_id, b_id), c in loc_edge.items():
        if c.count <= 1:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        loc_edge_gt1.append({
            "locClass": loc,
            "from": fn,
            "to": tn,
            "count": c.count,
            "first": c.first,
            "last": c.last,
        })
    loc_edge_gt1.sort(key=combo_sort_key)

    multi_vehicle_edges = sorted(
        [r for r in all_edge_rows if r["uniqueVehicles"] > 1],
        key=lambda r: (-r["uniqueVehicles"], -r["count"], r["from"]),
    )

    edges_repeat_n = sum(1 for r in all_edge_rows if r["count"] > 1)

    # Katalogseite „Linien“: aggregierte gerichtete Laufwege + Baureihen-Anteile.
    def station_label(sid):
        st = station_aggs.get(sid)
        if st and st.name:
            return st.name
        return edge_names.get(sid) or str(sid)

    def route_stop_flags(sid, lk):
        st = station_aggs.get(sid)
        flags = 0
        if st and (st.boarded or st.alighted):
            flags |= ROUTE_FLAG_USED
        role = line_station_role.get((sid, lk))
        if role:
            if role.boarded or role.alighted:
                flags |= ROUTE_FLAG_USED_LINE
            if role.passed and not (role.boarded or role.alighted or role.through):
                flags |= ROUTE_FLAG_PASS_ONLY
        return flags

    line_edge_ids = {}
    for (line, a_id, b_id), c in line_edge.items():
        line_edge_ids.setdefault(line, {})[(a_id, b_id)] = c.count
    line_seq_ids = {}
    for (line, a_id, b_id, c_id), n in line_seq.items():
        line_seq_ids.setdefault(line, {})[(a_id, b_id, c_id)] = n

    line_catalog = []
    for lk, agg in line_aggs.items():
        if not lk:
            continue
        _name, operator = split_line_key(lk)
        tagged = sum(agg.loc_class_counts.values())
        untagged = max(0, agg.count - tagged)
        # [Baureihe, Fahrten, km]; Reihenfolge nach Fahrten, damit Farbindex
        # und Legende für den Kilometer-Balken dieselben bleiben.
        loc_classes = [
            [k, n, round(agg.loc_class_km.get(k, 0.0), 1)]
            for k, n in agg.loc_class_counts.most_common()
        ]
        if untagged:
            untagged_km = max(0.0, agg.distance_km - sum(agg.loc_class_km.values()))
            loc_classes.append(["", untagged, round(untagged_km, 1)])
        packed_routes = []
        for route in cover_directed_paths(
            line_edge_ids.get(lk, {}),
            station_label,
            line_seq_ids.get(lk, {}),
        ):
            stops = [
                [sid, station_label(sid), route_stop_flags(sid, lk)]
                for sid in route["ids"]
            ]
            packed_routes.append({
                "loop": route["loop"],
                "stops": stops,
                "counts": route["counts"],
            })
        line_catalog.append({
            "key": lk,
            "operator": operator,
            "count": agg.count,
            "distanceKm": round(agg.distance_km, 1),
            "locClasses": loc_classes,
            "routes": packed_routes,
        })
    op_km = {}
    for row in line_catalog:
        op_km[row["operator"]] = op_km.get(row["operator"], 0) + row["distanceKm"]
    line_catalog.sort(
        key=lambda r: (
            -op_km[r["operator"]],
            r["operator"],
            split_line_key(r["key"])[0].casefold(),
            r["key"],
        )
    )

    stats = {
        "extra": {
            "lines": len(line_aggs),
            "locClasses": len(loc_aggs),
            "vehicles": len(veh_aggs),
            "tagLocPct": round(100.0 * trips_with_loc / n_trips, 1),
            "tagVehPct": round(100.0 * trips_with_veh / n_trips, 1),
            "uniqueLineVehicle": len(line_vehicle),
            "uniqueLineLocClass": len(line_loc),
            "uniqueVehicleLocClass": len(vehicle_loc),
            "uniqueRoutes": len(uniq_routes),
            "edges": len(edge_aggs),
            "edgesRepeat": edges_repeat_n,
            "vehEdgeRepeat": veh_edge_repeat_n,
            "stationsBoarded": stations_boarded_n,
            "stationsAlighted": stations_alighted_n,
            "stationsThrough": stations_through_n,
            "stationsPassed": stations_passed_n,
            "minTrip": min_trip,
            "maxTrip": max_trip,
            "topLine": (
                {"key": top_line["key"], "km": top_line["distanceKm"]}
                if top_line
                else None
            ),
            "topVehicle": (
                {
                    "key": top_veh["key"],
                    "locClass": top_veh.get("locClass") or "",
                    "km": top_veh["distanceKm"],
                }
                if top_veh
                else None
            ),
        },
        "byLine": by_line,
        "byLocClass": by_loc,
        "byVehicle": by_veh,
        "byStation": by_station,
        "crossLocClassLine": cross_loc_line,
        "crossVehicleLine": cross_veh_line,
        "crossLineMonth": cross_line_month,
        "crossLineWeekday": cross_line_weekday,
        "crossLocClassMonth": cross_loc_month,
        "crossLocClassWeekday": cross_loc_weekday,
        "crossLocClassCategory": cross_loc_cat,
        "crossLineCategory": cross_line_cat,
        "crossLineDelay": cross_line_delay,
        "crossLocClassDelay": cross_loc_delay,
        "crossVehicleMonth": cross_veh_month,
        "edgeRows": all_edge_rows,
        "vehEdgeGt1": veh_edge_gt1,
        "vehEdgeLineGt1": veh_edge_line_gt1,
        "lineEdgeGt1": line_edge_gt1,
        "locEdgeGt1": loc_edge_gt1,
        "multiVehicleEdges": multi_vehicle_edges,
    }

    # --- Tagesziele: Erstvorkommen und Wiederholungen je Kalendertag ---
    daily_firsts = {}
    daily_repeats = {}

    def _push_day(store, date_str, bucket, row):
        if not date_str:
            return
        day = store.setdefault(date_str, {})
        day.setdefault(bucket, []).append(row)

    def _emit_dated(dates, first, bucket, row):
        _push_day(daily_firsts, first, bucket, row)
        for d in dates or ():
            if d and d != first:
                _push_day(daily_repeats, d, bucket, row)

    for k, a in line_aggs.items():
        if k and a.first:
            _emit_dated(a.dates, a.first, "lines", {"key": k})
    for k, a in loc_aggs.items():
        if k and a.first:
            _emit_dated(a.dates, a.first, "locClasses", {"key": k})
    for (num, loc), c in vehicle_loc.items():
        if not num or not c.first:
            continue
        row = {"key": num}
        if loc:
            row["locClass"] = loc
        _emit_dated(c.dates, c.first, "vehicles", row)
    for e in edge_aggs.values():
        if e.first:
            _emit_dated(
                e.dates, e.first, "edges", {"from": e.from_name, "to": e.to_name}
            )
    for sid, a in station_aggs.items():
        name = a.name or str(sid)
        if a.first_used:
            _emit_dated(a.dates_used, a.first_used, "stationsUsed", {"key": name})
        if a.first_through:
            _emit_dated(
                a.dates_through, a.first_through, "stationsThrough", {"key": name}
            )
        if a.first_pass:
            _emit_dated(
                a.dates_pass, a.first_pass, "stationsPassed", {"key": name}
            )

    for (line, loc, veh), c in line_vehicle.items():
        if c.first:
            _emit_dated(
                c.dates, c.first, "lineVehicle",
                {"line": line, "vehicle": veh, "locClass": loc},
            )
    for (line, loc), c in line_loc.items():
        if c.first:
            _emit_dated(
                c.dates, c.first, "lineLocClass", {"line": line, "locClass": loc}
            )

    for (loc, num, a_id, b_id), c in veh_edge.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "vehEdge",
            {"vehicle": num, "locClass": loc, "from": fn, "to": tn},
        )
    for (line, a_id, b_id), c in line_edge.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "lineEdge",
            {"line": line, "from": fn, "to": tn},
        )
    for (loc, a_id, b_id), c in loc_edge.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "locEdge",
            {"locClass": loc, "from": fn, "to": tn},
        )

    for (sid, line), c in station_line.items():
        if not c.first:
            continue
        st = station_aggs.get(sid)
        name = (st.name if st else "") or edge_names.get(sid) or str(sid)
        _emit_dated(
            c.dates, c.first, "stationLine", {"station": name, "line": line}
        )

    for (loc, num, a_id, b_id, line), c in veh_edge_line.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "vehEdgeLine",
            {"vehicle": num, "locClass": loc, "from": fn, "to": tn, "line": line},
        )
    for (loc, a_id, b_id, line), c in loc_edge_line.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "locEdgeLine",
            {"locClass": loc, "from": fn, "to": tn, "line": line},
        )

    # Top-Wagen der Baureihe: nur Neu, wenn ein Wagen den bisherigen Leader
    # derselben Baureihe strikt überholt (km, dann Fahrten). Das erste Fahrzeug
    # einer Baureihe zählt nicht — das steht schon unter „Fahrzeuge“.
    class_stats = {}  # locClass -> {veh: [km, count]}
    last_leader = {}  # locClass -> letzter eindeutiger Leader
    for rec in sorted(
        vehicle_records,
        key=lambda r: (r.get("date") or "", r.get("depTime") or "", r.get("vehicleNumber") or ""),
    ):
        loc = rec.get("locClass") or ""
        veh = rec.get("vehicleNumber") or ""
        if not loc or not veh:
            continue
        veh_stats = class_stats.setdefault(loc, {})
        km, n = veh_stats.get(veh, (0.0, 0))
        veh_stats[veh] = (km + (rec.get("distanceKm") or 0), n + 1)
        ranked = sorted(
            veh_stats.items(),
            key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]),
        )
        new_leader = ranked[0][0]
        if len(ranked) > 1:
            top_km, top_n = ranked[0][1]
            next_km, next_n = ranked[1][1]
            if (top_km, top_n) == (next_km, next_n):
                new_leader = None
        prev = last_leader.get(loc)
        if new_leader and prev and new_leader != prev:
            top_km, top_n = veh_stats[new_leader]
            prev_km, prev_n = veh_stats[prev]
            _push_day(
                daily_firsts,
                rec.get("date") or "",
                "topVehicleInClass",
                {
                    "vehicle": new_leader,
                    "locClass": loc,
                    "km": round(top_km, 1),
                    "count": top_n,
                    "prevVehicle": prev,
                    "prevKm": round(prev_km, 1),
                    "prevCount": prev_n,
                },
            )
        if new_leader:
            last_leader[loc] = new_leader

    # Stabile Sortierung innerhalb der Buckets.
    def _sort_key(row):
        return tuple(str(row.get(k, "")) for k in (
            "key", "line", "vehicle", "locClass", "station", "from", "to",
            "prevVehicle",
        ))

    for day_buckets in list(daily_firsts.values()) + list(daily_repeats.values()):
        for bucket, rows in day_buckets.items():
            rows.sort(key=_sort_key)

    return {
        "kpis": kpis,
        "trips": trips,
        "vehicles": vehicle_records,
        "lines": line_catalog,
        "lineRides": line_rides,
        "lineStopNames": line_stop_names,
        "statEdges": stat_edges,
        "statStops": stat_stops,
        "statEdgeNames": {
            f"{a_id}{LINE_SEP}{b_id}": [e.from_name, e.to_name]
            for (a_id, b_id), e in edge_aggs.items()
        },
        "statStopNames": {
            str(sid): (a.name or str(sid))
            for sid, a in station_aggs.items()
        },
        "lineColors": line_colors,
        "stations": map_stations,
        "variants": variants,
        "edges": map_edges,
        "nodes": map_nodes,
        "mapVehicles": map_vehicles,
        "vehEdges": map_veh_edges,
        "edgeSeqs": map_edge_seqs,
        "vehEdgeSeqs": map_veh_edge_seqs,
        "locEdgeSeqs": map_loc_edge_seqs,
        "vehNodes": map_veh_nodes,
        "discoveredEdges": map_disc_edges,
        "discoveredVehEdges": map_disc_veh_edges,
        "segments": segment_ranking,
        "stats": stats,
        "dailyFirsts": pack_daily(daily_firsts),
        "dailyRepeats": pack_daily(daily_repeats),
        "locClassFamilies": pack_loc_class_families(
            loc_class_families or {}, variants
        ),
    }


_DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")

# Reihenfolge = heutiger Ein-Script-Scope. Nicht als getrennte <script>-Tags
# laden: tabs.js nutzt lexikalisch map/mapFitted/mapBounds aus map.js.
_JS_FILES = (
    "js/helpers.js",
    "js/home.js",
    "js/theme.js",
    "js/tabs.js",
    "js/overview.js",
    "js/stats.js",
    "js/map.js",
    "js/trips.js",
    "js/vehicles.js",
    "js/wanted.js",
    "js/lines.js",
    "js/daily.js",
)


class TemplateError(Exception):
    """Dashboard-Asset unter dashboard/ fehlt oder ist nicht lesbar."""


def _read_asset(rel):
    path = os.path.join(_DASHBOARD_DIR, rel)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise TemplateError(f"Dashboard-Asset fehlt: {path}")
    except OSError as e:
        raise TemplateError(f"Dashboard-Asset nicht lesbar: {path} ({e})")


def render_html(data):
    """Baut das selbstständige Dashboard-HTML aus dashboard/-Assets + Daten.

    CSS und JS werden inline eingesetzt (ein <style>, ein <script>), damit die
    Ausgabe eine einzelne Datei bleibt. `builtAt` (ISO-Lokalzeit des Builds)
    wird in die Daten geschrieben. __DATA__ wird zuletzt ersetzt, damit
    Asset-Inhalte den JSON-Blob nicht zerlegen können.
    """
    template = _read_asset("template.html")
    if "__CSS__" not in template or "__JS__" not in template or "__DATA__" not in template:
        raise TemplateError(
            "dashboard/template.html: Platzhalter __CSS__, __JS__ oder __DATA__ fehlen."
        )
    css = _read_asset("style.css")
    js = "".join(_read_asset(rel) for rel in _JS_FILES)
    html = template.replace("__CSS__", css).replace("__JS__", js)
    payload = dict(data)
    payload["builtAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return html.replace("__DATA__", json_for_script(payload))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"walita {__version__}",
        help="Version ausgeben und beenden.",
    )
    parser.add_argument("--statuses", default="data/statuses.json", help="Eingabe Statuses.")
    parser.add_argument("--stations", default="data/stations.json", help="Eingabe Stations-Koordinaten.")
    parser.add_argument("--output", "-o", default="data/dashboard.html", help="Ziel-HTML-Datei.")
    parser.add_argument("--open", action="store_true", help="Dashboard danach im Browser öffnen.")
    parser.add_argument(
        "--ignore-plus", action="store_true",
        help="Wagennummern-Tags nicht am '+' trennen (Doppeltraktion bleibt ein Fahrzeug).",
    )
    parser.add_argument(
        "--loc-class-families", default="data/loc_class_families.txt",
        help="Baureihe→Familie für den Kartenfilter "
             "(Default: data/loc_class_families.txt; JSON-Objekt, "
             "gleiche Baureihe darf mehrfach vorkommen).",
    )
    parser.add_argument(
        "--edge-patches", default="data/edge_patches.json",
        help="Lokale Via-Patches für grobe Kanten "
             "(Default: data/edge_patches.json; fehlende Datei = keine Expansion).",
    )
    parser.add_argument(
        "--station-patches", default="data/station_patches.json",
        help="Lokale Stations-Patches (Koordinaten/Merges) "
             "(Default: data/station_patches.json; fehlende Datei = keine Änderung).",
    )
    parser.add_argument(
        "--line-color-patches", default="data/line_color_patches.json",
        help="Lokale Linienfarben-Patches je Status "
             "(Default: data/line_color_patches.json; fehlende Datei = keine Änderung).",
    )
    parser.add_argument(
        "--home-region", default="data/home_region.json",
        help="Lokale Operator-Liste der Heimatregion "
             "(Default: data/home_region.json; fehlende oder leere Datei = kein Filter).",
    )
    parser.add_argument(
        "--boarding-patches", default="data/boarding_patches.json",
        help="Lokale Einstiegs-Patches je Status "
             "(Default: data/boarding_patches.json; fehlende Datei = keine Änderung).",
    )
    parser.add_argument(
        "--vehicle-roster", default="data/vehicle_roster.json",
        help="Lokaler Fuhrpark je Baureihe "
             "(Default: data/vehicle_roster.json; fehlende Datei = keine Nummernliste).",
    )
    parser.add_argument(
        "--line-patches", default="data/line_patches.json",
        help="Liniennamen je Fahrt und zusammengeführte Linien "
             "(Default: data/line_patches.json; fehlende Datei = nur walita:line-Tags).",
    )
    parser.add_argument(
        "--operator-line-patches", default="data/operator_line_patches.json",
        help="Operator einer Linie überschreiben "
             "(Default: data/operator_line_patches.json; fehlende Datei = keine Änderung).",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.statuses, encoding="utf-8") as f:
            statuses = json.load(f)
    except FileNotFoundError:
        log(f"Fehler: {args.statuses} nicht gefunden. Erst download_statuses.py ausführen.")
        return 2
    except json.JSONDecodeError as e:
        # Typischer Fall: der Export wurde mitten im Schreiben abgebrochen.
        log(f"Fehler: {args.statuses} ist kein gültiges JSON ({e}). "
            f"Datei wahrscheinlich unvollständig – Export wiederholen.")
        return 2
    except OSError as e:
        log(f"Fehler: {args.statuses} nicht lesbar ({e}).")
        return 2

    try:
        with open(args.stations, encoding="utf-8") as f:
            stations = json.load(f)
    except FileNotFoundError:
        log(f"Hinweis: {args.stations} nicht gefunden – Karte bleibt leer (keine Koordinaten).")
        stations = {}
    except (json.JSONDecodeError, OSError) as e:
        log(f"Warnung: {args.stations} nicht lesbar ({e}) – Karte bleibt leer.")
        stations = {}

    try:
        loc_class_families = load_loc_class_families(args.loc_class_families)
    except ValueError as e:
        log(f"Fehler: {e}")
        return 2
    if loc_class_families:
        n = sum(len(v) if isinstance(v, list) else 1
                for v in loc_class_families.values())
        log(f"Baureihenfamilien: {n} Zuordnungen "
            f"aus {args.loc_class_families}.")
    elif os.path.isfile(args.loc_class_families):
        log(f"Baureihenfamilien: {args.loc_class_families} enthält keine Regeln.")

    edge_patches = load_patches(args.edge_patches)
    n_patch = (
        len(edge_patches.get("defaults") or {})
        + len(edge_patches.get("overrides") or {})
    )
    if n_patch:
        log(f"Kanten-Patches: {n_patch} Einträge aus {args.edge_patches}.")
    elif os.path.isfile(args.edge_patches):
        log(f"Kanten-Patches: {args.edge_patches} ist leer.")

    station_patches = load_station_patches(args.station_patches)
    n_move = len(station_patches.get("moves") or {})
    n_merge = len(station_patches.get("merges") or {})
    if n_move or n_merge:
        log(f"Stations-Patches: {n_move} Verschiebungen, {n_merge} Merges "
            f"aus {args.station_patches}.")
    elif os.path.isfile(args.station_patches):
        log(f"Stations-Patches: {args.station_patches} ist leer.")

    line_color_patches = load_line_color_patches(args.line_color_patches)
    n_color = len(line_color_patches.get("overrides") or {})
    if n_color:
        log(f"Linienfarben-Patches: {n_color} Einträge aus {args.line_color_patches}.")
    elif os.path.isfile(args.line_color_patches):
        log(f"Linienfarben-Patches: {args.line_color_patches} ist leer.")

    home_ops = load_operators(args.home_region)
    if home_ops:
        log(f"Heimatregion: {len(home_ops)} Operatoren aus {args.home_region}.")
    elif os.path.isfile(args.home_region):
        log(f"Heimatregion: {args.home_region} enthält keine Operatoren.")

    boarding_patches = load_boarding_patches(args.boarding_patches)
    n_board = len(boarding_patches.get("overrides") or {})
    if n_board:
        log(f"Einstiegs-Patches: {n_board} Einträge aus {args.boarding_patches}.")
    elif os.path.isfile(args.boarding_patches):
        log(f"Einstiegs-Patches: {args.boarding_patches} ist leer.")
    vehicle_roster = load_roster(args.vehicle_roster)
    n_roster_types = len(vehicle_roster.get("types") or {})
    n_roster = sum(len(v) for v in (vehicle_roster.get("types") or {}).values())
    if n_roster:
        log(f"Fuhrpark: {n_roster} Nummern in {n_roster_types} Baureihen "
            f"aus {args.vehicle_roster}.")
    elif os.path.isfile(args.vehicle_roster):
        log(f"Fuhrpark: {args.vehicle_roster} enthält keine Nummern.")

    try:
        line_patches = load_line_patches(args.line_patches)
    except ValueError as e:
        log(f"Fehler: {e}")
        return 2
    statuses, n_line = apply_line_patches(line_patches, statuses)
    if n_line:
        log(f"Linien-Patches: {n_line} Fahrten geändert "
            f"({len(line_patches['overrides'])} je Fahrt, "
            f"{len(line_patches['merges'])} Merges, Rest aus "
            f"walita:line-/walita:operator-Tags).")

    try:
        operator_line_rules = load_operator_line_rules(args.operator_line_patches)
    except ValueError as e:
        log(f"Fehler: {e}")
        return 2
    statuses, n_op_line = apply_operator_line_patches(operator_line_rules, statuses)
    if operator_line_rules:
        n_rules = len(operator_line_rules)
        log(f"Operator-Linien-Patches: {n_op_line} Fahrten, "
            f"{n_rules} {'Regel' if n_rules == 1 else 'Regeln'} "
            f"aus {args.operator_line_patches}.")
    elif os.path.isfile(args.operator_line_patches):
        log(f"Operator-Linien-Patches: {args.operator_line_patches} "
            f"enthält keine Regeln.")

    # Stations-Patches einmal für alle Läufe; build_data bekommt die
    # gepatchten Kopien und keine Stations-Patches mehr.
    if station_patches.get("moves") or station_patches.get("merges"):
        stations, statuses, edge_patches = apply_station_patches(
            station_patches, stations, statuses, edge_patches
        )
    edge_measures = learn_edge_measures(statuses) if n_board else None

    data = build_data(
        statuses, stations,
        ignore_plus=args.ignore_plus,
        loc_class_families=loc_class_families,
        edge_patches=edge_patches,
        line_color_patches=line_color_patches,
        boarding_patches=boarding_patches,
        edge_measures=edge_measures,
    )
    if home_ops:
        subset = filter_statuses(statuses, home_ops)
        data["homeOperators"] = home_ops
        data["home"] = build_data(
            subset, stations,
            ignore_plus=args.ignore_plus,
            loc_class_families=loc_class_families,
            edge_patches=edge_patches,
            line_color_patches=line_color_patches,
            boarding_patches=boarding_patches,
            edge_measures=edge_measures,
        )
        log(f"Heimatregion: {data['home']['kpis']['count']} von "
            f"{data['kpis']['count']} Fahrten.")
    if n_roster:
        data["vehicleRoster"] = vehicle_roster
    line_labels = resolved_line_labels(line_patches, operator_line_rules)
    if line_labels:
        data["lineLabels"] = {
            line_key(line, op): label
            for (line, op), label in sorted(line_labels.items())
        }
        log(f"Kartennamen: {len(line_labels)} Linien aus {args.line_patches}.")
    log(f"Ausgewertet: {data['kpis']['count']} Fahrten, {len(data['edges'])} Karten-Kanten, "
        f"{len(data['segments'])} Segmente.")

    try:
        html = render_html(data)
    except TemplateError as e:
        log(f"Fehler: {e}")
        return 2

    try:
        parent = os.path.dirname(args.output)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as e:
        log(f"Fehler: {args.output} nicht schreibbar ({e}).")
        return 1
    log(f"Geschrieben: {args.output}")

    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(args.output))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Abgebrochen.")
        sys.exit(130)
