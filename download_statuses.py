#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lädt alle eigenen Träwelling-Statuses inkl. vollständiger Stopover-Liste herunter.

Die Status-Liste (`/user/{username}/statuses`) enthält pro Status nur Start- und
Zielhalt. Die Zwischenhalte kommen von `/stopovers/{tripIds}`, das bis zu 50 Trips
pro Anfrage liefert; die dafür nötige Trip-ID steht bereits als `checkin.trip` im
Status. Das Ergebnis hängt als Feld `trip` mit `stopovers` am Status.

Auth: entweder Personal Access Token (Bearer, Env-Var TRWL_TOKEN oder --token) mit dem
Scope `read-statuses`, oder OAuth-Login via `--login` (siehe auth.py; das Token wird
lokal in data/oauth_token.json zwischengespeichert). Token/PAT erstellen unter
https://traewelling.de/settings/applications

Beispiel:
    export TRWL_TOKEN="..."
    python3 download_statuses.py
    python3 download_statuses.py --login         # OAuth-Login im Browser (statt TRWL_TOKEN)
    python3 download_statuses.py --limit 3       # kleiner Testlauf
    python3 download_statuses.py --skip-trips    # nur Liste, ohne Zwischenhalte
    python3 download_statuses.py --full          # alle Statuses laden

Liegt statuses.json schon vor, werden nur neue Statuses und die letzten
INCREMENTAL_OVERLAP_DAYS Tage davor neu geladen (siehe `incremental_cutoff`);
ältere bleiben aus der Datei. Änderungen an älteren Fahrten und nachträgliche
Check-ins kommen mit --full an.
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import auth
from version import USER_AGENT, __version__

BASE = "https://traewelling.de/api/v1"

# Pause zwischen Trip-/Stations-Requests, um die API-Rate-Limits zu schonen (Sekunden).
TRIP_REQUEST_DELAY = 0.15
# Mehr IDs wertet /stopovers pro Anfrage nicht aus; überzählige fallen still weg.
STOPOVER_BATCH = 50
MAX_RETRIES = 4
# Inkrementeller Abruf: so viele Tage vor dem neuesten bekannten Status werden
# erneut geladen, damit Korrekturen an jüngeren Fahrten ankommen.
INCREMENTAL_OVERLAP_DAYS = 2
# Station-Identifier (IBNR, DHID/IFOPT, MOTIS, …) an Stopovers und GET /station/{id}.
WITH_IDENTIFIERS = {"withIdentifiers": "true"}


def log(msg):
    """Fortschrittsausgabe auf stderr, damit stdout/Datei sauber bleibt."""
    print(msg, file=sys.stderr, flush=True)


def api_request(method, path_or_url, token, params=None, json_body=None):
    """Führt einen HTTP-Request gegen die API aus und gibt das geparste JSON zurück.

    `path_or_url` darf ein Pfad ("/auth/user") oder eine vollständige URL sein
    (z. B. aus `links.next`). `json_body` wird als JSON-Body gesendet (PUT/POST).
    Leere Antworten (z. B. 204) ergeben `{}`. Behandelt 429 (Rate-Limit) und
    5xx mit Retry; andere 4xx werden als `ApiError` geworfen.
    """
    if path_or_url.startswith("http"):
        url = path_or_url
    else:
        url = BASE + path_or_url
    if params:
        url = url + "?" + urllib.parse.urlencode(params)

    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    data = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 429:
                retry_after = e.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                log(f"  Rate-Limit (429), warte {wait}s (Versuch {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            if 500 <= e.code < 600:
                wait = 2 ** attempt
                log(f"  Serverfehler {e.code}, warte {wait}s (Versuch {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            # 4xx (außer 429): nicht wiederholbar -> Fehlertext durchreichen
            body = e.read().decode("utf-8", errors="replace")
            raise ApiError(e.code, body) from e
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
            wait = 2 ** attempt
            log(f"  Netzwerkfehler ({e}), warte {wait}s (Versuch {attempt}/{MAX_RETRIES})")
            time.sleep(wait)

    raise ApiError(None, f"Maximale Versuche erschöpft: {last_error}")


def api_get(path_or_url, token, params=None):
    """GET-Request gegen die API; siehe `api_request`."""
    return api_request("GET", path_or_url, token, params=params)


def url_with_query(path_or_url, extra):
    """Hängt Query-Parameter an einen Pfad oder eine volle URL an (gleiche Keys werden überschrieben)."""
    parts = urllib.parse.urlsplit(path_or_url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query.update(extra)
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc, parts.path,
        urllib.parse.urlencode(query), parts.fragment,
    ))


class ApiError(Exception):
    def __init__(self, code, body):
        self.code = code
        self.body = body
        super().__init__(f"HTTP {code}: {body[:300]}")


def get_username(token):
    """Ermittelt den eigenen Benutzernamen via /auth/user."""
    data = api_get("/auth/user", token)
    username = data.get("data", {}).get("username")
    if not username:
        raise ApiError(None, f"Konnte Username nicht aus /auth/user lesen: {data}")
    return username


def local_day(ts):
    """'YYYY-MM-DD' eines ISO-Zeitstempels in lokaler Zeit, sonst None.

    Die API liefert UTC; ohne Umrechnung landen späte Fahrten am Vortag bzw.
    frühe am Folgetag. Zeitstempel ohne Zone gelten als lokal.
    """
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:10]
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.date().isoformat()


