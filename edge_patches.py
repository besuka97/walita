#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokale Kanten-Patches: Default-Via und Fahrt-Overrides.

Kein API-Write. Die Datei (Default `data/edge_patches.json`) splittet grobe
Träwelling-Kanten (A→B) in topologische Teilstücke A→…→B.
"""

import datetime
import json
import math
import os
import socketserver
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

MISSING = object()

# Korridor um eine Kante: Stationen weiter weg nur per Suche, nicht als Marker.
CORRIDOR_KM = 20.0
SEARCH_LIMIT = 30


def station_id(value):
    """Normalisiert eine Stations-ID zu int, sonst unverändert."""
    if isinstance(value, bool) or value is None:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _via_list(raw):
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        sid = station_id(item)
        if sid is None or sid in out:
            continue
        out.append(sid)
    return out


def empty_patches():
    return {"defaults": {}, "overrides": {}}


def load_patches(path):
    """Liest edge_patches.json. Fehlende/ungültige Datei → leere Struktur.

    defaults: (from_id, to_id) -> [via_ids]
    overrides: (status_id, from_id, to_id) -> [via_ids]  (leere Liste = Default aus)
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
    defaults = {}
    for row in data.get("defaults") or []:
        if not isinstance(row, dict):
            continue
        a, b = station_id(row.get("from")), station_id(row.get("to"))
        if a is None or b is None or a == b:
            continue
        defaults[(a, b)] = _via_list(row.get("via"))
    overrides = {}
    for row in data.get("overrides") or []:
        if not isinstance(row, dict):
            continue
        sid = station_id(row.get("statusId"))
        a, b = station_id(row.get("from")), station_id(row.get("to"))
        if sid is None or a is None or b is None or a == b:
            continue
        overrides[(sid, a, b)] = _via_list(row.get("via"))
    return {"defaults": defaults, "overrides": overrides}


def dumps_patches(patches):
    """Serialisiert die interne Map-Form zurück ins JSON-Objekt."""
    defaults = []
    for (a, b), via in sorted(
        (patches.get("defaults") or {}).items(), key=lambda kv: (kv[0][0], kv[0][1])
    ):
        defaults.append({"from": a, "to": b, "via": list(via)})
    overrides = []
    for (sid, a, b), via in sorted(
        (patches.get("overrides") or {}).items(),
        key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]),
    ):
        overrides.append({"statusId": sid, "from": a, "to": b, "via": list(via)})
    return {"defaults": defaults, "overrides": overrides}


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


def resolve_via(patches, status_id, from_id, to_id):
    """Via-IDs oder None (kein Patch). Leere Liste = Override „nicht expandieren“."""
    if not patches:
        return None
    a, b = station_id(from_id), station_id(to_id)
    sid = station_id(status_id)
    ov = (patches.get("overrides") or {}).get((sid, a, b), MISSING)
    if ov is not MISSING:
        return list(ov)
    default = (patches.get("defaults") or {}).get((a, b))
    if default is None:
        return None
    return list(default)


def lookup_station(stations, sid):
    """Eintrag aus stations.json (Keys oft Strings)."""
    if not stations:
        return None
    return stations.get(str(sid)) or stations.get(sid) or None


def station_coords(stations, sid):
    st = lookup_station(stations, sid)
    if not st:
        return None
    lat, lon = st.get("latitude"), st.get("longitude")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def station_name(stations, sid, fallback=""):
    st = lookup_station(stations, sid) or {}
    return st.get("name") or fallback or str(sid)


def filter_via(via, from_id, to_id, served_ids, stations):
    """Wirft from/to, bediente IDs und Stationen ohne Koordinaten raus."""
    a, b = station_id(from_id), station_id(to_id)
    blocked = {a, b}
    for sid in served_ids or ():
        blocked.add(station_id(sid))
    out = []
    for vid in via or ():
        vid = station_id(vid)
        if vid is None or vid in blocked or vid in out:
            continue
        if station_coords(stations, vid) is None:
            continue
        out.append(vid)
        blocked.add(vid)
    return out


