#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokale Stations-Patches: Koordinaten verschieben, Stationen mergen und aufteilen.

Kein API-Write. Die Datei (Default `data/station_patches.json`) liegt vor den
Kanten-Patches: Moves überschreiben Lat/Lon, Merges schreiben IDs auf den
Survivor um, Splits schreiben einen Halt auf eine andere Station um, wenn sein
Nachbar im Laufweg passt (Träwelling fasst getrennte Stationen zusammen).
Quelle `stations.json` / `statuses.json` bleibt unangetastet.
"""

import json
import math
import os
import socketserver
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from edge_patches import (
    add_seen,
    consecutive_pairs,
    seen_dates,
    lookup_station,
    search_stations,
    station_id,
    station_map_row,
    station_name,
)


def empty_patches():
    return {"moves": {}, "merges": {}, "splits": {}}


def _parse_coord_pair(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if lat < -90.0 or lat > 90.0 or lon < -180.0 or lon > 180.0:
        return None
    return lat, lon


def load_patches(path):
    """Liest station_patches.json. Fehlende/ungültige Datei → leere Struktur.

    moves: id -> (lat, lon)
    merges: from_id -> to_id
    splits: (station_id, neighbor_id) -> to_id
    """
    if not path or not os.path.isfile(path):
        return empty_patches()
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty_patches()
    if not isinstance(data, dict):
        return empty_patches()
    moves = {}
    for row in data.get("moves") or []:
        if not isinstance(row, dict):
            continue
        sid = station_id(row.get("id"))
        coords = _parse_coord_pair(row.get("latitude"), row.get("longitude"))
        if sid is None or coords is None:
            continue
        moves[sid] = coords
    merges = {}
    for row in data.get("merges") or []:
        if not isinstance(row, dict):
            continue
        src, dest = station_id(row.get("from")), station_id(row.get("to"))
        if src is None or dest is None or src == dest:
            continue
        merges[src] = dest
    splits = {}
    for row in data.get("splits") or []:
        if not isinstance(row, dict):
            continue
        key = _split_key(row.get("station"), row.get("neighbor"), row.get("to"))
        if key is not None:
            splits[key[:2]] = key[2]
    return {"moves": moves, "merges": merges, "splits": splits}


def _split_key(station, neighbor, to):
    """(Station, Nachbar, Ersatz) normalisiert oder None, wenn nicht drei verschiedene."""
    ids = (station_id(station), station_id(neighbor), station_id(to))
    if None in ids or len(set(ids)) < 3:
        return None
    return ids


def dumps_patches(patches):
    """Serialisiert die interne Map-Form zurück ins JSON-Objekt."""
    moves = []
    for sid, coords in sorted((patches.get("moves") or {}).items(), key=lambda kv: kv[0]):
        lat, lon = coords
        moves.append({"id": sid, "latitude": lat, "longitude": lon})
    merges = []
    for src, dest in sorted(
        (patches.get("merges") or {}).items(), key=lambda kv: (kv[0], kv[1])
    ):
        merges.append({"from": src, "to": dest})
    splits = []
    for (sid, nb), dest in sorted(
        (patches.get("splits") or {}).items(), key=lambda kv: (str(kv[0]), str(kv[1]))
    ):
        splits.append({"station": sid, "neighbor": nb, "to": dest})
    return {"moves": moves, "merges": merges, "splits": splits}


def save_patches(path, patches):
    """Schreibt Patches atomar. Gibt True bei Erfolg."""
    if not path:
        return False
    parent = os.path.dirname(path)
    payload = dumps_patches(patches)
    tmp = path + ".tmp"
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


def alias_map(patches):
    """from_id → kanonische Survivor-ID. Ketten flach, Zyklen verworfen."""
    raw = {}
    for src, dest in (patches.get("merges") or {}).items():
        a, b = station_id(src), station_id(dest)
        if a is None or b is None or a == b:
            continue
        raw[a] = b

    def dest_or_cycle(sid):
        seen = []
        cur = sid
        while cur in raw:
            if cur in seen:
                return None
            seen.append(cur)
            cur = raw[cur]
        return cur

    out = {}
    for src in raw:
        dest = dest_or_cycle(src)
        if dest is None or dest == src:
            continue
        out[src] = dest
    return out


def canonical_id(sid, aliases):
    sid = station_id(sid)
    if sid is None:
        return sid
    return (aliases or {}).get(sid, sid)


def apply_to_stations(patches, stations):
    """Kopie von stations.json: Moves auf Survivor, Lookup gemergter IDs folgt Alias."""
    patches = patches or empty_patches()
    aliases = alias_map(patches)
    moves = patches.get("moves") or {}
    merged_away = set(aliases)
    out = {}
    for key, st in (stations or {}).items():
        if not isinstance(st, dict):
            continue
        sid = station_id(st.get("id") if st.get("id") is not None else key)
        if sid is None or sid in merged_away:
            continue
        if str(sid) in out:
            continue
        entry = dict(st)
        entry["id"] = sid
        mv = moves.get(sid)
        if mv:
            entry["latitude"] = mv[0]
            entry["longitude"] = mv[1]
        out[str(sid)] = entry
    for src, dest in aliases.items():
        survivor = out.get(str(dest))
        if not survivor:
            continue
        alias_entry = dict(survivor)
        alias_entry["id"] = src
        out[str(src)] = alias_entry
    return out


def _stop_hits(obj, aliases):
    """True, wenn der Halt oder seine verschachtelte Station gemergt ist."""
    if not isinstance(obj, dict):
        return False
    if station_id(obj.get("id")) in aliases:
        return True
    return _stop_hits(obj.get("station"), aliases)


def _remap_stop(obj, aliases, stations):
    """Kopie des Halts mit Survivor-ID (+ Name/Koordinaten), sonst obj selbst."""
    if not _stop_hits(obj, aliases):
        return obj
    out = dict(obj)
    sid = station_id(out.get("id"))
    if sid in aliases:
        _set_station(out, aliases[sid], stations)
    nested = out.get("station")
    if isinstance(nested, dict):
        out["station"] = _remap_stop(nested, aliases, stations)
    return out


def _set_station(out, dest, stations):
    """Setzt ID, Name und Koordinaten von `dest` in den Halt-Dict `out`."""
    out["id"] = dest
    st = lookup_station(stations, dest) or {}
    if st.get("name"):
        out["name"] = st["name"]
    lat, lon = st.get("latitude"), st.get("longitude")
    if lat is not None and lon is not None:
        out["latitude"] = lat
        out["longitude"] = lon


def _replace_stop(obj, dest, stations):
    """Kopie des Halts (und seiner verschachtelten Station) auf Station `dest`."""
    out = dict(obj)
    _set_station(out, dest, stations)
    nested = out.get("station")
    if isinstance(nested, dict):
        nested = dict(nested)
        _set_station(nested, dest, stations)
        out["station"] = nested
    return out


def apply_to_statuses(statuses, aliases, stations):
    """Origin/Dest/Stopover-IDs (+ Name) auf Survivor. Mutiert nicht.

    Nur betroffene Statuses und darin nur die geänderten Teile werden
    kopiert; der Rest wird geteilt.
    """
    if not statuses or not aliases:
        return statuses
    out = []
    for status in statuses:
        if not isinstance(status, dict):
            out.append(status)
            continue
        item = status
        checkin = status.get("checkin")
        if isinstance(checkin, dict):
            origin = _remap_stop(checkin.get("origin"), aliases, stations)
            dest = _remap_stop(checkin.get("destination"), aliases, stations)
            if origin is not checkin.get("origin") or dest is not checkin.get("destination"):
                checkin = dict(checkin)
                if "origin" in checkin:
                    checkin["origin"] = origin
                if "destination" in checkin:
                    checkin["destination"] = dest
                item = dict(item)
                item["checkin"] = checkin
        trip = status.get("trip")
        if isinstance(trip, dict):
            stops = trip.get("stopovers") or []
            mapped = [_remap_stop(stop, aliases, stations) for stop in stops]
            if any(a is not b for a, b in zip(mapped, stops)):
                trip = dict(trip)
                trip["stopovers"] = mapped
                if item is status:
                    item = dict(item)
                item["trip"] = trip
        out.append(item)
    return out


def _map_via(via, a2, b2, aliases):
    out = []
    blocked = {a2, b2}
    for vid in via or ():
        v2 = canonical_id(vid, aliases)
        if v2 is None or v2 in blocked or v2 in out:
            continue
        out.append(v2)
        blocked.add(v2)
    return out


def apply_to_edge_patches(edge_patches, aliases):
    """In-Memory-Umschlüsselung. Datei bleibt bei Original-IDs (Unmerge)."""
    if not edge_patches or not aliases:
        return edge_patches
    return _rewrite_edge_patches(
        edge_patches,
        lambda a, b: (canonical_id(a, aliases), canonical_id(b, aliases)),
        aliases,
    )


def _rewrite_edge_patches(edge_patches, pair_fn, aliases):
    """Schlüssel (a, b) über `pair_fn`; unveränderte Schlüssel gewinnen."""

    def rewrite_map(src, key_len):
        originals = []
        remapped = []
        for key, via in (src or {}).items():
            if key_len == 2:
                a, b = key
                extra = ()
            else:
                extra, a, b = key[0], key[1], key[2]
            a2, b2 = pair_fn(a, b)
            if a2 is None or b2 is None or a2 == b2:
                continue
            new_key = (a2, b2) if key_len == 2 else (extra, a2, b2)
            via2 = _map_via(via, a2, b2, aliases)
            unchanged = a2 == a and b2 == b
            row = (new_key, via2)
            if unchanged:
                originals.append(row)
            else:
                remapped.append(row)
        out = {}
        for key, via in originals:
            out[key] = via
        for key, via in remapped:
            if key not in out:
                out[key] = via
        return out

    return {
        "defaults": rewrite_map(edge_patches.get("defaults"), 2),
        "overrides": rewrite_map(edge_patches.get("overrides"), 3),
    }


def split_rules(patches, aliases=None):
    """(Station, Nachbar) → Ersatz, alle IDs nach Merges kanonisch.

    Regeln, die nach dem Merge nicht mehr drei verschiedene Stationen nennen,
    fallen weg; kollidieren zwei, gewinnt die erste in Dateireihenfolge.
    """
    if aliases is None:
        aliases = alias_map(patches or {})
    out = {}
    for (sid, nb), dest in ((patches or {}).get("splits") or {}).items():
        key = _split_key(canonical_id(sid, aliases), canonical_id(nb, aliases),
                         canonical_id(dest, aliases))
        if key is not None and key[:2] not in out:
            out[key[:2]] = key[2]
    return out


def split_targets(stops, rules):
    """Index → Ersatz-ID für Halte, deren Nachbar im Laufweg eine Regel trifft.

    Nachbarn sind die nächsten nicht entfallenen Halte davor und danach (wie
    die Kantenbildung). Trifft der Vorgänger, gilt seine Regel.
    """
    stops = list(stops or [])
    ids = [station_id(s.get("id")) if isinstance(s, dict) else None for s in stops]
    live = [i for i, s in enumerate(stops)
            if isinstance(s, dict) and not s.get("cancelled") and ids[i] is not None]
    out = {}
    if not rules:
        return out
    stations_with_rules = {sid for sid, _nb in rules}
    for i, sid in enumerate(ids):
        if sid not in stations_with_rules:
            continue
        prev = next((ids[j] for j in reversed(live) if j < i and ids[j] != sid), None)
        nxt = next((ids[j] for j in live if j > i and ids[j] != sid), None)
        dest = rules.get((sid, prev)) or rules.get((sid, nxt))
        if dest is not None:
            out[i] = dest
    return out


def _boundary_indices(checkin, stops):
    """Indizes von Start und Ziel in `stops` (Logik wie traveled_stopovers)."""
    def by_stopover(stop):
        so = (stop or {}).get("stopoverId")
        if so is None:
            return None
        return next((i for i, s in enumerate(stops) if s.get("stopoverId") == so), None)

    def by_station(stop, after=-1):
        sid = station_id((stop or {}).get("id"))
        if sid is None:
            return None
        return next((i for i, s in enumerate(stops)
                     if i > after and station_id(s.get("id")) == sid), None)

    origin, dest = checkin.get("origin"), checkin.get("destination")
    i_start = by_stopover(origin)
    if i_start is None:
        i_start = by_station(origin)
    i_end = by_stopover(dest)
    if i_end is None:
        i_end = by_station(dest, -1 if i_start is None else i_start)
    return i_start, i_end


def apply_splits_to_statuses(statuses, rules, stations):
    """Halte mit passendem Nachbarn auf die Ersatz-Station. Mutiert nicht.

    Umgeschrieben wird der ganze Halt, also beide angrenzenden Kanten. Start
    und Ziel des Check-ins folgen ihrem Halt im Trip; ohne Trip ist das
    jeweils andere Ende der Nachbar.
    """
    if not statuses or not rules:
        return statuses
    out = []
    for status in statuses:
        if not isinstance(status, dict):
            out.append(status)
            continue
        checkin = status.get("checkin")
        checkin = checkin if isinstance(checkin, dict) else {}
        trip = status.get("trip")
        stops = (trip.get("stopovers") or []) if isinstance(trip, dict) else []
        ends = {}
        item = status
        if stops:
            targets = split_targets(stops, rules)
            if targets:
                trip = dict(trip)
                trip["stopovers"] = [
                    _replace_stop(s, targets[i], stations) if i in targets else s
                    for i, s in enumerate(stops)
                ]
                item = dict(status)
                item["trip"] = trip
                i_start, i_end = _boundary_indices(checkin, stops)
                if i_start in targets:
                    ends["origin"] = targets[i_start]
                if i_end in targets:
                    ends["destination"] = targets[i_end]
        else:
            o = station_id((checkin.get("origin") or {}).get("id"))
            d = station_id((checkin.get("destination") or {}).get("id"))
            if (o, d) in rules:
                ends["origin"] = rules[(o, d)]
            if (d, o) in rules:
                ends["destination"] = rules[(d, o)]
        if ends:
            checkin = dict(checkin)
            for role, dest in ends.items():
                if isinstance(checkin.get(role), dict):
                    checkin[role] = _replace_stop(checkin[role], dest, stations)
            if item is status:
                item = dict(status)
            item["checkin"] = checkin
        out.append(item)
    return out


def apply_splits_to_edge_patches(edge_patches, rules):
    """Kanten-Patches auf W–N wandern nach R–N (beide Richtungen), nur im Speicher."""
    if not edge_patches or not rules:
        return edge_patches
    return _rewrite_edge_patches(
        edge_patches,
        lambda a, b: (rules.get((a, b), a), rules.get((b, a), b)),
        {},
    )


def apply_station_patches(patches, stations, statuses=None, edge_patches=None):
    """Kopien: Moves/Merges, dann Splits anwenden. Mutiert die Eingaben nicht."""
    patches = patches or empty_patches()
    stations_out = apply_to_stations(patches, stations)
    aliases = alias_map(patches)
    rules = split_rules(patches, aliases)
    statuses_out = statuses
    if statuses is not None and aliases:
        statuses_out = apply_to_statuses(statuses, aliases, stations_out)
    if statuses_out is not None and rules:
        statuses_out = apply_splits_to_statuses(statuses_out, rules, stations_out)
    edge_out = edge_patches
    if edge_patches is not None and aliases:
        edge_out = apply_to_edge_patches(edge_patches, aliases)
    if edge_out is not None and rules:
        edge_out = apply_splits_to_edge_patches(edge_out, rules)
    return stations_out, statuses_out, edge_out


def _collect_stop_ids(obj, out):
    if not isinstance(obj, dict):
        return
    sid = station_id(obj.get("id"))
    if sid is not None:
        out.add(sid)
    nested = obj.get("station")
    if isinstance(nested, dict):
        sid = station_id(nested.get("id"))
        if sid is not None:
            out.add(sid)


def collect_used_ids(statuses, edge_patches=None, station_patches=None):
    """IDs aus Fahrten, Kanten-Patches und Station-Patches."""
    ids = set()
    for status in statuses or []:
        if not isinstance(status, dict):
            continue
        checkin = status.get("checkin") or {}
        _collect_stop_ids(checkin.get("origin"), ids)
        _collect_stop_ids(checkin.get("destination"), ids)
        trip = status.get("trip") or {}
        if isinstance(trip, dict):
            for stop in trip.get("stopovers") or []:
                _collect_stop_ids(stop, ids)
    ep = edge_patches or {}
    for (a, b), via in (ep.get("defaults") or {}).items():
        ids.add(a)
        ids.add(b)
        ids.update(via or ())
    for (_sid, a, b), via in (ep.get("overrides") or {}).items():
        ids.add(a)
        ids.add(b)
        ids.update(via or ())
    sp = station_patches or empty_patches()
    ids.update(sp.get("moves") or {})
    for src, dest in (sp.get("merges") or {}).items():
        ids.add(src)
        ids.add(dest)
    for (sid, nb), dest in (sp.get("splits") or {}).items():
        ids.update((sid, nb, dest))
    ids.discard(None)
    return ids


def ridden_ids(statuses, edge_patches=None):
    """IDs, an denen ein Check-in vorbeikam: Halte im Abschnitt Start..Ziel
    (Logik wie traveled_stopovers, ohne eindeutige Grenzen der ganze Trip)
    und Via-Stationen der Kanten-Patches."""
    ids = set()
    for status in statuses or []:
        if not isinstance(status, dict):
            continue
        checkin = status.get("checkin") or {}
        _collect_stop_ids(checkin.get("origin"), ids)
        _collect_stop_ids(checkin.get("destination"), ids)
        trip = status.get("trip") or {}
        stops = (trip.get("stopovers") or []) if isinstance(trip, dict) else []
        i_start, i_end = _boundary_indices(checkin, stops)
        if i_start is not None and i_end is not None and i_start <= i_end:
            stops = stops[i_start:i_end + 1]
        for stop in stops:
            _collect_stop_ids(stop, ids)
    ep = edge_patches or {}
    for via in list((ep.get("defaults") or {}).values()) + list((ep.get("overrides") or {}).values()):
        ids.update(via or ())
    ids.discard(None)
    return ids


def set_move(patches, sid, lat, lon):
    coords = _parse_coord_pair(lat, lon)
    sid = station_id(sid)
    if sid is None or coords is None:
        return False
    aliases = alias_map(patches)
    if sid in aliases:
        return False
    patches.setdefault("moves", {})[sid] = coords
    return True


def clear_move(patches, sid):
    (patches.get("moves") or {}).pop(station_id(sid), None)


def set_merge(patches, from_id, to_id):
    """from wird zu to. False wenn ungültig oder Zyklus."""
    src, dest = station_id(from_id), station_id(to_id)
    if src is None or dest is None or src == dest:
        return False
    merges = dict(patches.get("merges") or {})
    prev = merges.get(src)
    merges[src] = dest
    trial = {"merges": merges, "moves": patches.get("moves") or {}}
    aliases = alias_map(trial)
    if aliases.get(src) is None:
        return False
    patches["merges"] = merges
    if prev == dest:
        return True
    (patches.get("moves") or {}).pop(src, None)
    return True


def clear_merge(patches, from_id):
    (patches.get("merges") or {}).pop(station_id(from_id), None)


def set_split(patches, station, neighbor, to):
    """Halt `station` neben `neighbor` wird zu `to`. False wenn ungültig."""
    key = _split_key(station, neighbor, to)
    if key is None:
        return False
    patches.setdefault("splits", {})[key[:2]] = key[2]
    return True


def clear_split(patches, station, neighbor):
    (patches.get("splits") or {}).pop((station_id(station), station_id(neighbor)), None)


def neighbor_counts(statuses, sid):
    """Nachbar-ID → Anzahl Kanten (beide Richtungen) an `sid` in allen Trips."""
    sid = station_id(sid)
    counts = {}
    for status in statuses or []:
        if not isinstance(status, dict):
            continue
        stops = (status.get("trip") or {}).get("stopovers") or []
        if not stops:
            checkin = status.get("checkin") or {}
            stops = [s for s in (checkin.get("origin"), checkin.get("destination"))
                     if isinstance(s, dict)]
        for a, b in consecutive_pairs(stops):
            a_id, b_id = station_id(a.get("id")), station_id(b.get("id"))
            if a_id == sid:
                counts[b_id] = counts.get(b_id, 0) + 1
            elif b_id == sid:
                counts[a_id] = counts.get(a_id, 0) + 1
    return counts


PATCH_MAP_PORT = 8712
_PATCH_HTML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dashboard", "station_patch.html"
)


class StationMapService:
    """Einmaliger localhost-HTTP-Server für die Stations-Karte (Port 8712)."""

    def __init__(self, patches_path, html_path=None, on_saved=None):
        self.patches_path = patches_path
        self.html_path = html_path or _PATCH_HTML
        self.on_saved = on_saved
        self.stations = {}
        self.statuses = []
        self.edge_patches = {"defaults": {}, "overrides": {}}
        self.patches = empty_patches()
        self.seen_days = {}
        self.ridden = set()
        self._httpd = None
        self._thread = None
        self._lock = threading.Lock()

    def url(self):
        return f"http://127.0.0.1:{PATCH_MAP_PORT}/"

    def start(self):
        if self._httpd is not None:
            return True
        handler = _make_handler(self)
        try:
            httpd = _ThreadingServer(("127.0.0.1", PATCH_MAP_PORT), handler)
        except OSError:
            return False
        self._httpd = httpd
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self._thread = t
        return True

    def set_data(self, *, stations, statuses, edge_patches, patches):
        with self._lock:
            self.stations = stations or {}
            self.statuses = list(statuses or [])
            self.seen_days = seen_dates(self.statuses)
            self.edge_patches = edge_patches or {"defaults": {}, "overrides": {}}
            self.ridden = ridden_ids(self.statuses, self.edge_patches)
            self.patches = patches or empty_patches()

    def set_patches(self, patches):
        """Stand nach einer Änderung im Editor, damit Saves ihn nicht verlieren."""
        with self._lock:
            self.patches = patches or empty_patches()

    def _snapshot(self):
        return (
            self.stations,
            self.statuses,
            self.edge_patches,
            self.patches,
            self.patches_path,
        )

    def context_payload(self, extra_ids=None):
        with self._lock:
            stations, statuses, edge_patches, patches, _path = self._snapshot()
            seen_days = self.seen_days
            ridden_raw = self.ridden
        aliases = alias_map(patches)
        ridden = {canonical_id(sid, aliases) for sid in ridden_raw}
        applied = apply_to_stations(patches, stations)
        used = collect_used_ids(statuses, edge_patches, patches)
        for sid in extra_ids or ():
            used.add(station_id(sid))
        used.discard(None)
        seen = set()
        mapped = []
        for sid in used:
            canon = canonical_id(sid, aliases)
            if canon is None or canon in seen or canon in aliases:
                continue
            seen.add(canon)
            row = station_map_row(applied, canon)
            if not row:
                continue
            row["moved"] = canon in (patches.get("moves") or {})
            row["ridden"] = canon in ridden
            mapped.append(row)
        add_seen(mapped, seen_days)
        mapped.sort(key=lambda r: ((r.get("name") or ""), r["id"]))
        moves_out = []
        for sid, coords in sorted((patches.get("moves") or {}).items(), key=lambda kv: kv[0]):
            if sid in aliases:
                continue
            moves_out.append({
                "id": sid,
                "name": station_name(applied, sid) or station_name(stations, sid),
                "latitude": coords[0],
                "longitude": coords[1],
            })
        merges_out = []
        for src, dest in sorted(
            (patches.get("merges") or {}).items(), key=lambda kv: (kv[0], kv[1])
        ):
            merges_out.append({
                "from": src,
                "to": dest,
                "fromName": station_name(stations, src),
                "toName": station_name(applied, dest) or station_name(stations, dest),
            })
        return {
            "stations": mapped,
            "moves": moves_out,
            "merges": merges_out,
        }

    def search_payload(self, query):
        with self._lock:
            stations, _statuses, _edge, patches, _path = self._snapshot()
            seen_days = self.seen_days
            ridden_raw = self.ridden
        aliases = alias_map(patches)
        ridden = {canonical_id(sid, aliases) for sid in ridden_raw}
        applied = apply_to_stations(patches, stations)
        hits = search_stations(applied, query)
        out = []
        seen = set()
        for row in hits:
            canon = canonical_id(row.get("id"), aliases)
            if canon is None or canon in seen:
                continue
            if canon != row.get("id"):
                mapped = station_map_row(applied, canon)
                if not mapped:
                    continue
                row = mapped
            row["moved"] = canon in (patches.get("moves") or {})
            row["ridden"] = canon in ridden
            out.append(row)
            seen.add(canon)
        return add_seen(out, seen_days)

    def _commit(self, patches):
        path = self.patches_path
        if not save_patches(path, patches):
            return False, "Datei nicht schreibbar."
        with self._lock:
            self.patches = patches
        if self.on_saved:
            self.on_saved(patches)
        return True, "Gespeichert."

    def apply_move(self, sid, lat, lon):
        with self._lock:
            patches = self.patches
        if not set_move(patches, sid, lat, lon):
            return False, "Ungültige Verschiebung."
        return self._commit(patches)

    def apply_clear_move(self, sid):
        with self._lock:
            patches = self.patches
        clear_move(patches, sid)
        return self._commit(patches)

    def apply_merge(self, from_id, to_id):
        with self._lock:
            patches = self.patches
        src, dest = station_id(from_id), station_id(to_id)
        if src is None or dest is None:
            return False, "Beide Stationen wählen."
        if src == dest:
            return False, "Quelle und Ziel müssen verschieden sein."
        if not set_merge(patches, src, dest):
            return False, "Merge ungültig oder würde einen Zyklus erzeugen."
        return self._commit(patches)

    def apply_clear_merge(self, from_id):
        with self._lock:
            patches = self.patches
        clear_merge(patches, from_id)
        return self._commit(patches)


class _ThreadingServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _read_json_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length < 0 or length > 1_000_000:
        return None, "payload"
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "Kein JSON."
    if not isinstance(payload, dict):
        return None, "Kein Objekt."
    return payload, None


def _make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _send(self, code, body, content_type="application/json; charset=utf-8"):
            if isinstance(body, str):
                raw = body.encode("utf-8")
            else:
                raw = body
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _send_result(self, ok, msg):
            self._send(
                200 if ok else 400,
                json.dumps(
                    {"ok": ok, "error": None if ok else msg, "message": msg},
                    ensure_ascii=False,
                ),
            )

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                try:
                    with open(service.html_path, "r", encoding="utf-8") as f:
                        html = f.read()
                except OSError:
                    self._send(404, json.dumps({"error": "station_patch.html fehlt"}))
                    return
                self._send(200, html, "text/html; charset=utf-8")
                return
            if path == "/api/context":
                qs = urllib.parse.parse_qs(parsed.query)
                extra = []
                for chunk in qs.get("extra") or []:
                    for part in str(chunk).split(","):
                        part = part.strip()
                        if part:
                            extra.append(station_id(part))
                self._send(
                    200,
                    json.dumps(service.context_payload(extra), ensure_ascii=False),
                )
                return
            if path == "/api/search":
                qs = urllib.parse.parse_qs(parsed.query)
                query = (qs.get("q") or [""])[0]
                hits = service.search_payload(query)
                self._send(
                    200, json.dumps({"stations": hits}, ensure_ascii=False)
                )
                return
            self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            payload, err = _read_json_body(self)
            if err:
                self._send(400, json.dumps({"ok": False, "error": err}))
                return
            if path == "/api/move":
                ok, msg = service.apply_move(
                    payload.get("id"), payload.get("latitude"), payload.get("longitude")
                )
                self._send_result(ok, msg)
                return
            if path == "/api/clear-move":
                ok, msg = service.apply_clear_move(payload.get("id"))
                self._send_result(ok, msg)
                return
            if path == "/api/merge":
                ok, msg = service.apply_merge(payload.get("from"), payload.get("to"))
                self._send_result(ok, msg)
                return
            if path == "/api/clear-merge":
                ok, msg = service.apply_clear_merge(payload.get("from"))
                self._send_result(ok, msg)
                return
            self._send(404, json.dumps({"error": "not found"}))

    return Handler