def status_date(status):
    """Maßgebliches Datum eines Status als lokaler 'YYYY-MM-DD'-String.

    Nutzt die tatsächliche Abfahrt (`checkin.origin.departure`) und fällt auf
    `departurePlanned` bzw. `createdAt` zurück, wenn diese fehlt (z. B. alte,
    nicht auflösbare Trips). Gibt None zurück, wenn kein Datum ermittelbar ist.
    """
    checkin = status.get("checkin") or {}
    origin = checkin.get("origin") or {}
    ts = (
        origin.get("departure")
        or origin.get("departurePlanned")
        or status.get("createdAt")
    )
    return local_day(ts)


def status_sort_time(status):
    """Zeitpunkt, nach dem `/user/{username}/statuses` absteigend sortiert.

    Das ist die geplante Abfahrt am Einstieg; Fallback auf `departure` bzw.
    `createdAt`. Gibt ein zeitzonenbehaftetes datetime oder None zurück.
    """
    checkin = status.get("checkin") or {}
    origin = checkin.get("origin") or {}
    for ts in (origin.get("departurePlanned"), origin.get("departure"),
               status.get("createdAt")):
        if not isinstance(ts, str):
            continue
        try:
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    return None


def incremental_cutoff(statuses, now=None):
    """Grenze für den inkrementellen Abruf, oder None (dann alles laden).

    Neuester bekannter Status, der nicht in der Zukunft liegt, minus
    INCREMENTAL_OVERLAP_DAYS. Zukünftige Check-ins zählen nicht: sie stehen
    vorn in der Liste und liegen hinter allen neuen vergangenen Fahrten.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    past = [t for t in map(status_sort_time, statuses) if t is not None and t <= now]
    if not past:
        return None
    return max(past) - datetime.timedelta(days=INCREMENTAL_OVERLAP_DAYS)


def merge_incremental(fetched, existing):
    """Neu geladene Statuses plus die älteren aus der vorhandenen Datei.

    Die API liefert absteigend sortiert; alles ab dem ältesten neu geladenen
    Status gilt als frisch. Was dort in der Datei steht, aber in der Antwort
    fehlt, gilt als gelöscht und fällt weg. Ältere Statuses bleiben
    unverändert (inklusive lokal zusammengeführter Tags und Texte).
    """
    ids = {s.get("id") for s in fetched}
    times = [t for t in map(status_sort_time, fetched) if t is not None]
    boundary = min(times) if times else None
    kept = []
    for status in existing:
        if status.get("id") in ids:
            continue
        t = status_sort_time(status)
        if boundary is None or t is None or t <= boundary:
            kept.append(status)
    return list(fetched) + kept


def load_existing_statuses(path):
    """Liest eine vorhandene statuses.json; None, wenn sie fehlt oder unlesbar ist."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            statuses = json.load(f)
    except (OSError, ValueError) as e:
        log(f"Vorhandene {path} nicht lesbar ({e}), lade alle Statuses.")
        return None
    return statuses if isinstance(statuses, list) else None