def expand_edge_stopovers(edge_segment, status_id, patches, stations):
    """A→B um Via-Stationen erweitern. Original-Halte unverändert, Via mit Flag.

    `edge_segment` sind die nicht-cancelled Stopovers in Fahrreihenfolge.
    """
    if not edge_segment:
        return []
    served_ids = [s.get("id") for s in edge_segment if s.get("id") is not None]
    out = [edge_segment[0]]
    for a, b in zip(edge_segment, edge_segment[1:]):
        via = resolve_via(patches, status_id, a.get("id"), b.get("id"))
        if via:
            for vid in filter_via(via, a.get("id"), b.get("id"), served_ids, stations):
                out.append({
                    "id": vid,
                    "name": station_name(stations, vid),
                    "physicalThrough": True,
                })
        out.append(b)
    return out


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def dist_to_segment_km(plat, plon, alat, alon, blat, blon):
    """Näherung: Punkt-Abstand zur Sehne A–B in km (äquirektangulär)."""
    # Lokale km-Koordinaten um A.
    lat0 = math.radians((alat + blat + plat) / 3.0)
    def xy(lat, lon):
        return (
            math.radians(lon - alon) * math.cos(lat0) * 6371.0,
            math.radians(lat - alat) * 6371.0,
        )
    px, py = xy(plat, plon)
    ax, ay = 0.0, 0.0
    bx, by = xy(blat, blon)
    vx, vy = bx - ax, by - ay
    mag = vx * vx + vy * vy
    if mag < 1e-12:
        return haversine_km(plat, plon, alat, alon)
    t = max(0.0, min(1.0, (px * vx + py * vy) / mag))
    qx, qy = ax + t * vx, ay + t * vy
    return math.hypot(px - qx, py - qy)


def project_t(plat, plon, alat, alon, blat, blon):
    """0…1 Position auf der Sehne A–B (für Via-Sortierung)."""
    lat0 = math.radians((alat + blat + plat) / 3.0)
    def xy(lat, lon):
        return (
            math.radians(lon - alon) * math.cos(lat0) * 6371.0,
            math.radians(lat - alat) * 6371.0,
        )
    px, py = xy(plat, plon)
    bx, by = xy(blat, blon)
    mag = bx * bx + by * by
    if mag < 1e-12:
        return 0.0
    return max(0.0, min(1.0, (px * bx + py * by) / mag))


def station_map_row(stations, sid, from_id=None, to_id=None):
    """Eine Station als Karten-/Suchzeile, oder None ohne Koordinaten."""
    sid = station_id(sid)
    coords = station_coords(stations, sid)
    if coords is None:
        return None
    row = {
        "id": sid,
        "name": station_name(stations, sid),
        "lat": coords[0],
        "lon": coords[1],
    }
    ca = station_coords(stations, from_id) if from_id is not None else None
    cb = station_coords(stations, to_id) if to_id is not None else None
    if ca and cb:
        row["t"] = project_t(coords[0], coords[1], ca[0], ca[1], cb[0], cb[1])
        row["distKm"] = round(
            dist_to_segment_km(coords[0], coords[1], ca[0], ca[1], cb[0], cb[1]), 2
        )
    return row


def _status_day(status):
    """Lokales 'YYYY-MM-DD' der Abfahrt am Einstieg (wie `status_date` im Export)."""
    checkin = status.get("checkin") or {}
    origin = checkin.get("origin") or {}
    ts = (
        origin.get("departure")
        or origin.get("departurePlanned")
        or status.get("createdAt")
    )
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:10]
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.date().isoformat()


def seen_dates(statuses):
    """{station_id: ('YYYY-MM-DD', 'YYYY-MM-DD')} der frühesten und spätesten
    Fahrt, die die ID enthält.

    Zählt Ein-/Ausstieg und alle Stopover des Trips (auch außerhalb des
    Check-in-Abschnitts), jeweils samt verschachteltem `station`-Objekt.
    Ohne Station-Merges: jede ID behält ihre eigenen Daten.
    """
    out = {}

    def note(obj, day):
        if not isinstance(obj, dict):
            return
        for raw in (obj.get("id"), (obj.get("station") or {}).get("id")
                    if isinstance(obj.get("station"), dict) else None):
            sid = station_id(raw)
            if sid is None:
                continue
            prev = out.get(sid)
            if prev is None:
                out[sid] = (day, day)
            else:
                out[sid] = (min(prev[0], day), max(prev[1], day))

    for status in statuses or []:
        if not isinstance(status, dict):
            continue
        day = _status_day(status)
        if day is None:
            continue
        checkin = status.get("checkin") or {}
        note(checkin.get("origin"), day)
        note(checkin.get("destination"), day)
        trip = status.get("trip")
        if isinstance(trip, dict):
            for stop in trip.get("stopovers") or []:
                note(stop, day)
    return out


def add_seen(rows, seen):
    """Setzt `firstSeen` / `lastSeen` an Kartenzeilen mit bekannten Daten (in place)."""
    for row in rows:
        days = (seen or {}).get(station_id(row.get("id")))
        if days:
            row["firstSeen"], row["lastSeen"] = days
    return rows