def iter_statuses(username, token, limit=None, since=None, stop_before=None):
    """Iteriert über alle Statuses des Nutzers, folgt der Cursor-Pagination.

    `since` (String 'YYYY-MM-DD') filtert auf Statuses, deren Abfahrtsdatum
    *strikt nach* diesem Tag liegt (siehe `status_date`). Statuses ohne
    ermittelbares Datum werden dann übersprungen.

    `stop_before` (datetime) beendet das Blättern nach der ersten Seite, deren
    ältester Status (`status_sort_time`) davor liegt; die Seite selbst wird
    noch vollständig geliefert.
    """
    count = 0
    skipped = 0
    next_url = url_with_query(
        f"/user/{urllib.parse.quote(username)}/statuses", WITH_IDENTIFIERS
    )
    page = 0
    while next_url:
        page += 1
        payload = api_get(next_url, token)
        rows = payload.get("data", [])
        log(f"Seite {page} geladen ({len(rows)} Statuses)")
        for status in rows:
            if since and (status_date(status) or "") <= since:
                skipped += 1
                continue
            yield status
            count += 1
            if limit and count >= limit:
                return
        if stop_before is not None and rows:
            times = [t for t in map(status_sort_time, rows) if t is not None]
            if times and min(times) < stop_before:
                log(f"Bekannter Bereich erreicht (vor {stop_before:%Y-%m-%d %H:%M} UTC), "
                    "höre auf zu blättern.")
                break
        next_url = payload.get("links", {}).get("next")
        if next_url:
            next_url = url_with_query(next_url, WITH_IDENTIFIERS)
    if since:
        log(f"Datumsfilter (> {since}): {count} behalten, {skipped} übersprungen.")


def fetch_stopovers(trip_ids, token, cache):
    """Holt die Stopovers zu mehreren Trip-IDs und legt sie in `cache` ab.

    `cache` bildet `trip_id -> (stopovers, error)`; bereits enthaltene IDs werden
    übersprungen. Jeder Stopover trägt ein `station`-Objekt mit Koordinaten;
    Identifier kommen mit, wenn die API `withIdentifiers` am Endpoint auswertet.
    """
    todo = []
    for raw in trip_ids:
        try:
            tid = int(raw)
        except (TypeError, ValueError):
            continue
        if tid not in cache and tid not in todo:
            todo.append(tid)

    for i in range(0, len(todo), STOPOVER_BATCH):
        batch = todo[i:i + STOPOVER_BATCH]
        path = url_with_query(
            "/stopovers/" + ",".join(str(t) for t in batch), WITH_IDENTIFIERS
        )
        try:
            data = api_get(path, token).get("data") or {}
        except ApiError as e:
            for tid in batch:
                cache[tid] = (None, f"HTTP {e.code}")
        else:
            for tid in batch:
                stopovers = data.get(str(tid))
                if isinstance(stopovers, list) and stopovers:
                    cache[tid] = (stopovers, None)
                else:
                    cache[tid] = (None, "Trip nicht in der Antwort enthalten")
        log(f"  Stopovers: {min(i + STOPOVER_BATCH, len(todo))}/{len(todo)} Trips "
            f"nachgeladen (Cache: {len(cache)})")
        time.sleep(TRIP_REQUEST_DELAY)


def traveled_stopovers(status):
    """Gibt das tatsächlich befahrene Teilstück der Trip-Stopovers zurück.

    Slice von `checkin.origin` bis `checkin.destination` (inklusive). Bevorzugt
    `stopoverId` (eindeutig bei Ringfahrten); Fallback: erste Origin-Station,
    dann erste Ziel-Station mit Index strikt nach dem Start. Fällt auf die
    komplette Stopover-Liste bzw. origin/destination zurück, wenn die Grenzen
    nicht eindeutig bestimmbar sind. Gibt eine Liste von Stopover-Dicts zurück.

    ACHTUNG: identische Kopie in build_dashboard.py – Änderungen dort
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


def _trip_cache_from_statuses(statuses):
    """Sammelt die Stopovers aus Status-Dicts in die Memory-Cache-Form.

    Gibt ein Dict `trip_id -> (stopovers, None)` zurück. Statuses ohne Stopovers
    oder ohne `checkin.trip` werden übersprungen.
    """
    cache = {}
    for status in statuses or []:
        trip = status.get("trip")
        if not isinstance(trip, dict):
            continue
        stopovers = trip.get("stopovers")
        if not isinstance(stopovers, list) or not stopovers:
            continue
        tid = (status.get("checkin") or {}).get("trip")
        try:
            cache[int(tid)] = (stopovers, None)
        except (TypeError, ValueError):
            continue
    return cache


def load_trip_cache(path, statuses_path=None):
    """Liest trips.json als persistenten Stopover-Cache ein.

    Keys in der Datei sind Trip-IDs als String, Werte die zugehörigen
    Stopover-Listen; intern `trip_id -> (stopovers, None)`. Fehlt die Datei und
    `statuses_path` zeigt auf eine vorhandene statuses.json, werden die Stopovers
    daraus übernommen.
    """
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            log(f"Trips-Cache {path} nicht lesbar ({e}), starte ohne Cache.")
            return {}
        cache = {}
        verworfen = 0
        for key, stopovers in (raw or {}).items():
            if not isinstance(stopovers, list) or not stopovers:
                verworfen += 1
                continue
            try:
                cache[int(key)] = (stopovers, None)
            except (TypeError, ValueError):
                verworfen += 1
        if verworfen and not cache:
            log(f"Trip-Cache {path} nicht im erwarteten Format – wird neu aufgebaut.")
            return {}
        log(f"Trips-Cache: {len(cache)} Trips geladen aus {path}.")
        return cache

    if statuses_path and os.path.exists(statuses_path):
        try:
            with open(statuses_path, "r", encoding="utf-8") as f:
                statuses = json.load(f)
        except (OSError, ValueError) as e:
            log(f"Trips-Bootstrap aus {statuses_path} nicht lesbar ({e}).")
            return {}
        if not isinstance(statuses, list):
            return {}
        cache = _trip_cache_from_statuses(statuses)
        if cache:
            log(f"Trips-Cache: {len(cache)} Trips aus {statuses_path} geseedet "
                f"(kein {path or 'trips.json'} vorhanden).")
        return cache

    return {}


def save_trip_cache(path, cache):
    """Schreibt die erfolgreich geladenen Stopovers aus dem Memory-Cache nach trips.json.

    Fehler-Einträge (`stopovers is None`) werden nicht persistiert, damit temporäre
    API-Fehler beim nächsten Lauf erneut versucht werden.

    Wird auch aus einem `finally` heraus aufgerufen (Abbruch mit Ctrl-C), darf also
    weder eine laufende Exception überdecken noch eine halb geschriebene Datei
    hinterlassen: Schreibfehler werden geloggt statt geworfen, und geschrieben wird
    über eine temporäre Datei mit anschließendem `os.replace` (atomar).
    """
    raw = {}
    for tid, (stopovers, _err) in cache.items():
        if not stopovers:
            continue
        raw[str(tid)] = [_without_identifiers(st) for st in stopovers]
    tmp = path + ".tmp"
    try:
        ensure_parent_dir(path)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        log(f"Warnung: Trip-Cache {path} nicht schreibbar ({e}).")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return
    log(f"Geschrieben: {path} ({len(raw)} Trips)")


def load_station_cache(path):
    """Liest eine bereits geschriebene stations.json als Cache ein.

    JSON-Keys sind Strings; intern wird die Map mit Integer-IDs geführt, damit
    sie zu den `id`-Feldern aus den Statuses passt. Gibt ein leeres Dict zurück,
    wenn die Datei fehlt oder unlesbar ist.
    """
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        log(f"Stations-Cache {path} nicht lesbar ({e}), starte ohne Cache.")
        return {}

    cache = {}
    for key, entry in (raw or {}).items():
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if sid is None:
            try:
                sid = int(key)
            except (TypeError, ValueError):
                continue
        cache[sid] = entry
    cached_coords = sum(1 for e in cache.values() if e.get("latitude") is not None)
    cached_ids = sum(1 for e in cache.values() if isinstance(e.get("identifiers"), list))
    log(f"Stations-Cache: {len(cache)} Stationen geladen "
        f"({cached_coords} mit Koordinaten, {cached_ids} mit Identifiers) aus {path}.")
    return cache


def _station_ident_summary(stations):
    """Zählt Identifier-Typen über alle Stationen (für die Log-Zeile)."""
    types = {}
    with_ids = 0
    for entry in stations.values():
        idents = entry.get("identifiers")
        if not isinstance(idents, list) or not idents:
            continue
        with_ids += 1
        for ident in idents:
            if not isinstance(ident, dict):
                continue
            kind = ident.get("type") or "?"
            types[kind] = types.get(kind, 0) + 1
    return with_ids, types


def resolve_stations(statuses, token, cache=None):
    """Baut eine Map station_id -> {id, name, latitude, longitude, identifiers}.

    Koordinaten kommen gratis aus dem `station`-Objekt jedes Stopovers; Identifier
    (IBNR, DHID/IFOPT, MOTIS, …) nur, wenn die API sie mitliefert. Was fehlt,
    wird per `GET /station/{id}?withIdentifiers=true` nachgeladen. Es werden
    *alle* Halte der kompletten Trip-Route berücksichtigt (nicht nur das
    befahrene Teilstück); bei Status ohne auflösbaren Trip fällt es auf
    Start/Ziel des Checkins zurück.

    `cache` seedet die Map mit bereits aufgelösten Stationen (siehe
    `load_station_cache`), damit Koordinaten und Identifier nicht erneut per
    `GET /station/{id}` geholt werden müssen.
    """
    stations = dict(cache) if cache else {}

    def add(station, with_coords):
        sid = station.get("id")
        if sid is None:
            return
        existing = stations.get(sid) or {}
        entry = {
            "id": sid,
            "name": station.get("name") or existing.get("name"),
            "latitude": existing.get("latitude"),
            "longitude": existing.get("longitude"),
        }
        if with_coords:
            if entry["latitude"] is None:
                entry["latitude"] = station.get("latitude")
            if entry["longitude"] is None:
                entry["longitude"] = station.get("longitude")
        if isinstance(existing.get("identifiers"), list):
            entry["identifiers"] = existing["identifiers"]
        elif isinstance(station.get("identifiers"), list):
            entry["identifiers"] = station["identifiers"]
        stations[sid] = entry

    needed = set()
    for status in statuses:
        trip = status.get("trip") or {}
        # Komplette Route cachen (alle Halte des Trips). Trägt ein Status keinen
        # auflösbaren Trip, liefert traveled_stopovers wenigstens Start/Ziel.
        stopovers = trip.get("stopovers") or []
        route = stopovers if stopovers else traveled_stopovers(status)
        for st in route:
            # Jeder Stopover trägt sein `station`-Objekt inklusive Koordinaten mit;
            # ohne das bleibt nur der Name aus dem flachen Teil des Stopovers.
            station = st.get("station")
            if station:
                add(station, with_coords=True)
            else:
                add(st, with_coords=False)
            if st.get("id") is not None:
                needed.add(st["id"])

    missing = [
        sid for sid in (needed | set(stations))
        if stations.get(sid, {}).get("latitude") is None
        or not isinstance(stations.get(sid, {}).get("identifiers"), list)
    ]
    log(f"Stationen: {len(needed)} im Einsatz, {len(missing)} nachzuladen "
        f"(Koordinaten und/oder Identifier).")
    for i, sid in enumerate(missing, 1):
        existing = stations.get(sid) or {}
        try:
            data = api_get(
                url_with_query(f"/station/{sid}", WITH_IDENTIFIERS), token
            ).get("data", {})
            idents = data.get("identifiers")
            stations[sid] = {
                "id": sid,
                "name": data.get("name") or existing.get("name"),
                "latitude": data.get("latitude")
                if data.get("latitude") is not None else existing.get("latitude"),
                "longitude": data.get("longitude")
                if data.get("longitude") is not None else existing.get("longitude"),
                "identifiers": idents if isinstance(idents, list) else [],
            }
        except ApiError as e:
            log(f"  Station {sid} nicht auflösbar: {e}")
            # 404: Station unbekannt – leere Liste merken, nicht jedes Mal neu fragen.
            # Andere Fehler (429, 5xx): Schlüssel weglassen, nächster Lauf versucht es erneut.
            if e.code == 404:
                stations[sid] = {
                    "id": sid,
                    "name": existing.get("name"),
                    "latitude": existing.get("latitude"),
                    "longitude": existing.get("longitude"),
                    "identifiers": [],
                }
        if i % 25 == 0 or i == len(missing):
            log(f"  Stationen: {i}/{len(missing)}")
        time.sleep(TRIP_REQUEST_DELAY)
    return stations


def _without_identifiers(st):
    """Kopie eines Stopovers ohne `identifiers` (auch im verschachtelten station-Objekt).

    Identifier stehen pro Station nur in stations.json. Stopovers ohne
    Identifier kommen als dasselbe Objekt zurück.
    """
    if not isinstance(st, dict):
        return st
    station = st.get("station")
    has_nested = isinstance(station, dict) and "identifiers" in station
    if "identifiers" not in st and not has_nested:
        return st
    out = {k: v for k, v in st.items() if k != "identifiers"}
    if has_nested:
        out["station"] = {k: v for k, v in station.items() if k != "identifiers"}
    return out


def strip_identifiers_from_statuses(statuses):
    """Entfernt Identifier aus Origin, Destination und Trip-Stopovers (in place)."""
    for status in statuses:
        checkin = status.get("checkin")
        if isinstance(checkin, dict):
            for key in ("origin", "destination"):
                if key in checkin:
                    checkin[key] = _without_identifiers(checkin[key])
        trip = status.get("trip")
        if isinstance(trip, dict) and isinstance(trip.get("stopovers"), list):
            trip["stopovers"] = [_without_identifiers(st) for st in trip["stopovers"]]


def ensure_parent_dir(path):
    """Legt das übergeordnete Verzeichnis von `path` an, falls es noch nicht existiert."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def load_operator_replacements(path):
    """Liest die manuelle Operator-Ersetzungsdatei (JSON-Objekt alt -> neu).

    Schlüssel mit führendem '_' (z.B. '_comment') werden übersprungen.
    Fehlt die Datei oder ist sie leer, wird {} zurückgegeben.
    """
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Operator-Ersetzungen {path} nicht lesbar: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"Operator-Ersetzungen {path}: erwartet ein JSON-Objekt "
            f"{{alt: neu}}, bekommen {type(data).__name__}."
        )
    out = {}
    for key, value in data.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"Operator-Ersetzungen {path}: Wert für {key!r} muss "
                f"ein nicht-leerer String sein."
            )
        out[key] = value
    return out