def corridor_stations(stations, from_id, to_id, radius_km=CORRIDOR_KM):
    """Stationen mit Koordinaten im Korridor um A–B, ohne A/B selbst."""
    a, b = station_id(from_id), station_id(to_id)
    ca, cb = station_coords(stations, a), station_coords(stations, b)
    if ca is None or cb is None or not stations:
        return []
    out = []
    for key, st in stations.items():
        if not isinstance(st, dict):
            continue
        sid = station_id(st.get("id") if st.get("id") is not None else key)
        if sid in (a, b):
            continue
        row = station_map_row(stations, sid, a, b)
        if not row:
            continue
        if row.get("distKm", 0) > radius_km:
            continue
        out.append(row)
    out.sort(key=lambda r: (r.get("t", 0), r.get("distKm", 0), str(r["name"])))
    return out


def search_stations(stations, query, from_id=None, to_id=None, limit=SEARCH_LIMIT):
    """Name-Teilstring oder exakte ID, nur Stationen mit Koordinaten."""
    q = (query or "").strip()
    if not q or not stations:
        return []
    qn = q.casefold()
    qid = None
    if q.isdigit() or (len(q) > 1 and q[0] in "+-" and q[1:].isdigit()):
        qid = station_id(q)
    hits = []
    seen = set()
    for key, st in stations.items():
        if not isinstance(st, dict):
            continue
        sid = station_id(st.get("id") if st.get("id") is not None else key)
        if sid in seen:
            continue
        name = st.get("name") or str(sid)
        id_hit = qid is not None and sid == qid
        name_hit = qn in name.casefold()
        if not id_hit and not name_hit:
            continue
        row = station_map_row(stations, sid, from_id, to_id)
        if not row:
            continue
        row["exactId"] = bool(id_hit)
        hits.append(row)
        seen.add(sid)
    hits.sort(key=lambda r: (
        0 if r.get("exactId") else 1,
        0 if (r["name"] or "").casefold().startswith(qn) else 1,
        (r["name"] or ""),
        r["id"],
    ))
    return hits[:limit]


def consecutive_pairs(stopovers):
    """Aufeinanderfolgende nicht-cancelled Stopovers (wie die Kantenbildung)."""
    edge_segment = [s for s in (stopovers or []) if not s.get("cancelled")]
    pairs = []
    for a, b in zip(edge_segment, edge_segment[1:]):
        a_id, b_id = a.get("id"), b.get("id")
        if a_id is None or b_id is None or station_id(a_id) == station_id(b_id):
            continue
        pairs.append((a, b))
    return pairs


def served_station_ids(stopovers):
    """IDs, die auf dieser Fahrt schon bedient sind und keine Via sein dürfen.

    Entfällt-Zwischenhalte zählen nicht: die Kante überspringt sie, der Editor
    darf sie als Via nachtragen. Ein- und Ausstieg bleiben gesperrt, auch wenn
    der Halt selbst cancelled ist. Dieselbe ID an einem bedienten Halt sperrt
    sie weiter.
    """
    stops = list(stopovers or [])
    n = len(stops)
    out = []
    seen = set()
    for i, s in enumerate(stops):
        sid = station_id(s.get("id"))
        if sid is None or sid in seen:
            continue
        if s.get("cancelled") and 0 < i < n - 1:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def patch_kind(patches, status_id, from_id, to_id):
    """'override' | 'default' | ''."""
    if not patches:
        return ""
    a, b = station_id(from_id), station_id(to_id)
    sid = station_id(status_id)
    if (sid, a, b) in (patches.get("overrides") or {}):
        return "override"
    if (a, b) in (patches.get("defaults") or {}):
        return "default"
    return ""


def set_default(patches, from_id, to_id, via):
    a, b = station_id(from_id), station_id(to_id)
    if a is None or b is None or a == b:
        return False
    patches.setdefault("defaults", {})[(a, b)] = list(via or [])
    return True


def clear_default(patches, from_id, to_id):
    (patches.get("defaults") or {}).pop(
        (station_id(from_id), station_id(to_id)), None
    )


def set_override(patches, status_id, from_id, to_id, via):
    sid, a, b = station_id(status_id), station_id(from_id), station_id(to_id)
    if sid is None or a is None or b is None or a == b:
        return False
    patches.setdefault("overrides", {})[(sid, a, b)] = list(via or [])
    return True


def clear_override(patches, status_id, from_id, to_id):
    (patches.get("overrides") or {}).pop(
        (station_id(status_id), station_id(from_id), station_id(to_id)), None
    )


PATCH_MAP_PORT = 8711
_PATCH_HTML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dashboard", "edge_patch.html"
)