def apply_operator_replacements(statuses, replacements):
    """Schreibt kanonische Operator-Namen in `checkin.operator.name`.

    Gibt die Anzahl der geänderten Statuses zurück.
    """
    if not replacements:
        return 0
    changed = 0
    for status in statuses:
        checkin = status.get("checkin")
        if not isinstance(checkin, dict):
            continue
        operator = checkin.get("operator")
        if not isinstance(operator, dict):
            continue
        name = operator.get("name")
        if name in replacements:
            operator["name"] = replacements[name]
            changed += 1
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"walita {__version__}",
        help="Version ausgeben und beenden.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("TRWL_TOKEN"),
        help="Personal Access Token (sonst aus Env-Var TRWL_TOKEN).",
    )
    parser.add_argument(
        "--login", action="store_true",
        help="Interaktiven OAuth-Login im Browser erzwingen (statt TRWL_TOKEN/Cache).",
    )
    parser.add_argument(
        "--logout", action="store_true",
        help="Gespeichertes OAuth-Token löschen und beenden.",
    )
    parser.add_argument(
        "--client-id", default=os.environ.get("TRWL_CLIENT_ID") or auth.DEFAULT_CLIENT_ID,
        help="OAuth-Client-ID (sonst Env TRWL_CLIENT_ID bzw. auth.DEFAULT_CLIENT_ID).",
    )
    parser.add_argument(
        "--redirect-uri", default=os.environ.get("TRWL_REDIRECT_URI") or auth.DEFAULT_REDIRECT,
        help="OAuth-Redirect-URL, muss exakt zum registrierten Client passen "
             f"(Default: {auth.DEFAULT_REDIRECT}).",
    )
    parser.add_argument(
        "--manual", action="store_true",
        help="OAuth-Code manuell einfügen statt lokalem Callback-Server "
             "(z.B. unter WSL). Redirect-Seite muss dann nicht laden.",
    )
    parser.add_argument(
        "--oauth-token-file", default="data/oauth_token.json",
        help="Ablageort des OAuth-Tokens (Default: data/oauth_token.json).",
    )
    parser.add_argument(
        "--scopes", default=auth.DEFAULT_SCOPES,
        help=f"OAuth-Scopes für den Login (Default: {auth.DEFAULT_SCOPES}).",
    )
    parser.add_argument(
        "--output", "-o", default="data/statuses.json",
        help="Zieldatei (Default: data/statuses.json)."
    )
    parser.add_argument(
        "--skip-trips",
        action="store_true",
        help="Nur Status-Liste laden, keine vollständigen Zwischenhalte nachladen.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Max. Anzahl Statuses (zum Testen)."
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Alle Statuses laden. Ohne das Flag lädt der Export bei vorhandener "
             f"Ausgabedatei nur neue und die letzten {INCREMENTAL_OVERLAP_DAYS} Tage "
             "davor; für Änderungen an älteren Fahrten und nachträgliche Check-ins.",
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", default="",
        help="Nur Statuses mit Abfahrtsdatum strikt nach diesem Tag herunterladen. "
             "Default: leer, also alle Statuses. "
             "Beispiel: --since 2026-01-01 lädt alles ab dem 02.01.2026.",
    )
    parser.add_argument(
        "--stations-output", default="data/stations.json",
        help="Zieldatei für die Stations-Koordinaten (Default: data/stations.json).",
    )
    parser.add_argument(
        "--no-stations", action="store_true",
        help="Keine Stations-Koordinaten auflösen (keine stations.json schreiben).",
    )
    parser.add_argument(
        "--refresh-stations", action="store_true",
        help="Vorhandene stations.json nicht als Cache nutzen, Koordinaten und "
             "Identifier neu auflösen.",
    )
    parser.add_argument(
        "--trips-output", default="data/trips.json",
        help="Persistenter Trip-Cache (Default: data/trips.json).",
    )
    parser.add_argument(
        "--refresh-trips", action="store_true",
        help="Vorhandenen Trip-Cache nicht nutzen, alle Trips neu von der API laden.",
    )
    parser.add_argument(
        "--operator-replacements", default="data/operator_replacements.json",
        help="JSON-Datei mit Operator-Namen-Ersetzungen (alt -> neu). "
             "Default: data/operator_replacements.json. "
             "Fehlt die Datei, wird nichts ersetzt.",
    )
    args = parser.parse_args(argv)

    if args.logout:
        if os.path.exists(args.oauth_token_file):
            try:
                os.remove(args.oauth_token_file)
            except OSError as e:
                log(f"Fehler: OAuth-Token {args.oauth_token_file} nicht löschbar ({e}).")
                return 1
            log(f"OAuth-Token gelöscht: {args.oauth_token_file}")
        else:
            log(f"Kein gespeichertes OAuth-Token unter {args.oauth_token_file}.")
        return 0

    # Token bestimmen: expliziter PAT (--token/TRWL_TOKEN) hat Vorrang, außer --login
    # erzwingt den OAuth-Login. Sonst OAuth: gültiges Cache-Token nutzen, per
    # refresh_token erneuern oder interaktiv im Browser anmelden (siehe auth.py).
    client_secret = os.environ.get("TRWL_CLIENT_SECRET")
    if args.token and not args.login:
        token = args.token
    else:
        try:
            token = auth.get_access_token(
                args.oauth_token_file, args.client_id, redirect=args.redirect_uri,
                scopes=args.scopes, secret=client_secret, force_login=args.login,
                manual=args.manual,
            )
        except auth.AuthError as e:
            log(f"OAuth-Login fehlgeschlagen: {e}")
            return 1

    if not token:
        log("Fehler: Kein Token. Setze TRWL_TOKEN, nutze --token oder --login "
            "(ggf. --client-id angeben).")
        return 2

    if args.since:
        try:
            datetime.datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            log("Fehler: --since erwartet ein Datum im Format YYYY-MM-DD.")
            return 2

    try:
        username = get_username(token)
    except ApiError as e:
        log(f"Authentifizierung fehlgeschlagen: {e}")
        return 1
    log(f"Angemeldet als: {username}")

    existing = None
    if args.full:
        log("--full: lade alle Statuses.")
    elif args.limit:
        log("--limit: lade ohne Abgleich mit vorhandener Datei.")
    else:
        existing = load_existing_statuses(args.output)
    cutoff = incremental_cutoff(existing) if existing else None
    if existing is not None and cutoff is None:
        log(f"{args.output} enthält keine vergangene Fahrt, lade alle Statuses.")
    elif cutoff is not None:
        log(f"Inkrementell: {len(existing)} Statuses aus {args.output}, lade neue "
            f"und ab {cutoff:%Y-%m-%d %H:%M} UTC neu (--full für alle).")

    try:
        statuses = list(iter_statuses(
            username, token, limit=args.limit, since=args.since, stop_before=cutoff,
        ))
    except ApiError as e:
        log(f"Abruf der Statuses fehlgeschlagen: {e}")
        return 1
    log(f"Insgesamt {len(statuses)} Statuses geladen.")
    if cutoff is not None:
        fetched = len(statuses)
        if args.since:
            existing = [s for s in existing if (status_date(s) or "") > args.since]
        statuses = merge_incremental(statuses, existing)
        log(f"Zusammengeführt: {fetched} neu geladen + {len(statuses) - fetched} "
            f"aus {args.output} = {len(statuses)} Statuses.")

    if not args.skip_trips:
        if args.refresh_trips:
            cache = {}
        else:
            cache = load_trip_cache(args.trips_output, statuses_path=args.output)
        # Der Cache wird im finally geschrieben: ein Abbruch (Ctrl-C) oder Fehler
        # mitten im Nachladen darf die bereits getätigten API-Calls nicht verwerfen.
        try:
            fetch_stopovers(
                [(s.get("checkin") or {}).get("trip") for s in statuses], token, cache
            )
        finally:
            save_trip_cache(args.trips_output, cache)

        errors = 0
        for status in statuses:
            tid = (status.get("checkin") or {}).get("trip")
            stopovers, err = cache.get(tid, (None, "keine Trip-ID im Status"))
            status["trip"] = {"stopovers": stopovers} if stopovers else None
            status.pop("trip_error", None)
            if err:
                status["trip_error"] = err
                errors += 1
        log(f"Trips fertig. {errors} Statuses ohne auflösbaren Trip.")
        if errors and any(
            (s.get("trip_error") or "").startswith("HTTP 403") for s in statuses
        ):
            log(
                "Hinweis: HTTP 403 bei /stopovers bedeutet meist fehlenden Scope "
                "'read-statuses'. Mit OAuth: erneut `--login`; "
                "mit PAT: Token inkl. read-statuses neu ausstellen."
            )

    # Operator-Namen vereinheitlichen (manuelle Mapping-Datei, vor dem Schreiben).
    try:
        replacements = load_operator_replacements(args.operator_replacements)
    except ValueError as e:
        log(f"Fehler: {e}")
        return 1
    if replacements:
        n = apply_operator_replacements(statuses, replacements)
        log(
            f"Operator-Ersetzungen: {n} Statuses angepasst "
            f"({len(replacements)} Regeln aus {args.operator_replacements})."
        )
    elif os.path.isfile(args.operator_replacements):
        log(f"Operator-Ersetzungen: {args.operator_replacements} enthält keine Regeln.")

    stations = None
    if not args.skip_trips and not args.no_stations:
        station_cache = {} if args.refresh_stations else load_station_cache(args.stations_output)
        stations = resolve_stations(statuses, token, cache=station_cache)

    # Identifier erst nach resolve_stations entfernen: liefert die API sie am
    # station-Objekt mit, spart das dort den Einzelabruf /station/{id}.
    strip_identifiers_from_statuses(statuses)

    try:
        ensure_parent_dir(args.output)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(statuses, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log(f"Fehler: {args.output} nicht schreibbar ({e}).")
        return 1
    log(f"Geschrieben: {args.output} ({len(statuses)} Statuses)")

    if stations is not None:
        try:
            ensure_parent_dir(args.stations_output)
            with open(args.stations_output, "w", encoding="utf-8") as f:
                json.dump(stations, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log(f"Fehler: {args.stations_output} nicht schreibbar ({e}).")
            return 1
        with_coords = sum(1 for s in stations.values() if s.get("latitude") is not None)
        with_ids, ident_types = _station_ident_summary(stations)
        type_bits = ", ".join(
            f"{k}={v}" for k, v in sorted(ident_types.items())
        ) or "keine"
        log(f"Geschrieben: {args.stations_output} ({len(stations)} Stationen, "
            f"{with_coords} mit Koordinaten, {with_ids} mit Identifiers: {type_bits})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Abgebrochen.")
        sys.exit(130)