class PatchMapService:
    """Einmaliger localhost-HTTP-Server für die Patch-Karte (Port 8711)."""

    def __init__(self, patches_path, html_path=None, on_saved=None):
        self.patches_path = patches_path
        self.html_path = html_path or _PATCH_HTML
        self.on_saved = on_saved
        self.stations = {}
        self.patches = empty_patches()
        self.status_id = None
        self.from_id = None
        self.to_id = None
        self.from_name = ""
        self.to_name = ""
        self.served_ids = []
        self.seen_days = {}
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

    def set_edge(self, *, status_id, from_id, to_id, from_name, to_name,
                 served_ids, stations, patches, seen_days=None):
        with self._lock:
            self.status_id = station_id(status_id)
            self.from_id = station_id(from_id)
            self.to_id = station_id(to_id)
            self.from_name = from_name or station_name(stations, from_id)
            self.to_name = to_name or station_name(stations, to_id)
            self.served_ids = [station_id(s) for s in (served_ids or [])]
            self.stations = stations or {}
            self.patches = patches or empty_patches()
            self.seen_days = seen_days or {}

    def context_payload(self):
        with self._lock:
            stations = self.stations
            patches = self.patches
            sid = self.status_id
            a, b = self.from_id, self.to_id
            from_name, to_name = self.from_name, self.to_name
            served = list(self.served_ids)
            seen_days = self.seen_days
        ca = station_coords(stations, a)
        cb = station_coords(stations, b)
        kind = patch_kind(patches, sid, a, b)
        via = resolve_via(patches, sid, a, b)
        if via is None:
            via = []
        corridor = corridor_stations(stations, a, b) if ca and cb else []
        seen = {a, b}
        mapped = []
        for row in corridor:
            seen.add(row["id"])
            mapped.append(row)
        for vid in via:
            vid = station_id(vid)
            if vid in seen:
                continue
            extra = station_map_row(stations, vid, a, b)
            if extra:
                mapped.append(extra)
                seen.add(vid)
        add_seen(mapped, seen_days)
        return {
            "statusId": sid,
            "corridorKm": CORRIDOR_KM,
            "from": {
                "id": a,
                "name": from_name,
                "lat": ca[0] if ca else None,
                "lon": ca[1] if ca else None,
            },
            "to": {
                "id": b,
                "name": to_name,
                "lat": cb[0] if cb else None,
                "lon": cb[1] if cb else None,
            },
            "via": via,
            "source": kind,
            "servedIds": served,
            "stations": mapped,
        }

    def search_payload(self, query):
        with self._lock:
            stations = self.stations
            a, b = self.from_id, self.to_id
            seen_days = self.seen_days
        return add_seen(search_stations(stations, query, a, b), seen_days)

    def apply_save(self, mode, via_raw):
        with self._lock:
            stations = self.stations
            patches = self.patches
            sid = self.status_id
            a, b = self.from_id, self.to_id
            served = list(self.served_ids)
            path = self.patches_path
        if a is None or b is None:
            return False, "Keine Kante gewählt."
        served_filter = served if mode == "trip" else []
        via = filter_via(_via_list(via_raw), a, b, served_filter, stations)
        if mode == "default":
            set_default(patches, a, b, via)
        elif mode == "trip":
            if sid is None:
                return False, "Keine Fahrt gewählt."
            set_override(patches, sid, a, b, via)
        else:
            return False, "Ungültiger Speichermodus."
        if not save_patches(path, patches):
            return False, "Datei nicht schreibbar."
        with self._lock:
            self.patches = patches
        if self.on_saved:
            self.on_saved(patches)
        return True, "Gespeichert."


class _ThreadingServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


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

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                try:
                    with open(service.html_path, "r", encoding="utf-8") as f:
                        html = f.read()
                except OSError:
                    self._send(404, json.dumps({"error": "edge_patch.html fehlt"}))
                    return
                self._send(200, html, "text/html; charset=utf-8")
                return
            if path == "/api/context":
                self._send(200, json.dumps(service.context_payload(), ensure_ascii=False))
                return
            if path == "/api/search":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                query = (qs.get("q") or [""])[0]
                hits = service.search_payload(query)
                self._send(200, json.dumps({"stations": hits}, ensure_ascii=False))
                return
            self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if path != "/api/save":
                self._send(404, json.dumps({"error": "not found"}))
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 1_000_000:
                self._send(400, json.dumps({"error": "payload"}))
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._send(400, json.dumps({"ok": False, "error": "Kein JSON."}))
                return
            if not isinstance(payload, dict):
                self._send(400, json.dumps({"ok": False, "error": "Kein Objekt."}))
                return
            ok, msg = service.apply_save(payload.get("mode"), payload.get("via"))
            self._send(200 if ok else 400, json.dumps(
                {"ok": ok, "error": None if ok else msg, "message": msg},
                ensure_ascii=False,
            ))

    return Handler
