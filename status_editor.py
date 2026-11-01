#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokaler Tag-Editor: Tags und Status-Text nach Träwelling synchronisieren.

Werkzeugleiste oben (von API laden, Speichern, Dashboard bauen), Navigation
links, Inhalt rechts. Die Fahrtenseite filtert nach Zeitraum, Betreiber und
Arbeitslisten; Baureihe und Fahrzeugnummer sind direkt in der Liste
editierbar. Die Details der gewählten Fahrt stehen in einklappbaren Gruppen.
Speichern schreibt Text und Tags (auch `dubi=start` / `dubi=ende`) live nach
Träwelling; der Laufweg (`trip`) bleibt unangetastet. Einstieg, Kanten und
Linienfarbe sind lokale Overlays. Je Konfig-Datei gibt es eine
Einstellungsseite (`editor_settings.py`), die sofort lokal speichert.

Auth wie der Export, aber mit Scope `write-statuses` zusätzlich zu
`read-statuses`.

    python3 status_editor.py
    python3 status_editor.py --login
    python3 walita.py --edit
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import colorchooser, font as tkfont
from tkinter import messagebox, ttk
import urllib.parse

import auth
import boarding_patches as bp
import build_dashboard
import download_statuses as dl
import edge_patches as ep
import editor_settings as es
import line_color_patches as lcp
import line_patches as lp
import vehicle_roster as vr
import station_patches as sp
from version import __version__

EDITOR_SCOPES = "read-statuses write-statuses"
BODY_MAX = 280

KEY_LOC = "trwl:locomotive_class"
KEY_VEH = "trwl:vehicle_number"
TABLE_TAG_KEYS = (KEY_LOC, KEY_VEH)
TABLE_TAG_SET = frozenset(TABLE_TAG_KEYS)
KEY_LINE = lp.KEY_LINE
KEY_OPERATOR = lp.KEY_OPERATOR
# Tags mit eigener Bedienung; erscheinen nicht in der freien Tag-Liste.
MANAGED_TAG_SET = TABLE_TAG_SET | {KEY_LINE, KEY_OPERATOR}

TRIP_COLS = ("date", "line", "route", "operator", "loc", "veh", "marks")
TRIP_HEADINGS = {
    "date": "Datum", "line": "Linie", "route": "Von → Nach",
    "operator": "Betreiber", "loc": "Baureihe", "veh": "Nummer", "marks": "◆",
}
COL_WIDTHS = {
    "date": 140, "line": 70, "route": 300, "operator": 170,
    "loc": 170, "veh": 110, "marks": 50,
}
# Mindestbreite je Seite der Fahrten-Seite (Liste | Details) in px.
TRIP_PANE_MIN = 360
EDIT_COLS = ("loc", "veh")
COL_TO_KEY = {"loc": KEY_LOC, "veh": KEY_VEH}
EDGE_KIND_LABEL = {"override": "Fahrt", "default": "Standard", "": "—"}


def _edge_patch_label(row):
    """Patch-Spalte der Kantenliste: Art und Anzahl der Via-Stationen."""
    kind = row.get("kind") or ""
    split = "aufgeteilt" if row.get("split") else ""
    if not kind:
        return split or "—"
    if kind == "override" and not row.get("via"):
        label = "Fahrt: aus"
    else:
        label = "%s (%d Via)" % (EDGE_KIND_LABEL.get(kind, kind), len(row.get("via") or []))
    return label + (", " + split if split else "")

TAG_KEY_SUGGESTIONS = (
    "trwl:seat",
    "trwl:wagon",
    "trwl:wagon_class",
    "trwl:travel_class",
    "trwl:journey_number",
    "trwl:ticket",
    "trwl:price",
    "trwl:role",
    "trwl:passenger_rights",
    "trwl:social_status",
    KEY_VEH,
    KEY_LOC,
)

VISIBILITY_CHOICES = (
    (0, "Öffentlich"),
    (1, "Ungelistet"),
    (2, "Follower"),
    (3, "Privat"),
    (4, "Angemeldet"),
    (5, "Vertrauenswürdig"),
)
VISIBILITY_LABEL = {n: label for n, label in VISIBILITY_CHOICES}
VISIBILITY_BY_LABEL = {label: n for n, label in VISIBILITY_CHOICES}


def log(msg):
    """Fortschritt auf stderr (GUI-Fehler laufen über die Statuszeile)."""
    print(msg, file=sys.stderr, flush=True)


def _enable_dpi_awareness():
    """Schärfere Darstellung unter Windows, vor dem ersten Tk()-Aufruf."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.8, 3.0, 0.1
# ttk-Stiloptionen in Pixeln, die mit dem Zoom wachsen (sofern das Theme sie hat).
ZOOM_STYLE_OPTIONS = (
    ("TCheckbutton", "indicatordiameter"),
    ("TRadiobutton", "indicatordiameter"),
    ("TCombobox", "arrowsize"),
    ("TScrollbar", "arrowsize"),
)
# Eigene Schriften (Name, Punktgröße); Größe folgt dem Zoom.
APP_FONTS = (
    (es.FONT_SECTION, 10),
    (es.FONT_TITLE, 12),
    (es.FONT_HEADING, 13),
)


def clamp_zoom(value):
    """Zoom auf ZOOM_STEP gerundet und begrenzt; ungültig → None."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value or value <= 0:
        return None
    steps = round(value / ZOOM_STEP)
    return round(min(max(steps * ZOOM_STEP, ZOOM_MIN), ZOOM_MAX), 2)


def detect_zoom():
    """Systemskalierung unter Linux (GDK_SCALE, Xft.dpi, GNOME); sonst 1.0.

    Tk zeichnet seine Standardschriften dort in festen Pixeln und übernimmt
    die Desktop-Skalierung nicht.
    """
    if sys.platform in ("win32", "darwin"):
        return 1.0
    try:
        env = float(os.environ.get("GDK_SCALE") or 1) * float(
            os.environ.get("GDK_DPI_SCALE") or 1)
    except ValueError:
        env = 1.0
    if env > 0 and abs(env - 1.0) > 0.01:
        return clamp_zoom(env) or 1.0

    def run(cmd):
        if not shutil.which(cmd[0]):
            return ""
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=2, check=False
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    m = re.search(r"^Xft\.dpi:\s*([0-9.]+)", run(["xrdb", "-query"]), re.M)
    if m and float(m.group(1)) > 0:
        return clamp_zoom(float(m.group(1)) / 96.0) or 1.0
    schema = "org.gnome.desktop.interface"
    text = run(["gsettings", "get", schema, "text-scaling-factor"]).strip()
    whole = run(["gsettings", "get", schema, "scaling-factor"]).strip()
    try:
        factor = float(text or 1)
        m = re.search(r"(\d+)$", whole)
        if m and int(m.group(1)) > 0:
            factor *= int(m.group(1))
    except ValueError:
        factor = 1.0
    return clamp_zoom(factor) or 1.0


class UiZoom:
    """Vergrößert die Oberfläche: Schriften, Zeilenhöhe und Pixelmaße.

    Benannte Tk-Schriften und die eigenen Schriften aus APP_FONTS werden
    neu skaliert. Treeview-Spaltenbreiten, Label-Umbruchbreiten und feste
    tk.Frame-Größen werden je Widget einmal auf den aktuellen Faktor
    gebracht (`refresh`), auch in später geöffneten Dialogen.
    """

    def __init__(self, root, factor=1.0):
        self.root = root
        self.factor = 1.0
        self._applied = {}
        self._fonts = {}
        for name in tkfont.names(root):
            if name.startswith("Tk"):
                font = tkfont.nametofont(name)
                self._fonts[name] = (font, int(font.cget("size")))
        family = tkfont.nametofont("TkDefaultFont").cget("family")
        default_size = self._fonts["TkDefaultFont"][1]
        for name, size in APP_FONTS + ((es.FONT_BOLD, default_size),):
            # Fester Name, damit Widgets die Schrift per String nutzen können.
            try:
                font = tkfont.Font(root=root, name=name, exists=False)
            except tk.TclError:
                font = tkfont.Font(root=root, name=name, exists=True)
            font.configure(family=family, size=size, weight="bold")
            self._fonts[name] = (font, size)
        style = ttk.Style(root)
        self._style_base = {}
        for name, opt in ZOOM_STYLE_OPTIONS:
            try:
                self._style_base[(name, opt)] = int(float(str(style.lookup(name, opt))))
            except (ValueError, tk.TclError):
                pass
        root.bind_class("Toplevel", "<Map>", self._on_toplevel_map, add="+")
        self.set(factor)

    def px(self, value):
        return int(round(value * self.factor))

    def applied(self, widget):
        return self._applied.get(str(widget), 1.0)

    def set(self, factor):
        self.factor = clamp_zoom(factor) or 1.0
        for font, size in self._fonts.values():
            scaled = max(1, int(round(abs(size) * self.factor)))
            font.configure(size=-scaled if size < 0 else scaled)
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style = ttk.Style(self.root)
        style.configure("Treeview", rowheight=max(self.px(20), line + self.px(4)))
        for (name, opt), base in self._style_base.items():
            style.configure(name, **{opt: max(1, self.px(base))})
        self.refresh()
        return self.factor

    def _on_toplevel_map(self, event):
        try:
            self.refresh(self.root.nametowidget(str(event.widget)))
        except (KeyError, tk.TclError):
            pass

    def refresh(self, top=None):
        stack = [top or self.root]
        while stack:
            widget = stack.pop()
            try:
                stack.extend(widget.winfo_children())
                self._scale_widget(widget)
            except tk.TclError:
                continue

    def _scale_widget(self, widget):
        key = str(widget)
        ratio = self.factor / self._applied.get(key, 1.0)
        if abs(ratio - 1.0) < 1e-9:
            return
        cls = widget.winfo_class()
        if cls == "Treeview":
            for col in ("#0",) + tuple(widget["columns"] or ()):
                width = int(widget.column(col, "width"))
                widget.column(col, width=max(1, int(round(width * ratio))))
        elif cls in ("TLabel", "Label", "Message"):
            wrap = int(float(str(widget.cget("wraplength") or 0)))
            if wrap > 0:
                widget.configure(wraplength=int(round(wrap * ratio)))
        elif cls == "Frame" and not isinstance(widget, ttk.Frame):
            for opt in ("width", "height"):
                size = int(float(str(widget.cget(opt) or 0)))
                if size > 0:
                    widget.configure(**{opt: int(round(size * ratio))})
        self._applied[key] = self.factor


def format_api_error(exc):
    """Macht ApiError-Bodies (Laravel-JSON) in der GUI lesbar."""
    if not isinstance(exc, dl.ApiError):
        return str(exc)
    text = exc.body or ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        parts = []
        msg = parsed.get("message") or parsed.get("error")
        if isinstance(msg, str) and msg:
            parts.append(msg)
        elif isinstance(msg, dict):
            parts.append(json.dumps(msg, ensure_ascii=False))
        errors = parsed.get("errors") or parsed.get("data")
        if isinstance(errors, dict):
            for key, val in errors.items():
                if isinstance(val, (list, tuple)):
                    val = "; ".join(str(v) for v in val)
                parts.append(f"{key}: {val}")
        elif isinstance(errors, str):
            parts.append(errors)
        if parts:
            prefix = f"HTTP {exc.code}: " if exc.code else ""
            return prefix + " | ".join(parts)
    return str(exc)


def _station_name(stop):
    if not isinstance(stop, dict):
        return "?"
    name = stop.get("name")
    if name:
        return name
    station = stop.get("station") or {}
    return station.get("name") or "?"


def _checkin_bits(status):
    checkin = status.get("checkin") or {}
    origin = checkin.get("origin") or {}
    dest = checkin.get("destination") or {}
    line = checkin.get("lineName") or checkin.get("number") or "?"
    dep = origin.get("departure") or origin.get("departurePlanned") or ""
    arr = dest.get("arrival") or dest.get("arrivalPlanned") or ""
    return {
        "line": line,
        "origin": _station_name(origin),
        "dest": _station_name(dest),
        "dep": dep,
        "arr": arr,
        "date": dl.status_date(status) or "",
    }


def _local_dt(ts):
    """ISO-Zeitstempel als datetime in lokaler Zeit, sonst None.

    Ohne Zone gilt der Zeitstempel als lokal.
    """
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone() if dt.tzinfo is not None else dt


def _fmt_when(ts):
    """ISO-Zeit kompakt als 'YYYY-MM-DD HH:MM' in lokaler Zeit."""
    dt = _local_dt(ts)
    if dt is None:
        return ts or "—"
    return dt.strftime("%Y-%m-%d %H:%M")


def _boarding_label(stop, api_so):
    """Eine Zeile im Einstiegs-Dialog: Zeit, Name, Markierung des API-Halts."""
    dep = stop.get("departureReal") or stop.get("departurePlanned") or ""
    name = _station_name(stop)
    mark = ""
    if bp.stopover_id(stop.get("stopoverId")) == api_so:
        mark = "  (Träwelling)"
    return f"{_fmt_when(dep)}  {name}{mark}"


def _trip_short(status):
    """Eine Zeile für die Übertragungsliste."""
    bits = _checkin_bits(status)
    return f"{bits['date']}  {bits['line']}  {bits['origin']} → {bits['dest']}"


def _norm_tag(tag):
    vis = tag.get("visibility")
    try:
        vis = int(vis)
    except (TypeError, ValueError):
        vis = 0
    return {
        "key": (tag.get("key") or "").strip(),
        "value": "" if tag.get("value") is None else str(tag.get("value")),
        "visibility": vis,
    }


def _tags_tuple(tags):
    return tuple(
        (t["key"], t["value"], t["visibility"])
        for t in (_norm_tag(t) for t in (tags or []))
        if t["key"]
    )


def _tag_value(status, key):
    for t in status.get("tags") or []:
        if (t.get("key") or "").strip() == key:
            return "" if t.get("value") is None else str(t.get("value"))
    return ""


def _dubi_kind(tag):
    """``start``, ``ende`` oder None. Dieselben Regeln wie ``dubi_flags``."""
    start, ende = build_dashboard.dubi_flags([tag])
    if start:
        return "start"
    if ende:
        return "ende"
    return None


def _new_dubi_tag(kind):
    """Kanonisches Tag für eine neu gesetzte Checkbox."""
    return {"key": "dubi=" + kind, "value": kind, "visibility": 0}


def apply_dubi_checks(tags, start_on, ende_on, original=None):
    """Passt dubi-Tags an die Checkboxen an.

    Bleibt eine Checkbox an, bleiben vorhandene Tags stehen — auch
    ``dubi`` mit Wert ``start``/``ende``. Ein wieder eingeschaltetes Flag
    setzt die Tags aus ``original`` zurück, sonst ``dubi=start`` bzw.
    ``dubi=ende``. Ausgeschaltete Flags verschwinden.
    """
    kept = []
    have = set()
    for t in tags or []:
        kind = _dubi_kind(t)
        if kind == "start" and not start_on:
            continue
        if kind == "ende" and not ende_on:
            continue
        kept.append(t)
        if kind:
            have.add(kind)
    for kind, on in (("start", start_on), ("ende", ende_on)):
        if not on or kind in have:
            continue
        restored = [
            _norm_tag(t) for t in (original or []) if _dubi_kind(t) == kind
        ]
        if restored:
            kept.extend(restored)
        else:
            kept.append(_new_dubi_tag(kind))
    return kept


def _order_like_original(tags, original):
    """Schlüssel wie in ``original``, danach neue Schlüssel in ``tags``-Folge."""
    by_key = {}
    seq = []
    for t in tags or []:
        n = _norm_tag(t)
        if not n["key"] or n["key"] in by_key:
            continue
        by_key[n["key"]] = n
        seq.append(n["key"])
    merged = []
    used = set()
    for t in original or []:
        key = _norm_tag(t)["key"]
        if key in by_key and key not in used:
            merged.append(by_key[key])
            used.add(key)
    for key in seq:
        if key not in used:
            merged.append(by_key[key])
            used.add(key)
    return merged


def set_table_tag(status, key, value):
    """Setzt oder entfernt einen Tabellen-Tag (leerer Wert = löschen)."""
    value = (value or "").strip()
    tags = [_norm_tag(t) for t in (status.get("tags") or []) if _norm_tag(t)["key"]]
    idx = next((i for i, t in enumerate(tags) if t["key"] == key), None)
    if not value:
        if idx is not None:
            tags.pop(idx)
    elif idx is not None:
        tags[idx]["value"] = value
    else:
        tags.append({"key": key, "value": value, "visibility": 0})
    status["tags"] = tags


def apply_line_reconcile(patches, statuses, result, choices):
    """Wendet einen Abgleich an: übernehmen, vormerken, Konflikte nach Wahl.

    Gibt (Einträge in der Datei, vorgemerkte Tags) zurück.
    """
    by_id = {s.get("id"): s for s in statuses if isinstance(s, dict)}
    n_file = n_tag = 0
    for sid, tag in sorted(result["adopt"].items(), key=lambda kv: str(kv[0])):
        lp.set_override(patches, sid, tag)
        n_file += 1
    for sid, line in sorted(result["stage"].items(), key=lambda kv: str(kv[0])):
        if sid in by_id:
            set_table_tag(by_id[sid], KEY_LINE, line)
            n_tag += 1
    for sid in result.get("uncleared") or ():
        patches.get("cleared", set()).discard(sid)
        n_file += 1
    for sid, _hafas, local, tag in result["conflicts"]:
        choice = (choices or {}).get(sid, "later")
        if choice == "remote":
            lp.set_override(patches, sid, tag)
            n_file += 1
        elif choice == "local" and sid in by_id:
            set_table_tag(by_id[sid], KEY_LINE, local)
            n_tag += 1
    return n_file, n_tag


def _snapshot_status(status):
    return (status.get("body") or "", _tags_tuple(status.get("tags") or []))


def _search_blob(status):
    bits = _checkin_bits(status)
    parts = [
        bits["date"], bits["line"], bits["origin"], bits["dest"],
        _tag_value(status, KEY_LOC), _tag_value(status, KEY_VEH),
        status.get("body") or "",
    ]
    for t in status.get("tags") or []:
        parts.append(t.get("key") or "")
        parts.append("" if t.get("value") is None else str(t.get("value")))
    return " ".join(parts).lower()


NO_OPERATOR_LABEL = "(ohne Betreiber)"
MONTH_NAMES = (
    "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
    "August", "September", "Oktober", "November", "Dezember",
)


def operator_of(status):
    """Betreiber aus walita:operator, sonst aus dem Check-in."""
    return lp.line_and_operator(status or {})[1]


def has_tag(status, key):
    return bool(_tag_value(status, key).strip())


def status_day(status):
    """Abfahrtstag in lokaler Zeit oder None."""
    checkin = (status or {}).get("checkin") or {}
    origin = checkin.get("origin") or {}
    for ts in (origin.get("departure"), origin.get("departurePlanned"),
               (status or {}).get("createdAt")):
        if not isinstance(ts, str):
            continue
        try:
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.date()
    return None


def period_options(statuses, today):
    opts = [("all", "Alle"), ("7d", "Letzte 7 Tage"), ("30d", "Letzte 30 Tage")]
    months = sorted(
        {(d.year, d.month) for d in map(status_day, statuses) if d}, reverse=True
    )
    seen_years = set()
    for year, month in months:
        if year not in seen_years:
            seen_years.add(year)
            opts.append(("y:%d" % year, str(year)))
        opts.append(("m:%04d-%02d" % (year, month), "%s %d" % (MONTH_NAMES[month - 1], year)))
    return opts


def in_period(status, key, today):
    if key == "all":
        return True
    day = status_day(status)
    if day is None:
        return False
    if key == "7d":
        return today - datetime.timedelta(days=7) < day <= today
    if key == "30d":
        return today - datetime.timedelta(days=30) < day <= today
    if key.startswith("y:"):
        return str(day.year) == key[2:]
    if key.startswith("m:"):
        return "%04d-%02d" % (day.year, day.month) == key[2:]
    return True


WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def weekday_label(ts):
    """Kurzer Wochentag eines ISO-Zeitstempels in lokaler Zeit oder ""."""
    dt = _local_dt(ts)
    return WEEKDAYS[dt.weekday()] if dt else ""


def split_vehicle_numbers(text):
    return [n.strip() for n in re.split(r"[,;+]", text or "") if n.strip()]


def roster_hint(roster, loc_class, number_text):
    entries = ((roster or {}).get("types") or {}).get((loc_class or "").strip())
    numbers = split_vehicle_numbers(number_text)
    if not entries or not numbers:
        return ""
    by_number = {e.get("number"): e for e in entries}
    missing = [n for n in numbers if n not in by_number]
    withdrawn = [
        n for n in numbers if n in by_number and by_number[n].get("withdrawn")
    ]
    parts = []
    if missing:
        parts.append("⚠ nicht im Fuhrpark: " + ", ".join(missing))
    if withdrawn:
        parts.append("ausgemustert: " + ", ".join(withdrawn))
    return " · ".join(parts) or "✓ im Fuhrpark"


def patch_markers(status, boarding, colors, edges, lines=None):
    sid = (status or {}).get("id")
    out = ""
    if sid in ((boarding or {}).get("overrides") or {}):
        out += "E"
    if sid in ((colors or {}).get("overrides") or {}):
        out += "F"
    if lines is not None and lp.effective_line(status, lines)[2]:
        out += "L"
    if lp.tag_operator(status):
        out += "B"
    shown = bp.preview_status(status, boarding)
    for a, b in ep.consecutive_pairs(dl.traveled_stopovers(shown)):
        if ep.patch_kind(edges, sid, a.get("id"), b.get("id")):
            out += "K"
            break
    start, ende = build_dashboard.dubi_flags(status.get("tags") or [])
    if start or ende:
        out += "D"
    return out


def load_local_statuses(path):
    """Liest statuses.json oder gibt None zurück (fehlt/unlesbar)."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log(f"Lokale Statuses {path} nicht lesbar ({e}).")
        return None
    if not isinstance(data, list):
        log(f"Lokale Statuses {path} sind kein JSON-Array.")
        return None
    return data


DEFAULT_UI_STATE = {
    "sort": [["date", True]],
    "widths": {},
    "sash": None,
    "collapsed": [],
    "page": "trips:all",
    "zoom": None,
}


def load_ui_state(path):
    """Oberflächenzustand; fehlende oder ungültige Teile fallen auf Defaults."""
    state = json.loads(json.dumps(DEFAULT_UI_STATE))
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return state
    if not isinstance(raw, dict):
        return state
    sort = [
        [c, bool(d)] for c, d in (
            item for item in raw.get("sort") or []
            if isinstance(item, list) and len(item) == 2
        )
        if c in TRIP_COLS
    ]
    if sort:
        state["sort"] = sort
    widths = raw.get("widths")
    if isinstance(widths, dict):
        state["widths"] = {
            c: int(w) for c, w in widths.items()
            if c in TRIP_COLS and isinstance(w, int) and w > 0
        }
    if isinstance(raw.get("sash"), int):
        state["sash"] = raw["sash"]
    if isinstance(raw.get("collapsed"), list):
        state["collapsed"] = [x for x in raw["collapsed"] if isinstance(x, str)]
    if isinstance(raw.get("page"), str):
        state["page"] = raw["page"]
    if not isinstance(raw.get("zoom"), bool):
        state["zoom"] = clamp_zoom(raw.get("zoom"))
    if isinstance(raw.get("reload_since"), str) and parse_day(raw["reload_since"]):
        state["reload_since"] = raw["reload_since"]
    return state


def parse_day(text):
    """'YYYY-MM-DD' → date, sonst None."""
    try:
        return datetime.datetime.strptime((text or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def merge_since(fetched, existing, since):
    """Geladene Statuses nach `since` plus die lokalen bis einschließlich `since`.

    Lokale Statuses nach `since`, die die API nicht mehr liefert, gelten als
    gelöscht. Ohne ermittelbares Datum bleiben lokale Statuses erhalten.
    """
    ids = {s.get("id") for s in fetched}
    kept = [
        s for s in existing
        if s.get("id") not in ids and (dl.status_date(s) or "") <= since
    ]
    return list(fetched) + kept


def save_ui_state(path, state):
    """Schreibt den Oberflächenzustand atomar. Gibt True bei Erfolg."""
    parent = os.path.dirname(path)
    tmp = path + ".tmp"
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


def merge_local_body_tags_many(path, updates):
    """Schreibt body/tags mehrerer IDs in statuses.json; trip bleibt.

    `updates` ist {status_id: (body, tags)}. Gibt die Zahl der Treffer zurück.
    Fehlt die Datei, ist das ein No-Op (0).
    """
    if not path or not os.path.isfile(path) or not updates:
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise OSError(f"{path} nicht lesbar: {e}") from e
    if not isinstance(data, list):
        raise OSError(f"{path} enthält kein JSON-Array.")
    n = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        if sid in updates:
            body, tags = updates[sid]
            item["body"] = body
            item["tags"] = tags
            n += 1
    if not n:
        return 0
    dl.ensure_parent_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return n


def attach_local_trips(fetched, existing, trip_cache):
    """Hängt geladenen Statuses ihre Halte an (in place).

    Die Statusliste der API trägt keine Stopovers. Quelle ist der Trip-Cache
    (`trip_id -> (stopovers, err)`), sonst `trip`/`trip_error` des bisherigen
    Status mit derselben ID. Gibt `fetched` zurück.
    """
    old = {s.get("id"): s for s in existing if isinstance(s, dict)}
    for status in fetched:
        if not isinstance(status, dict) or "trip" in status:
            continue
        tid = (status.get("checkin") or {}).get("trip")
        try:
            stopovers = (trip_cache.get(int(tid)) or (None, None))[0]
        except (TypeError, ValueError):
            stopovers = None
        prev = old.get(status.get("id")) or {}
        if stopovers:
            status["trip"] = {"stopovers": stopovers}
        elif "trip" in prev:
            status["trip"] = prev["trip"]
            if prev.get("trip_error"):
                status["trip_error"] = prev["trip_error"]
    return fetched


def _write_json_atomic(path, data, trailing_newline=True):
    dl.ensure_parent_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        if trailing_newline:
            f.write("\n")
    os.replace(tmp, path)


def refresh_trips(token, trip_ids, statuses_path, trips_path, stations_path):
    """Lädt die Stopovers der Trips neu und überschreibt sie lokal.

    Hausmeister-Aktion: kein Abgleich mit vorhandenen Daten, der neue Stand
    ersetzt den alten in `trips.json` und in `status["trip"]` von
    `statuses.json`. Neue Stationen landen in `stations.json`.
    `trip_ids` ist {status_id: trip_id}. Gibt ({status_id: stopovers},
    {status_id: fehler}) zurück; fehlgeschlagene Trips bleiben unverändert.
    """
    cache = {}
    dl.fetch_stopovers(sorted(set(trip_ids.values()), key=str), token, cache)
    fresh, errors = {}, {}
    for sid, tid in trip_ids.items():
        try:
            stopovers, err = cache.get(int(tid), (None, "keine Trip-ID"))
        except (TypeError, ValueError):
            stopovers, err = None, "keine Trip-ID"
        if stopovers:
            fresh[sid] = stopovers
        else:
            errors[sid] = err
    if not fresh:
        return fresh, errors

    station_cache = dl.load_station_cache(stations_path)
    needed = {st.get("id") for sts in fresh.values() for st in sts}
    resolved = dl.resolve_stations(
        [{"trip": {"stopovers": sts}} for sts in fresh.values()], token,
        cache={k: v for k, v in station_cache.items() if k in needed},
    )
    if any(station_cache.get(k) != v for k, v in resolved.items()):
        station_cache.update(resolved)
        _write_json_atomic(stations_path, station_cache, trailing_newline=False)

    fresh = {sid: [dl._without_identifiers(st) for st in sts]
             for sid, sts in fresh.items()}
    trip_cache = dl.load_trip_cache(trips_path, statuses_path=statuses_path)
    for sid, sts in fresh.items():
        trip_cache[int(trip_ids[sid])] = (sts, None)
    dl.save_trip_cache(trips_path, trip_cache)

    if os.path.isfile(statuses_path):
        with open(statuses_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        hits = 0
        for item in data if isinstance(data, list) else ():
            if isinstance(item, dict) and item.get("id") in fresh:
                item["trip"] = {"stopovers": fresh[item["id"]]}
                item.pop("trip_error", None)
                hits += 1
        if hits:
            _write_json_atomic(statuses_path, data)
    return fresh, errors


def resync_statuses(token, status_ids, statuses_path, trips_path, stations_path,
                    operator_replacements_path):
    """Lädt einzelne Statuses per GET /status/{id} und ersetzt sie in statuses.json.

    Für Änderungen in Träwelling (Ausstieg, Einstieg, Linie …) außerhalb des
    inkrementellen Fensters. Operator-Ersetzungen gelten wie beim Export.
    `trip` bleibt bei gleicher Trip-ID; sonst kommt er aus trips.json oder
    wird per `refresh_trips` neu abgerufen. Fehlt ein Status in der Datei,
    wird er nach Abfahrt einsortiert. Gibt ({status_id: status},
    {status_id: fehler}) zurück.
    """
    replacements = dl.load_operator_replacements(operator_replacements_path)
    fresh, errors = {}, {}
    for sid in status_ids:
        try:
            fresh[sid] = fetch_status(token, sid)
        except dl.ApiError as e:
            errors[sid] = str(e)
    if not fresh:
        return fresh, errors
    dl.apply_operator_replacements(list(fresh.values()), replacements)
    dl.strip_identifiers_from_statuses(list(fresh.values()))

    data = []
    if os.path.isfile(statuses_path):
        with open(statuses_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise OSError(f"{statuses_path} enthält kein JSON-Array.")
    old = {s.get("id"): s for s in data if isinstance(s, dict)}
    trip_cache = None
    missing = {}
    for sid, status in fresh.items():
        tid = (status.get("checkin") or {}).get("trip")
        prev = old.get(sid) or {}
        if "trip" in prev and (prev.get("checkin") or {}).get("trip") == tid:
            status["trip"] = prev["trip"]
            if prev.get("trip_error"):
                status["trip_error"] = prev["trip_error"]
            continue
        if trip_cache is None:
            trip_cache = dl.load_trip_cache(trips_path, statuses_path=statuses_path)
        try:
            stopovers = (trip_cache.get(int(tid)) or (None, None))[0]
        except (TypeError, ValueError):
            stopovers = None
        if stopovers:
            status["trip"] = {"stopovers": stopovers}
        elif tid not in (None, ""):
            missing[sid] = tid

    replaced = set()
    for i, item in enumerate(data):
        if isinstance(item, dict) and item.get("id") in fresh:
            data[i] = fresh[item["id"]]
            replaced.add(item["id"])
    added = [s for sid, s in fresh.items() if sid not in replaced]
    if added:
        far = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        data = sorted(data + added,
                      key=lambda s: dl.status_sort_time(s) or far, reverse=True)
    _write_json_atomic(statuses_path, data)

    if missing:
        trips, trip_errors = refresh_trips(token, missing, statuses_path,
                                           trips_path, stations_path)
        for sid, sts in trips.items():
            fresh[sid]["trip"] = {"stopovers": sts}
        for sid, err in trip_errors.items():
            errors[sid] = f"Trip: {err}"
    return fresh, errors


def fetch_station_into_file(token, station_id, stations_path):
    """GET /station/{id} und Eintrag in stations.json. Gibt den Eintrag oder None."""
    station_cache = dl.load_station_cache(stations_path)
    resolved = dl.resolve_stations(
        [{"trip": {"stopovers": [{"id": station_id}]}}], token, cache={})
    entry = resolved.get(station_id)
    if not entry or entry.get("latitude") is None:
        return None
    station_cache[station_id] = entry
    _write_json_atomic(stations_path, station_cache, trailing_newline=False)
    return entry


def fetch_status(token, status_id):
    """GET /status/{id} → StatusResource (das `data`-Objekt)."""
    payload = dl.api_request("GET", f"/status/{int(status_id)}", token)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise dl.ApiError(None, f"Unerwartete Antwort für Status {status_id}")
    return data


def tag_path(status_id, key):
    encoded = urllib.parse.quote(str(key), safe="")
    return f"/status/{int(status_id)}/tags/{encoded}"


def apply_status_edits(token, status_id, new_body, new_tags):
    """Live: body per PUT, Tags per Diff (POST/PUT/DELETE). Gibt den Server-Status zurück."""
    current = fetch_status(token, status_id)
    old_body = current.get("body") or ""
    if (new_body or "") != (old_body or ""):
        payload = dl.api_request(
            "PUT", f"/status/{int(status_id)}", token,
            json_body={"body": new_body if new_body else None},
        )
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            current = payload["data"]

    server = {
        t["key"]: t
        for t in (_norm_tag(t) for t in (current.get("tags") or []))
        if t["key"]
    }
    form = {
        t["key"]: t
        for t in (_norm_tag(t) for t in new_tags)
        if t["key"]
    }

    for key in list(server):
        if key not in form:
            dl.api_request("DELETE", tag_path(status_id, key), token)
    for key, tag in form.items():
        body = {
            "key": tag["key"],
            "value": tag["value"],
            "visibility": tag["visibility"],
        }
        if key not in server:
            dl.api_request("POST", f"/status/{int(status_id)}/tags", token, json_body=body)
        elif (
            server[key]["value"] != tag["value"]
            or server[key]["visibility"] != tag["visibility"]
        ):
            dl.api_request("PUT", tag_path(status_id, key), token, json_body=body)

    return fetch_status(token, status_id)


class TransferWindow(tk.Toplevel):
    """Fenster, das den Server-Sync Fahrt für Fahrt anzeigt."""

    _STATE_LABEL = {
        "waiting": "Wartend",
        "running": "Übertrage …",
        "ok": "Gespeichert",
        "error": "Fehler",
        "local": "Lokal",
    }

    def __init__(self, master, items):
        super().__init__(master)
        self.title("Übertragung nach Träwelling")
        self.transient(master)
        self.resizable(True, True)
        self.minsize(560, 280)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._done = False
        self._iids = {}
        self.total = len(items)
        self.finished = 0

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self.summary = tk.StringVar(value=f"0/{self.total} übertragen")
        ttk.Label(frm, textvariable=self.summary).pack(anchor="w")
        self.bar = ttk.Progressbar(
            frm, mode="determinate", maximum=max(self.total, 1), value=0
        )
        self.bar.pack(fill="x", pady=(4, 8))

        tree_fr = ttk.Frame(frm)
        tree_fr.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_fr, columns=("fahrt", "status", "detail"),
            show="headings", selectmode="browse",
        )
        self.tree.heading("fahrt", text="Fahrt")
        self.tree.heading("status", text="Status")
        self.tree.heading("detail", text="Meldung")
        self.tree.column("fahrt", width=320, stretch=True)
        self.tree.column("status", width=110, stretch=False)
        self.tree.column("detail", width=240, stretch=True)
        scroll = ttk.Scrollbar(tree_fr, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("ok", foreground="#1a7f37")
        self.tree.tag_configure("error", foreground="#d1242f")
        self.tree.tag_configure("running", foreground="#0969da")

        for sid, label in items:
            iid = str(sid)
            self._iids[sid] = iid
            self.tree.insert(
                "", "end", iid=iid,
                values=(label, self._STATE_LABEL["waiting"], ""),
            )

        btn = ttk.Frame(frm)
        btn.pack(fill="x", pady=(8, 0))
        self.close_btn = ttk.Button(
            btn, text="Schließen", command=self.destroy, state="disabled"
        )
        self.close_btn.pack(side="right")

    def set_row(self, sid, state, detail=""):
        iid = self._iids.get(sid)
        if iid is None or not self.tree.exists(iid):
            return
        vals = list(self.tree.item(iid, "values"))
        vals[1] = self._STATE_LABEL.get(state, state)
        vals[2] = detail or ""
        self.tree.item(iid, values=vals, tags=(state,) if state in ("ok", "error", "running") else ())
        self.tree.see(iid)

    def bump(self):
        self.finished += 1
        self.bar["value"] = self.finished
        self.summary.set(f"{self.finished}/{self.total} übertragen")

    def finish(self, text):
        self._done = True
        self.bar["value"] = self.total
        self.summary.set(text)
        self.close_btn.configure(state="normal")
        self.close_btn.focus_set()

    def _on_close(self):
        if self._done:
            self.destroy()


class TagDialog(tk.Toplevel):
    """Dialog zum Anlegen oder Bearbeiten eines sonstigen Tags."""

    def __init__(self, master, title, extra_keys=None, initial=None):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        initial = initial or {}

        keys = [
            k for k in TAG_KEY_SUGGESTIONS
            if k not in MANAGED_TAG_SET and _dubi_kind({"key": k, "value": ""}) is None
        ]
        for k in extra_keys or ():
            if (
                k and k not in keys and k not in MANAGED_TAG_SET
                and _dubi_kind({"key": k, "value": ""}) is None
            ):
                keys.append(k)
        start_key = initial.get("key") or ""
        if start_key and start_key not in keys:
            keys.insert(0, start_key)

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")

        ttk.Label(frm, text="Schlüssel").grid(row=0, column=0, sticky="w")
        self.key_var = tk.StringVar(value=start_key)
        self.key_box = ttk.Combobox(frm, textvariable=self.key_var, values=keys, width=36)
        self.key_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(frm, text="Wert").grid(row=2, column=0, sticky="w")
        self.val_var = tk.StringVar(value=initial.get("value") or "")
        ttk.Entry(frm, textvariable=self.val_var, width=36).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8)
        )

        ttk.Label(frm, text="Sichtbarkeit").grid(row=4, column=0, sticky="w")
        vis = initial.get("visibility", 0)
        try:
            vis = int(vis)
        except (TypeError, ValueError):
            vis = 0
        self.vis_var = tk.StringVar(value=VISIBILITY_LABEL.get(vis, VISIBILITY_LABEL[0]))
        ttk.Combobox(
            frm, textvariable=self.vis_var, values=[l for _, l in VISIBILITY_CHOICES],
            state="readonly", width=34,
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=2, sticky="e")
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.key_box.focus_set()
        self.wait_window(self)

    def _ok(self):
        key = self.key_var.get().strip()
        if not key:
            messagebox.showerror("Tag", "Schlüssel darf nicht leer sein.", parent=self)
            return
        if key in MANAGED_TAG_SET:
            messagebox.showerror(
                "Tag",
                "Baureihe und Fahrzeugnummer in der Tabelle links, "
                "die Linie im Bereich Darstellung bearbeiten.",
                parent=self,
            )
            return
        if _dubi_kind({"key": key, "value": self.val_var.get()}):
            messagebox.showerror(
                "Tag",
                "dubi start und dubi ende über die Checkboxen setzen.",
                parent=self,
            )
            return
        vis = VISIBILITY_BY_LABEL.get(self.vis_var.get(), 0)
        self.result = {"key": key, "value": self.val_var.get(), "visibility": vis}
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class LineConflictDialog(tk.Toplevel):
    """Konflikte Datei ↔ walita:line; result = {sid: local|remote|later} oder None."""

    CHOICES = (("local", "Lokal behalten"), ("remote", "Träwelling übernehmen"),
               ("later", "Später"))

    def __init__(self, master, conflicts, dates=None):
        super().__init__(master)
        self.title("Linie: lokal und Träwelling verschieden")
        self.transient(master)
        self.result = None
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Für diese Fahrten weicht der Tag walita:line von der "
                              "lokalen Linie ab.", wraplength=620).pack(anchor="w")
        cols = ("date", "hafas", "local", "remote", "choice")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=12)
        for c, text, w in (("date", "Datum", 110), ("hafas", "HAFAS", 90),
                           ("local", "Lokal", 110), ("remote", "Träwelling", 110),
                           ("choice", "Aktion", 170)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, pady=8)
        self.choice = {}
        for sid, hafas, local, remote in conflicts:
            self.choice[sid] = "later"
            self.tree.insert("", "end", iid=str(sid), values=(
                (dates or {}).get(sid, ""), hafas, local, remote, "Später"))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        for key, label in self.CHOICES:
            ttk.Button(row, text=label + " (Auswahl)",
                       command=lambda k=key: self._set(k, False)).pack(side="left", padx=(0, 6))
        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=(6, 0))
        for key, label in self.CHOICES:
            ttk.Button(row2, text=label + " (alle)",
                       command=lambda k=key: self._set(k, True)).pack(side="left", padx=(0, 6))
        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=6)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window(self)

    def _set(self, key, all_rows):
        label = dict(self.CHOICES)[key]
        iids = self.tree.get_children() if all_rows else self.tree.selection()
        by_iid = {str(sid): sid for sid in self.choice}
        for iid in iids:
            self.choice[by_iid[iid]] = key
            self.tree.set(iid, "choice", label)

    def _ok(self):
        self.result = dict(self.choice)
        self.destroy()


class LineColorDialog(tk.Toplevel):
    """Hex-Eingabe plus nativer Farbwähler; Vorschau als Linien-Badge."""

    _EMPTY = "#cbd5e1"

    def __init__(self, master, line_name, initial_bg=None):
        super().__init__(master)
        self.title("Linienfarbe")
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        self._line_name = line_name or "Linie"
        start = lcp.normalize_hex(initial_bg)
        self._hex_var = tk.StringVar(value=("#" + start) if start else "")

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Hintergrund (Hex)").pack(anchor="w")
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(0, 8))
        self._entry = ttk.Entry(row, textvariable=self._hex_var, width=14)
        self._entry.pack(side="left")
        ttk.Button(row, text="Farbwähler…", command=self._pick).pack(
            side="left", padx=(8, 0)
        )

        self._preview = tk.Label(
            frm, text=self._line_name, padx=10, pady=4,
            relief="flat", font=tkfont.nametofont("TkDefaultFont"),
        )
        self._preview.pack(fill="x", pady=(0, 8))
        ttk.Label(
            frm, text="Textfarbe wird automatisch gesetzt. Nur lokal, kein Upload.",
        ).pack(anchor="w")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")

        if hasattr(self._hex_var, "trace_add"):
            self._hex_var.trace_add("write", lambda *_: self._refresh_preview())
        else:
            self._hex_var.trace("w", lambda *_: self._refresh_preview())
        self._refresh_preview()

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self._entry.focus_set()
        self._entry.selection_range(0, "end")
        self.wait_window(self)

    def _refresh_preview(self):
        pair = lcp.to_css_pair(self._hex_var.get())
        if not pair:
            self._preview.configure(
                bg=self._EMPTY, fg="#334155", text=self._line_name,
            )
            return
        self._preview.configure(bg=pair[0], fg=pair[1], text=self._line_name)

    def _pick(self):
        current = lcp.normalize_hex(self._hex_var.get())
        initial = "#" + current if current else None
        try:
            _rgb, hx = colorchooser.askcolor(
                color=initial, parent=self, title="Linienfarbe",
            )
        except tk.TclError:
            return
        if hx:
            self._hex_var.set(hx)

    def _ok(self):
        bg = lcp.normalize_hex(self._hex_var.get())
        if not bg:
            messagebox.showerror(
                "Linienfarbe",
                "Bitte eine 6-stellige Hex-Farbe angeben (z.B. #0066ad).",
                parent=self,
            )
            return
        self.result = bg
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class BoardingDialog(tk.Toplevel):
    """Halt vor dem Ausstieg wählen. result = stopoverId oder None."""

    def __init__(self, master, status, selected_so):
        super().__init__(master)
        self.title("Einstieg")
        self.transient(master)
        self.result = None
        self.minsize(420, 360)
        candidates = bp.candidate_stopovers(status)
        api_so = bp.stopover_id(
            ((status.get("checkin") or {}).get("origin") or {}).get("stopoverId")
        )
        self._ids = []

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm,
            text="Neuer Einstieg auf dieser Fahrt, vor dem Ausstieg. "
                 "Nur lokal, kein Upload.",
            wraplength=400,
        ).pack(anchor="w")

        wrap = ttk.Frame(frm)
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        self.list = tk.Listbox(wrap, height=12, activestyle="dotbox")
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        self.list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        select_at = 0
        wanted = bp.stopover_id(selected_so)
        for i, stop in enumerate(candidates):
            so = bp.stopover_id(stop.get("stopoverId"))
            self._ids.append(so)
            self.list.insert("end", _boarding_label(stop, api_so))
            if so == wanted:
                select_at = i
        if self._ids:
            self.list.selection_set(select_at)
            self.list.activate(select_at)
            self.list.see(select_at)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")

        self.list.bind("<Double-1>", lambda _e: self._ok())
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.list.focus_set()
        self.wait_window(self)

    def _ok(self):
        sel = self.list.curselection()
        if not sel:
            return
        self.result = self._ids[sel[0]]
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class Section(ttk.Frame):
    """Einklappbare Gruppe mit Kopfzeile ▾/▸; Zustand in der Liste `collapsed`."""

    def __init__(self, parent, key, title, collapsed, local=False):
        super().__init__(parent)
        self.key = key
        self._collapsed = collapsed
        head = ttk.Frame(self)
        head.pack(fill="x", pady=(10, 2))
        self._arrow = tk.StringVar()
        arrow = ttk.Label(head, textvariable=self._arrow, cursor="hand2")
        arrow.pack(side="left")
        label = ttk.Label(
            head, text=title, font=es.FONT_SECTION, cursor="hand2"
        )
        label.pack(side="left", padx=(4, 0))
        if local:
            ttk.Label(head, text="lokal", foreground="#666666").pack(
                side="left", padx=(8, 0)
            )
        for w in (arrow, label):
            w.bind("<Button-1>", lambda _e: self.toggle())
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        self.body = ttk.Frame(self, padding=(14, 4, 0, 0))
        self._render()

    def _render(self):
        closed = self.key in self._collapsed
        self._arrow.set("▸" if closed else "▾")
        if closed:
            self.body.pack_forget()
        else:
            self.body.pack(fill="x")

    def toggle(self):
        if self.key in self._collapsed:
            self._collapsed.remove(self.key)
        else:
            self._collapsed.append(self.key)
        self._render()


class EditorApp:
    def __init__(self, root, token, username, statuses, statuses_path, limit, since,
                 stations_path="data/stations.json", dashboard_path="data/dashboard.html",
                 loc_class_families="data/loc_class_families.txt", ignore_plus=False,
                 edge_patches_path="data/edge_patches.json",
                 station_patches_path="data/station_patches.json",
                 line_color_patches_path="data/line_color_patches.json",
                 home_region_path="data/home_region.json",
                 boarding_patches_path="data/boarding_patches.json",
                 vehicle_roster_path="data/vehicle_roster.json",
                 operator_line_patches_path="data/operator_line_patches.json",
                 operator_replacements_path="data/operator_replacements.json",
                 ui_state_path="data/editor_state.json",
                 line_patches_path="data/line_patches.json",
                 zoom=None, detect=False):
        self.root = root
        self.token = token
        self.username = username
        self.statuses = list(statuses or [])
        self.statuses_path = statuses_path
        self.stations_path = stations_path
        self.dashboard_path = dashboard_path
        self.loc_class_families = loc_class_families
        self.ignore_plus = ignore_plus
        self.edge_patches_path = edge_patches_path
        self.station_patches_path = station_patches_path
        self.line_color_patches_path = line_color_patches_path
        self.home_region_path = home_region_path
        self.boarding_patches_path = boarding_patches_path
        self.vehicle_roster_path = vehicle_roster_path
        self.operator_line_patches_path = operator_line_patches_path
        self.operator_replacements_path = operator_replacements_path
        self.ui_state_path = ui_state_path
        self.ui_state = load_ui_state(ui_state_path)
        start_zoom = (
            clamp_zoom(zoom) or self.ui_state.get("zoom")
            or (detect_zoom() if detect else 1.0)
        )
        self.zoom = UiZoom(root, start_zoom)
        self.patches = ep.load_patches(edge_patches_path)
        self.station_patches = sp.load_patches(station_patches_path)
        self.line_color_patches = lcp.load_patches(line_color_patches_path)
        self.boarding_patches = bp.load_patches(boarding_patches_path)
        self.line_patches_path = line_patches_path
        self._line_patches_ok = True
        self.line_patches = self._read_line_patches()
        self._patch_server = None
        self._station_server = None
        self.limit = limit
        self.since = since
        self.filtered = []
        self.current = None
        self._busy = False
        self._ignore_dubi = False
        self._tag_rows = []
        self._edge_rows = []
        self._ignore_select = False
        self._edit_entry = None
        self._edit_iid = None
        self._edit_col = None
        self._edit_closing = False
        self._baseline = {}
        self._xfer = None
        self._line_key_filter = None
        # (Spalte, absteigend), Index 0 = primär
        self._sort_keys = [(c, d) for c, d in self.ui_state["sort"]]
        self._reset_baselines()

        root.title(f"Walita – Tag-Editor ({username})")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self._apply_zoom(self.zoom.factor)
        self._apply_filter()
        self._show_page(self.ui_state.get("page") or "trips:all")
        kids = self.trips.get_children()
        if kids:
            self.trips.selection_set(kids[0])
            self.trips.focus(kids[0])
            self._on_select()
        src = (
            f" aus {statuses_path}"
            if os.path.isfile(statuses_path) else " von der API"
        )
        self._set_status(f"{len(self.statuses)} Fahrten geladen{src}.")
        self.root.after_idle(self._reconcile_lines)

    def _reconcile_lines(self, ask=True):
        """Abgleich Datei ↔ walita:line nach dem Laden."""
        self.line_patches = self._read_line_patches()
        if not self._line_patches_ok or es.check_config_file(self.line_patches_path):
            self._set_status("line_patches.json ungültig – kein Abgleich mit walita:line.")
            return
        result = lp.reconcile(self.line_patches, self.statuses)
        if not any(result.values()):
            return
        choices = {}
        if result["conflicts"] and ask:
            dates = {s.get("id"): _checkin_bits(s)["date"] for s in self.statuses}
            dlg = LineConflictDialog(self.root, result["conflicts"], dates)
            choices = dlg.result or {}
        n_file, n_tag = apply_line_reconcile(
            self.line_patches, self.statuses, result, choices)
        if n_file and not lp.save_patches(self.line_patches_path, self.line_patches):
            messagebox.showerror(
                "Linie", "line_patches.json nicht schreibbar.", parent=self.root)
            return
        self._reload_visible_page("settings:lines")
        self._apply_filter()
        self._refresh_nav_counts()
        if self.current is not None:
            self._load_status(self.current)
        open_n = sum(1 for c in result["conflicts"]
                     if choices.get(c[0], "later") == "later")
        msg = f"Linien abgeglichen: {n_file} aus Tags übernommen, {n_tag} Tags vorgemerkt"
        if open_n:
            msg += f", {open_n} Konflikte offen"
        self._set_status(msg + ".")

    def _read_line_patches(self):
        """Linien-Patches; ungültige Datei → leer, Schreiben und Abgleich gesperrt."""
        try:
            patches = lp.load_patches(self.line_patches_path)
        except ValueError:
            self._line_patches_ok = False
            return lp.empty_patches()
        self._line_patches_ok = True
        return patches

    def _reset_baselines(self):
        self._baseline = {
            s.get("id"): _snapshot_status(s)
            for s in self.statuses
            if s.get("id") is not None
        }
        self._original_tags = {
            s.get("id"): [
                _norm_tag(t) for t in (s.get("tags") or []) if _norm_tag(t)["key"]
            ]
            for s in self.statuses
            if s.get("id") is not None
        }

    def _status_dirty(self, status):
        sid = status.get("id")
        if sid is None:
            return False
        return _snapshot_status(status) != self._baseline.get(sid)

    def _any_dirty(self):
        self._flush_detail()
        return any(self._status_dirty(s) for s in self.statuses)

    NAV_TRIPS = (
        ("trips:all", "Alle"),
        ("trips:noloc", "Ohne Baureihe"),
        ("trips:dirty", "Geändert"),
    )

    def _build(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        self._build_toolbar(outer)
        self.status_var = tk.StringVar(value="")
        ttk.Label(
            outer, textvariable=self.status_var, relief="sunken", anchor="w",
            padding=(6, 2),
        ).pack(side="bottom", fill="x")
        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        nav = ttk.Frame(body, padding=(6, 6, 0, 6))
        self.content = ttk.Frame(body, padding=6)
        body.add(nav, weight=0)
        body.add(self.content, weight=1)
        self._build_nav(nav)
        self.pages = {}
        self.trips_page = ttk.Frame(self.content)
        self.pages["trips"] = self.trips_page
        self._build_trips_page(self.trips_page)
        self._build_settings_pages()
        for seq in ("<Control-s>", "<Control-S>"):
            self.root.bind(seq, lambda _e: self._save())
        self.root.bind("<Control-r>", lambda _e: self._reload_from_api("normal"))
        # Feststelltaste: Strg+R kommt als Control-R an, Umschalt zusätzlich nur bei Strg+Umschalt+R.
        self.root.bind("<Control-R>", lambda _e: self._reload_from_api("normal"))
        self.root.bind("<Control-Shift-R>", lambda _e: self._reload_from_api("full"))
        for seq in ("<Control-f>", "<Control-F>"):
            self.root.bind(seq, lambda _e: self._focus_search())
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.root.bind(seq, lambda _e: self._step_zoom(1))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.root.bind(seq, lambda _e: self._step_zoom(-1))
        for seq in ("<Control-0>", "<Control-KP_0>"):
            self.root.bind(seq, lambda _e: self._apply_zoom(1.0))

    def _step_zoom(self, direction):
        self._apply_zoom(self.zoom.factor + direction * ZOOM_STEP)
        return "break"

    def _apply_zoom(self, factor):
        """Zoom setzen; Fenster-Mindestgröße und Aufteilung folgen."""
        factor = self.zoom.set(factor)
        self.zoom_var.set(f"{int(round(factor * 100))} %")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.root.minsize(
            min(self.zoom.px(1100), max(400, screen_w - 80)),
            min(self.zoom.px(580), max(300, screen_h - 120)),
        )
        self.root.after_idle(self._clamp_sash)
        return "break"

    def _build_toolbar(self, parent):
        bar = ttk.Frame(parent, padding=(6, 6))
        bar.pack(fill="x")
        self.reload_btn = ttk.Button(
            bar, text="⟳ Von API laden", command=lambda: self._reload_from_api("normal")
        )
        self.reload_btn.pack(side="left")
        self.reload_menu_btn = ttk.Menubutton(bar, text="▾", width=2)
        menu = tk.Menu(self.reload_menu_btn, tearoff=False)
        menu.add_command(label="Normal (neue + letzte 2 Tage)", accelerator="Strg+R",
                         command=lambda: self._reload_from_api("normal"))
        menu.add_command(label="Vollständig", accelerator="Strg+Umschalt+R",
                         command=lambda: self._reload_from_api("full"))
        menu.add_command(label="Seit Datum …",
                         command=lambda: self._reload_from_api("since"))
        self.reload_menu_btn["menu"] = menu
        self.reload_menu_btn.pack(side="left")
        self.save_btn = ttk.Button(bar, text="Speichern", command=self._save)
        self.save_btn.pack(side="left", padx=6)
        self.dash_btn = ttk.Button(
            bar, text="▶ Dashboard bauen", command=self._on_rebuild_dashboard
        )
        self.dash_btn.pack(side="left")
        ttk.Label(bar, text=f"Angemeldet als {self.username}").pack(side="right")
        zoom_fr = ttk.Frame(bar)
        zoom_fr.pack(side="right", padx=(0, 16))
        ttk.Button(zoom_fr, text="−", width=3,
                   command=lambda: self._step_zoom(-1)).pack(side="left")
        self.zoom_var = tk.StringVar(value="100 %")
        zoom_label = ttk.Label(zoom_fr, textvariable=self.zoom_var, width=6,
                               anchor="center", cursor="hand2")
        zoom_label.pack(side="left")
        zoom_label.bind("<Button-1>", lambda _e: self._apply_zoom(1.0))
        ttk.Button(zoom_fr, text="+", width=3,
                   command=lambda: self._step_zoom(1)).pack(side="left")
        ttk.Separator(parent, orient="horizontal").pack(fill="x")

    def _build_nav(self, parent):
        self.nav = ttk.Treeview(parent, show="tree", selectmode="browse")
        self.nav.column("#0", width=190, stretch=False)
        self.nav.pack(fill="y", expand=True)
        group = self.nav.insert("", "end", iid="g:trips", text="FAHRTEN", open=True)
        for key, label in self.NAV_TRIPS:
            self.nav.insert(group, "end", iid=key, text=label)
        for group_label, pages in es.NAV_GROUPS:
            gid = "g:" + group_label
            entries = [(k, label) for k, label in pages if k in es.PAGES]
            if not entries:
                continue
            self.nav.insert("", "end", iid=gid, text=group_label, open=True)
            for page_key, label in entries:
                self.nav.insert(gid, "end", iid="settings:" + page_key, text=label)
        self.nav.bind("<<TreeviewSelect>>", lambda _e: self._on_nav())
        self.nav.bind("<ButtonRelease-1>", self._on_nav_click, add="+")

    def _on_nav(self, force=False):
        sel = self.nav.selection()
        if not sel or sel[0].startswith("g:"):
            return
        if force or sel[0] != self.ui_state.get("page"):
            self._show_page(sel[0])

    def _on_nav_click(self, event):
        """Klick auf die schon gewählte Seite lädt sie neu (Filter, Datei)."""
        if self.nav.identify_row(event.y) == self.ui_state.get("page"):
            self._on_nav(force=True)

    def _refresh_nav_counts(self):
        if not hasattr(self, "nav"):
            return
        n_dirty = sum(1 for s in self.statuses if self._status_dirty(s))
        counts = {
            "trips:all": len(self.statuses),
            "trips:noloc": sum(1 for s in self.statuses if not has_tag(s, KEY_LOC)),
            "trips:dirty": n_dirty,
        }
        for key, label in self.NAV_TRIPS:
            self.nav.item(key, text=f"{label}  ({counts[key]})")
        self.save_btn.configure(
            text=f"Speichern ({n_dirty})" if n_dirty else "Speichern"
        )

    def _show_page(self, key):
        if key.startswith("trips:"):
            page = "trips"
        else:
            page = key.split(":", 1)[-1]
            if page not in self.pages:
                page, key = "trips", "trips:all"
        self._commit_edit()
        self._flush_detail()
        for name, frame in self.pages.items():
            if name == page:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        self.ui_state["page"] = key
        if self.nav.exists(key) and self.nav.selection() != (key,):
            self.nav.selection_set(key)
            self.nav.see(key)
        if page == "trips":
            self._set_trip_view(key)
            if not self._sash_restored:
                self._sash_restored = True
                self.root.after_idle(self._restore_sash)
        else:
            self.settings_pages[page].reload()

    def _on_search_changed(self):
        if self.filter_var.get():
            self._line_key_filter = None
        self._apply_filter()

    def _set_trip_view(self, key):
        self._line_key_filter = None
        for var in self.filter_flags.values():
            var.set(False)
        if key == "trips:noloc":
            self.filter_flags["noloc"].set(True)
        elif key == "trips:dirty":
            self.filter_flags["dirty"].set(True)
        self._apply_filter()

    def _focus_search(self):
        page = self.ui_state.get("page", "")
        target = None
        if page.startswith("settings:"):
            target = getattr(self.settings_pages.get(page[9:]), "search_entry", None)
        else:
            target = self.search_entry
        if target is not None:
            target.focus_set()

    def _save_ui_state(self):
        self.ui_state["sort"] = [[c, d] for c, d in self._sort_keys]
        # Breiten bei Zoom 100 %, damit sie unter jedem Zoom passen.
        applied = self.zoom.applied(self.trips)
        self.ui_state["widths"] = {
            c: int(round(int(self.trips.column(c, "width")) / applied))
            for c in TRIP_COLS
        }
        self.ui_state["zoom"] = self.zoom.factor
        try:
            self.ui_state["sash"] = int(self.trips_panes.sashpos(0))
        except tk.TclError:
            pass
        save_ui_state(self.ui_state_path, self.ui_state)

    def _restore_sash(self):
        """Gespeicherte Aufteilung, sonst 60 % Liste; erst wenn die Breite steht."""
        try:
            total = self.trips_panes.winfo_width()
        except tk.TclError:
            return
        if total <= 1:
            self.root.after(50, self._restore_sash)
            return
        sash = self.ui_state.get("sash") or int(total * 0.6)
        self._clamp_sash(sash)

    def _clamp_sash(self, want=None):
        """Hält Liste und Details sichtbar: jede Seite mindestens TRIP_PANE_MIN px."""
        try:
            total = self.trips_panes.winfo_width()
            if total <= 1:
                return
            pos = self.trips_panes.sashpos(0) if want is None else int(want)
            lo = min(self.zoom.px(TRIP_PANE_MIN), total // 2)
            new = max(lo, min(total - lo, pos))
            if new != self.trips_panes.sashpos(0):
                self.trips_panes.sashpos(0, new)
        except tk.TclError:
            pass

    def _build_trips_page(self, parent):
        self.trips_panes = ttk.Panedwindow(parent, orient="horizontal")
        self.trips_panes.pack(fill="both", expand=True)
        left = ttk.Frame(self.trips_panes, padding=(0, 0, 8, 0))
        right_outer = ttk.Frame(self.trips_panes, padding=(8, 0, 0, 0))
        self.trips_panes.add(left, weight=3)
        self.trips_panes.add(right_outer, weight=2)
        self._build_trip_list(left)
        self._build_detail(right_outer)
        self._sash_restored = False
        clamp = lambda _e: self.root.after_idle(self._clamp_sash)
        self.trips_panes.bind("<Configure>", clamp, add="+")
        self.trips_panes.bind("<ButtonRelease-1>", clamp, add="+")

    def _build_trip_list(self, left):
        filt = ttk.Frame(left)
        filt.pack(fill="x")
        row1 = ttk.Frame(filt)
        row1.pack(fill="x")
        ttk.Label(row1, text="Suche").pack(side="left")
        self.filter_var = tk.StringVar()
        _on_filter = lambda *_: self._on_search_changed()
        if hasattr(self.filter_var, "trace_add"):
            self.filter_var.trace_add("write", _on_filter)
        else:
            self.filter_var.trace("w", _on_filter)
        self.search_entry = ttk.Entry(row1, textvariable=self.filter_var)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(4, 10))
        ttk.Label(row1, text="Zeitraum").pack(side="left")
        self.period_var = tk.StringVar()
        self.period_box = ttk.Combobox(
            row1, textvariable=self.period_var, state="readonly", width=16
        )
        self.period_box.pack(side="left", padx=(4, 10))
        ttk.Label(row1, text="Betreiber").pack(side="left")
        self.operator_var = tk.StringVar(value="Alle")
        self.operator_box = ttk.Combobox(
            row1, textvariable=self.operator_var, state="readonly", width=26
        )
        self.operator_box.pack(side="left", padx=(4, 0))
        self.period_box.bind("<<ComboboxSelected>>", lambda _e: self._apply_filter())
        self.operator_box.bind("<<ComboboxSelected>>", lambda _e: self._apply_filter())
        row2 = ttk.Frame(filt)
        row2.pack(fill="x", pady=(4, 0))
        self.filter_flags = {}
        for key, label in (
            ("noloc", "ohne Baureihe"), ("noveh", "ohne Nummer"),
            ("patched", "mit lokalem Patch"), ("dirty", "geändert"),
        ):
            var = tk.BooleanVar(value=False)
            self.filter_flags[key] = var
            ttk.Checkbutton(
                row2, text=label, variable=var, command=self._apply_filter
            ).pack(side="left", padx=(0, 12))
        self._period_opts = [("all", "Alle")]
        self._fill_filter_choices()

        list_fr = ttk.Frame(left)
        list_fr.pack(fill="both", expand=True, pady=(6, 0))
        self.trips = ttk.Treeview(
            list_fr, columns=TRIP_COLS, show="headings", selectmode="extended"
        )
        widths = self.ui_state.get("widths") or {}
        for col in TRIP_COLS:
            self.trips.heading(col, command=lambda c=col: self._sort_by(c))
            self.trips.column(
                col, width=widths.get(col, COL_WIDTHS[col]), minwidth=40,
                stretch=(col == "route"),
                anchor="center" if col == "marks" else "w",
            )
        self._refresh_headings()
        yscroll = ttk.Scrollbar(list_fr, orient="vertical", command=self.trips.yview)
        xscroll = ttk.Scrollbar(list_fr, orient="horizontal", command=self.trips.xview)
        self.trips.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.trips.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        list_fr.rowconfigure(0, weight=1)
        list_fr.columnconfigure(0, weight=1)
        self.footer_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.footer_var, anchor="e").pack(fill="x")

        self.trips.tag_configure("dirty", font=es.FONT_BOLD)

        self.trips.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self.trips.bind("<Button-1>", self._on_trip_click, add="+")
        self.trips.bind("<Double-1>", self._on_trip_click, add="+")
        self.trips.bind("<F2>", self._on_f2)
        self.trip_menu = tk.Menu(self.trips, tearoff=False)
        # Kontextmenü-Taste: "App" unter Windows, "Menu" unter X11.
        right = "<Button-2>" if sys.platform == "darwin" else "<Button-3>"
        for seq in (right, "<Shift-F10>", "<App>", "<Menu>"):
            try:
                self.trips.bind(seq, self._on_trip_menu)
            except tk.TclError:
                pass
        for seq in ("<Control-a>", "<Control-A>"):
            self.trips.bind(seq, self._select_all_trips)
        self.trips.bind("<MouseWheel>", lambda _e: self._commit_edit())

    def _fill_filter_choices(self):
        self._period_opts = period_options(self.statuses, datetime.date.today())
        labels = [label for _k, label in self._period_opts]
        self.period_box.configure(values=labels)
        if self.period_var.get() not in labels:
            self.period_var.set(labels[0])
        ops = sorted({operator_of(s) for s in self.statuses}, key=str.casefold)
        values = ["Alle"] + [o or NO_OPERATOR_LABEL for o in ops]
        self.operator_box.configure(values=values)
        if self.operator_var.get() not in values:
            self.operator_var.set("Alle")

    def _build_detail(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0)
        vbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        try:
            canvas.configure(background=ttk.Style().lookup("TFrame", "background"))
        except tk.TclError:
            pass

        self.title_var = tk.StringVar(value="Keine Fahrt gewählt.")
        self.meta_var = tk.StringVar(value="")
        ttk.Label(
            right, textvariable=self.title_var, font=es.FONT_TITLE,
            wraplength=420, justify="left",
        ).pack(anchor="w")
        ttk.Label(right, textvariable=self.meta_var, wraplength=420,
                  justify="left").pack(anchor="w")
        self.trwl_link = ttk.Label(
            right, text="⇄ Auf Träwelling öffnen", foreground="#1a5fb4",
            cursor="hand2",
        )
        self.trwl_link.pack(anchor="w", pady=(2, 0))
        self.trwl_link.bind("<Button-1>", lambda _e: self._open_on_traewelling())

        collapsed = self.ui_state["collapsed"]
        self.sections = {}

        def section(key, title, local=False, **pack):
            sec = Section(right, key, title, collapsed, local=local)
            sec.pack(fill="x", **pack)
            self.sections[key] = sec
            return sec.body

        self._build_vehicle_group(section("vehicle", "Fahrzeug"))
        self._build_route_group(section("route", "Fahrtverlauf", local=True))
        self._build_display_group(section("display", "Darstellung", local=True))
        self._build_text_group(section("text", "Text & Tags"))

    def _build_vehicle_group(self, parent):
        self.roster = vr.load_roster(self.vehicle_roster_path)
        ttk.Label(parent, text="Baureihe").grid(row=0, column=0, sticky="w")
        self.loc_var = tk.StringVar()
        self.loc_box = ttk.Combobox(parent, textvariable=self.loc_var, width=30)
        self.loc_box.grid(row=0, column=1, sticky="we", pady=2, padx=(8, 0))
        ttk.Label(parent, text="Nummer").grid(row=1, column=0, sticky="w")
        self.veh_var = tk.StringVar()
        self.veh_entry = ttk.Entry(parent, textvariable=self.veh_var, width=32)
        self.veh_entry.grid(row=1, column=1, sticky="we", pady=2, padx=(8, 0))
        self.roster_hint_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.roster_hint_var).grid(
            row=2, column=1, sticky="w", padx=(8, 0)
        )
        parent.columnconfigure(1, weight=1)
        for widget in (self.loc_box, self.veh_entry):
            widget.bind("<FocusOut>", lambda _e: self._on_vehicle_field())
            widget.bind("<Return>", lambda _e: self._on_vehicle_field())
        self.loc_box.bind("<<ComboboxSelected>>", lambda _e: self._on_vehicle_field())
        self.veh_var.trace_add("write", lambda *_: self._update_roster_hint())
        self.loc_var.trace_add("write", lambda *_: self._update_roster_hint())
        self._ignore_vehicle = False
        self._refresh_vehicle_fields()

    def _build_route_group(self, parent):
        board_row = ttk.Frame(parent)
        board_row.pack(fill="x")
        ttk.Label(board_row, text="Einstieg").pack(side="left")
        self._board_var = tk.StringVar(value="—")
        ttk.Label(board_row, textvariable=self._board_var).pack(
            side="left", padx=(8, 0)
        )
        self.board_reset_btn = ttk.Button(
            board_row, text="Zurücksetzen", command=self._reset_boarding
        )
        self.board_reset_btn.pack(side="right")
        self.board_pick_btn = ttk.Button(
            board_row, text="Ändern", command=self._pick_boarding
        )
        self.board_pick_btn.pack(side="right", padx=(0, 6))
        self._board_src_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self._board_src_var, foreground="#666666").pack(
            anchor="w"
        )

        dubi = ttk.Frame(parent)
        dubi.pack(fill="x", pady=(8, 0))
        ttk.Label(dubi, text="Durchbindung").pack(side="left")
        ttk.Label(dubi, text="geht an Träwelling", foreground="#666666").pack(
            side="right"
        )
        self.dubi_start_var = tk.BooleanVar(value=False)
        self.dubi_ende_var = tk.BooleanVar(value=False)
        self.dubi_start_btn = ttk.Checkbutton(
            parent, text="Beginn ist kein Einstieg",
            variable=self.dubi_start_var, command=self._on_dubi_changed,
        )
        self.dubi_start_btn.pack(anchor="w", padx=(12, 0))
        self.dubi_ende_btn = ttk.Checkbutton(
            parent, text="Ende ist kein Ausstieg",
            variable=self.dubi_ende_var, command=self._on_dubi_changed,
        )
        self.dubi_ende_btn.pack(anchor="w", padx=(12, 0))
        self._sync_dubi_buttons()

        ttk.Label(parent, text="Kanten").pack(anchor="w", pady=(8, 2))
        edge_fr = ttk.Frame(parent)
        edge_fr.pack(fill="x")
        self.edges = ttk.Treeview(
            edge_fr, columns=("origin", "dest", "patch"), show="headings",
            selectmode="browse", height=5,
        )
        self.edges.heading("origin", text="Von")
        self.edges.heading("dest", text="Nach")
        self.edges.heading("patch", text="Patch")
        self.edges.column("origin", width=130)
        self.edges.column("dest", width=130)
        self.edges.column("patch", width=110)
        edge_scroll = ttk.Scrollbar(edge_fr, orient="vertical", command=self.edges.yview)
        self.edges.configure(yscrollcommand=edge_scroll.set)
        self.edges.pack(side="left", fill="x", expand=True)
        edge_scroll.pack(side="right", fill="y")
        self.edges.bind("<Double-1>", lambda _e: self._open_edge_map())
        self.edges.bind("<<TreeviewSelect>>", lambda _e: self._sync_edge_menu())

        edge_btns = ttk.Frame(parent)
        edge_btns.pack(fill="x", pady=6)
        self.edge_map_btn = ttk.Button(
            edge_btns, text="Auf Karte anreichern", command=self._open_edge_map
        )
        self.edge_map_btn.pack(side="left")
        self.edge_menu_btn = ttk.Menubutton(edge_btns, text="Patch entfernen ▾")
        self.edge_menu = tk.Menu(self.edge_menu_btn, tearoff=False)
        self.edge_menu.add_command(
            label="Fahrt-Override", command=self._clear_edge_override
        )
        self.edge_menu.add_command(
            label="Standard für diese Kante", command=self._clear_edge_default
        )
        self.edge_menu_btn["menu"] = self.edge_menu
        self.edge_menu_btn.pack(side="left", padx=6)
        self.edge_split_btn = ttk.Button(
            edge_btns, text="Station aufteilen …", command=self._split_edge_station
        )
        self.edge_split_btn.pack(side="left")
        self._refresh_boarding()

    def _build_display_group(self, parent):
        op_row = ttk.Frame(parent)
        op_row.pack(fill="x")
        # Knöpfe zuerst packen: bei wenig Platz schrumpft das Eingabefeld.
        ttk.Label(op_row, text="Betreiber").pack(side="left")
        self.operator_reset_btn = ttk.Button(
            op_row, text="Zurücksetzen", command=lambda: self._set_operator(""))
        self.operator_reset_btn.pack(side="right")
        self.operator_apply_btn = ttk.Button(
            op_row, text="Übernehmen",
            command=lambda: self._set_operator(self.operator_field_var.get()))
        self.operator_apply_btn.pack(side="right", padx=(0, 6))
        self._operator_src_var = tk.StringVar(value="")
        ttk.Label(op_row, textvariable=self._operator_src_var,
                  foreground="#666666").pack(side="right", padx=(0, 6))
        self.operator_field_var = tk.StringVar(value="")
        self.operator_field = ttk.Combobox(
            op_row, textvariable=self.operator_field_var, width=16)
        self.operator_field.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.operator_field.bind(
            "<Return>", lambda _e: self._set_operator(self.operator_field_var.get()))
        self._operator_scope_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self._operator_scope_var,
                  foreground="#666666").pack(anchor="w", pady=(0, 6))
        self._refresh_operator_field()
        line_row = ttk.Frame(parent)
        line_row.pack(fill="x", pady=(0, 6))
        ttk.Label(line_row, text="Linie").pack(side="left")
        self.line_reset_btn = ttk.Button(
            line_row, text="Zurücksetzen", command=lambda: self._set_line(""))
        self.line_reset_btn.pack(side="right")
        self.line_apply_btn = ttk.Button(
            line_row, text="Übernehmen",
            command=lambda: self._set_line(self.line_var.get()))
        self.line_apply_btn.pack(side="right", padx=(0, 6))
        self._line_src_var = tk.StringVar(value="")
        ttk.Label(line_row, textvariable=self._line_src_var,
                  foreground="#666666").pack(side="right", padx=(0, 6))
        self.line_var = tk.StringVar(value="")
        self.line_entry = ttk.Entry(line_row, textvariable=self.line_var, width=16)
        self.line_entry.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.line_entry.bind("<Return>", lambda _e: self._set_line(self.line_var.get()))
        self._refresh_line_field()
        color_row = ttk.Frame(parent)
        color_row.pack(fill="x")
        ttk.Label(color_row, text="Linienfarbe").pack(side="left")
        # Knöpfe zuerst packen: bei wenig Platz werden die Texte abgeschnitten.
        self.color_reset_btn = ttk.Button(
            color_row, text="Zurücksetzen", command=self._reset_line_color
        )
        self.color_reset_btn.pack(side="right")
        self.color_pick_btn = ttk.Button(
            color_row, text="Ändern", command=self._pick_line_color
        )
        self.color_pick_btn.pack(side="right", padx=(0, 6))
        self._color_swatch = tk.Frame(
            color_row, width=32, height=18, relief="solid", bd=1,
            highlightthickness=0, cursor="hand2",
        )
        self._color_swatch.pack(side="left", padx=(8, 6))
        self._color_swatch.pack_propagate(False)
        self._color_hex_var = tk.StringVar(value="—")
        ttk.Label(color_row, textvariable=self._color_hex_var).pack(side="left")
        self._color_src_var = tk.StringVar(value="")
        ttk.Label(color_row, textvariable=self._color_src_var,
                  foreground="#666666").pack(side="left", padx=(6, 0))
        self._color_swatch.bind("<Button-1>", lambda _e: self._pick_line_color())
        self._refresh_line_color()

    def _build_text_group(self, parent):
        head = ttk.Frame(parent)
        head.pack(fill="x")
        ttk.Label(head, text="Status-Text").pack(side="left")
        self.body_count = tk.StringVar(value="0/280")
        ttk.Label(head, textvariable=self.body_count).pack(side="right")
        self.body = tk.Text(parent, height=4, wrap="word", undo=True)
        self.body.pack(fill="x")
        self.body.bind("<KeyRelease>", lambda _e: self._on_body_changed())

        ttk.Label(parent, text="Weitere Tags").pack(anchor="w", pady=(10, 2))
        tree_fr = ttk.Frame(parent)
        tree_fr.pack(fill="both", expand=True)
        cols = ("key", "value", "visibility")
        self.tree = ttk.Treeview(
            tree_fr, columns=cols, show="headings", selectmode="browse", height=5
        )
        self.tree.heading("key", text="Schlüssel")
        self.tree.heading("value", text="Wert")
        self.tree.heading("visibility", text="Sichtbarkeit")
        self.tree.column("key", width=140)
        self.tree.column("value", width=140)
        self.tree.column("visibility", width=100)
        tree_scroll = ttk.Scrollbar(tree_fr, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self._edit_tag())

        tag_btns = ttk.Frame(parent)
        tag_btns.pack(fill="x", pady=6)
        ttk.Button(tag_btns, text="+", width=3, command=self._add_tag).pack(side="left")
        ttk.Button(tag_btns, text="✎", width=3, command=self._edit_tag).pack(
            side="left", padx=6
        )
        ttk.Button(tag_btns, text="−", width=3, command=self._delete_tag).pack(
            side="left"
        )

    def _refresh_vehicle_fields(self):
        if not hasattr(self, "loc_box"):
            return
        self._ignore_vehicle = True
        try:
            st = self.current
            names = vr.collect_loc_classes(
                self.statuses, (self.roster.get("types") or {}).keys()
            )
            self.loc_box.configure(values=names)
            self.loc_var.set(_tag_value(st, KEY_LOC) if st else "")
            self.veh_var.set(_tag_value(st, KEY_VEH) if st else "")
            state = "normal" if st is not None and not self._busy else "disabled"
            self.loc_box.configure(state=state)
            self.veh_entry.configure(state=state)
        finally:
            self._ignore_vehicle = False
        self._update_roster_hint()

    def _update_roster_hint(self):
        if self.current is None:
            self.roster_hint_var.set("")
            return
        self.roster_hint_var.set(
            roster_hint(self.roster, self.loc_var.get(), self.veh_var.get())
        )

    def _on_vehicle_field(self):
        if self._ignore_vehicle or self.current is None:
            return
        changed = False
        for key, value in ((KEY_LOC, self.loc_var.get()), (KEY_VEH, self.veh_var.get())):
            if _tag_value(self.current, key) != value.strip():
                set_table_tag(self.current, key, value.strip())
                changed = True
        self._update_roster_hint()
        if changed:
            self._refresh_trip_row(self.current)
            self._refresh_nav_counts()
            self._update_footer()

    def _open_on_traewelling(self):
        if self.current is not None and self.current.get("id") is not None:
            webbrowser.open(f"https://traewelling.de/status/{self.current['id']}")

    def _sync_edge_menu(self):
        if not hasattr(self, "edge_menu"):
            return
        row = self._selected_edge()
        sid = self.current.get("id") if self.current else None
        has_ov = bool(row) and (
            (sid, row["from_id"], row["to_id"]) in (self.patches.get("overrides") or {})
        )
        has_def = bool(row) and (
            (row["from_id"], row["to_id"]) in (self.patches.get("defaults") or {})
        )
        self.edge_menu.entryconfigure(0, state="normal" if has_ov else "disabled")
        self.edge_menu.entryconfigure(1, state="normal" if has_def else "disabled")
        off = self._busy or not (has_ov or has_def)
        self.edge_menu_btn.configure(state="disabled" if off else "normal")

    def _build_settings_pages(self):
        self.settings_ctx = es.SettingsContext(
            root=self.root, statuses=self.statuses, stations_path=self.stations_path,
            paths={
                "replacements": self.operator_replacements_path,
                "line_rules": self.operator_line_patches_path,
                "families": self.loc_class_families,
                "home": self.home_region_path,
                "roster": self.vehicle_roster_path,
                "colors": self.line_color_patches_path,
                "boarding": self.boarding_patches_path,
                "edges": self.edge_patches_path,
                "stations": self.station_patches_path,
                "lines": self.line_patches_path,
            },
            goto_status=self._goto_status,
            open_edge_map=self._open_edge_map_for,
            open_station_map=self._open_station_map,
            after_change=self._after_settings_change,
            set_status=self._set_status,
            pick_color=self._ask_line_color,
            pick_boarding=self._ask_boarding,
            stage_line_tags=self._stage_line_tags,
            filter_trips=self._filter_trips,
            clear_line_tags=self._clear_line_tags,
            fetch_station=self._fetch_station,
        )
        self.settings_pages = {}
        for key, cls in es.PAGES.items():
            page = cls(self.content, self.settings_ctx)
            self.pages[key] = page
            self.settings_pages[key] = page

    def _stage_line_tags(self, src_key):
        """walita:line = effektiver Name für alle Fahrten einer gemergten Linie."""
        self._flush_detail()
        n = 0
        for s in self.statuses:
            if lp.source_key(s, self.line_patches) != tuple(src_key):
                continue
            eff = lp.effective_line(s, self.line_patches, use_tag=False)[0]
            set_table_tag(s, KEY_LINE, eff)
            n += 1
        self._apply_filter()
        self._refresh_nav_counts()
        if self.current is not None:
            self._load_status(self.current)
        return n

    def _filter_trips(self, key):
        """Fahrtenliste exakt auf einen Linien-Schlüssel (nach Override, vor Merge)."""
        self._show_page("trips:all")
        self.period_var.set(self._period_opts[0][1])
        self.operator_var.set("Alle")
        self.filter_var.set("")
        self._line_key_filter = tuple(key)
        self._apply_filter()

    def _clear_line_tags(self, src_key, line):
        """Entfernt walita:line = `line` von den Fahrten eines Linien-Schlüssels.

        Gibt die IDs zurück, deren Tag vorgemerkt gelöscht wurde.
        """
        self._flush_detail()
        sids = []
        for s in self.statuses:
            if lp.source_key(s, self.line_patches) != tuple(src_key):
                continue
            if lp.tag_line(s) != line:
                continue
            set_table_tag(s, KEY_LINE, "")
            sids.append(s.get("id"))
        self._apply_filter()
        self._refresh_nav_counts()
        if self.current is not None:
            self._load_status(self.current)
        return sids

    def _goto_status(self, sid):
        self.filter_var.set("")
        self.period_var.set(self._period_opts[0][1])
        self.operator_var.set("Alle")
        self._show_page("trips:all")
        st = self._select_id(sid)
        if st is None:
            self._set_status(f"Fahrt {sid} ist nicht in den geladenen Daten.")
            return
        self._flush_detail()
        self._load_status(st)

    def _after_settings_change(self, kind):
        self.patches = ep.load_patches(self.edge_patches_path)
        self.station_patches = sp.load_patches(self.station_patches_path)
        if self._station_server is not None:
            self._station_server.set_patches(self.station_patches)
        self.line_color_patches = lcp.load_patches(self.line_color_patches_path)
        self.boarding_patches = bp.load_patches(self.boarding_patches_path)
        self.line_patches = self._read_line_patches()
        self.roster = vr.load_roster(self.vehicle_roster_path)
        self._apply_filter()
        if self.current is not None:
            self._load_status(self.current)

    def _ask_line_color(self, parent, line_name, bg):
        dlg = LineColorDialog(parent, line_name, initial_bg=bg)
        return dlg.result

    def _ask_boarding(self, parent, status, selected_so):
        dlg = BoardingDialog(parent, status, selected_so)
        return dlg.result

    def _open_edge_map_for(self, from_id, to_id, status_id=None):
        if not self._config_ok(self.edge_patches_path, "Kanten"):
            return
        if status_id is not None:
            status = next(
                (s for s in self.statuses if s.get("id") == status_id), None
            )
        else:
            status = es.find_status_with_edge(
                self.statuses, from_id, to_id, self.patches
            )
        if status is None:
            messagebox.showinfo(
                "Kanten", "Keine Fahrt über diese Kante gefunden.", parent=self.root,
            )
            return
        self._launch_edge_map(status, from_id, to_id)


    def _set_status(self, msg):
        self.status_var.set(msg)
        log(msg)

    def _set_busy(self, busy, msg=None):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.save_btn, self.reload_btn, self.reload_menu_btn,
                    self.dash_btn, self.edge_map_btn, self.edge_split_btn):
            btn.configure(state=state)
        self._sync_edge_menu()
        self._sync_dubi_buttons()
        self._refresh_vehicle_fields()
        if busy:
            self.board_pick_btn.configure(state="disabled")
            self.board_reset_btn.configure(state="disabled")
        else:
            self._refresh_boarding()
        for page in self.settings_pages.values():
            page.set_busy(busy)
        if msg:
            self.status_var.set(msg)

    def _run_async(self, work, on_ok, busy_msg="Bitte warten …"):
        if self._busy:
            return
        self._set_busy(True, busy_msg)

        def worker():
            try:
                result = work()
            except Exception as e:
                self.root.after(0, lambda err=e: self._finish_err(err))
                return
            self.root.after(0, lambda r=result: self._finish_ok(on_ok, r))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_ok(self, on_ok, result):
        self._set_busy(False)
        on_ok(result)

    def _finish_err(self, err):
        self._set_busy(False)
        msg = format_api_error(err)
        self._set_status(msg)
        messagebox.showerror("Fehler", msg, parent=self.root)

    def _status_by_iid(self, iid):
        try:
            sid = int(iid)
        except (TypeError, ValueError):
            sid = iid
        for s in self.statuses:
            if s.get("id") == sid:
                return s
        return None

    def _apply_filter(self):
        if not hasattr(self, "trips"):
            return
        self._commit_edit()
        self._flush_detail()
        q = (self.filter_var.get() or "").strip().lower()
        period_key = dict(
            (label, k) for k, label in self._period_opts
        ).get(self.period_var.get(), "all")
        op = self.operator_var.get()
        today = datetime.date.today()
        flags = {k: v.get() for k, v in self.filter_flags.items()}

        line_key = self._line_key_filter

        def keep(s):
            if q and not self._matches_filter(s, q):
                return False
            if line_key and lp.source_key(s, self.line_patches) != line_key:
                return False
            if not in_period(s, period_key, today):
                return False
            if op != "Alle" and (operator_of(s) or NO_OPERATOR_LABEL) != op:
                return False
            if flags["noloc"] and has_tag(s, KEY_LOC):
                return False
            if flags["noveh"] and has_tag(s, KEY_VEH):
                return False
            if flags["patched"] and not patch_markers(
                s, self.boarding_patches, self.line_color_patches, self.patches,
                self.line_patches,
            ):
                return False
            if flags["dirty"] and not self._status_dirty(s):
                return False
            return True

        selected_id = self.current.get("id") if self.current else None
        marked = set(self.trips.selection())
        self.filtered = [s for s in self.statuses if keep(s)]
        self._apply_sort()
        self._ignore_select = True
        self.trips.delete(*self.trips.get_children())
        restore = None
        keep_marked = []
        for s in self.filtered:
            sid = s.get("id")
            if sid is None:
                continue
            iid = str(sid)
            tags = ("dirty",) if self._status_dirty(s) else ()
            self.trips.insert("", "end", iid=iid, values=self._row_values(s), tags=tags)
            if selected_id is not None and sid == selected_id:
                restore = iid
            elif iid in marked:
                keep_marked.append(iid)
        self._ignore_select = False
        if restore is not None:
            self.trips.selection_set([restore] + keep_marked)
            self.trips.focus(restore)
            self.trips.see(restore)
        elif self.current is not None:
            self.current = None
            self._clear_detail()
        self._update_footer()
        self._refresh_nav_counts()

    def _after_save_done(self):
        """Liste, Zähler und Fußzeile nach dem Speichern neu aufbauen."""
        self._apply_filter()

    def _refresh_current_row(self):
        if self.current is not None:
            self._refresh_trip_row(self.current)

    def _update_footer(self):
        n_dirty = sum(1 for s in self.filtered if self._status_dirty(s))
        key = self._line_key_filter
        self.footer_var.set(
            f"{len(self.filtered)} Fahrten"
            + (f" · Linie {key[0]} · {key[1]}" if key else "")
            + (f" · {n_dirty} ungespeichert" if n_dirty else "")
        )

    def _row_values(self, status):
        shown = self._shown(status)
        bits = _checkin_bits(shown)
        when = _fmt_when(bits["dep"]) if bits["dep"] else bits["date"]
        if self._status_dirty(status):
            when = "● " + when
        return (
            when, bits["line"], f"{bits['origin']} → {bits['dest']}",
            operator_of(status) or NO_OPERATOR_LABEL,
            _tag_value(shown, KEY_LOC), _tag_value(shown, KEY_VEH),
            patch_markers(
                status, self.boarding_patches, self.line_color_patches, self.patches,
                self.line_patches,
            ),
        )

    def _col_sort_key(self, status, col):
        if col == "date":
            origin = ((self._shown(status) or {}).get("checkin") or {}).get("origin") or {}
            ts = origin.get("departure") or origin.get("departurePlanned") or ""
            return (ts == "", ts)
        idx = TRIP_COLS.index(col)
        text = (str(self._row_values(status)[idx]) or "").strip()
        return (text == "", text.casefold())

    def _apply_sort(self):
        """Stabil mehrstufig: zuletzt geklickte Spalte zuerst, ältere als Tie-Breaker."""
        for col, desc in reversed(self._sort_keys):
            self.filtered.sort(
                key=lambda s, c=col: self._col_sort_key(s, c),
                reverse=desc,
            )

    def _refresh_headings(self):
        rank = {col: i + 1 for i, (col, _desc) in enumerate(self._sort_keys)}
        desc_of = {col: desc for col, desc in self._sort_keys}
        multi = len(self._sort_keys) > 1
        for col in TRIP_COLS:
            label = TRIP_HEADINGS[col]
            if col in rank:
                arrow = "▼" if desc_of[col] else "▲"
                if multi:
                    label = f"{label} {arrow}{rank[col]}"
                else:
                    label = f"{label} {arrow}"
            self.trips.heading(col, text=label)

    def _sort_by(self, col):
        if self._busy or col not in TRIP_COLS:
            return
        if self._sort_keys and self._sort_keys[0][0] == col:
            old_col, old_desc = self._sort_keys[0]
            self._sort_keys[0] = (old_col, not old_desc)
        else:
            self._sort_keys = [(c, d) for c, d in self._sort_keys if c != col]
            self._sort_keys.insert(0, (col, False))
        self._refresh_headings()
        self._apply_filter()

    def _refresh_trip_row(self, status):
        sid = status.get("id")
        if sid is None:
            return
        iid = str(sid)
        if not self.trips.exists(iid):
            return
        self.trips.item(
            iid, values=self._row_values(status),
            tags=("dirty",) if self._status_dirty(status) else (),
        )

    def _selected_status(self):
        """Fahrt für die Details: die fokussierte in der Auswahl, sonst die erste."""
        sel = self.trips.selection()
        if not sel:
            return None
        focus = self.trips.focus()
        return self._status_by_iid(focus if focus in sel else sel[0])

    def _on_trip_menu(self, event):
        """Kontextmenü; Rechtsklick außerhalb der Markierung markiert nur die Zeile."""
        if self._busy:
            return "break"
        iid = self.trips.identify_row(event.y) if event.y else ""
        if iid and iid not in self.trips.selection():
            self.trips.selection_set(iid)
            self.trips.focus(iid)
            self._on_select()
        targets = self._selected_statuses()
        if not targets:
            return "break"
        n = len(targets)
        self.trip_menu.delete(0, "end")
        self.trip_menu.add_command(
            label="Trip von Träwelling neu laden" + (f" ({n} Fahrten)" if n > 1 else ""),
            command=lambda: self._refresh_trips(targets),
        )
        self.trip_menu.add_command(
            label="Status von Träwelling übernehmen" + (f" ({n} Fahrten)" if n > 1 else ""),
            command=lambda: self._resync_statuses(targets),
        )
        x = event.x_root if event.x_root else self.trips.winfo_rootx() + 20
        y = event.y_root if event.y_root else self.trips.winfo_rooty() + 20
        self.trip_menu.tk_popup(x, y)
        return "break"

    def _resync_statuses(self, statuses):
        """Holt die Fahrten einzeln von Träwelling und schreibt sie in statuses.json."""
        self._commit_edit()
        self._flush_detail()
        ids = [s["id"] for s in statuses if s.get("id") is not None]
        if not ids:
            return
        if any(self._status_dirty(s) for s in statuses) and not messagebox.askyesno(
            "Status übernehmen",
            "Ungespeicherte Änderungen an diesen Fahrten gehen verloren. Fortfahren?",
            parent=self.root,
        ):
            return
        trips_path = os.path.join(os.path.dirname(self.statuses_path), "trips.json")

        def work():
            return resync_statuses(self.token, ids, self.statuses_path, trips_path,
                                   self.stations_path, self.operator_replacements_path)

        def done(result):
            fresh, errors = result
            for i, st in enumerate(self.statuses):
                new = fresh.get(st.get("id"))
                if new is None:
                    continue
                sid = new["id"]
                self.statuses[i] = new
                self._baseline[sid] = _snapshot_status(new)
                self._original_tags[sid] = [
                    _norm_tag(t) for t in (new.get("tags") or []) if _norm_tag(t)["key"]
                ]
                self._refresh_trip_row(new)
                if self.current is not None and self.current.get("id") == sid:
                    self.current = new
                    self._on_select()
            msg = (f"Status übernommen: {len(fresh)} von {len(ids)} Fahrten "
                   f"in {self.statuses_path}.")
            if errors:
                msg += " Fehler: " + "; ".join(
                    f"{sid}: {err}" for sid, err in sorted(errors.items(), key=str))
            self._set_status(msg)

        self._run_async(work, done, busy_msg="Lade Status von Träwelling …")

    def _refresh_trips(self, statuses):
        """Erzwingt den Neuabruf der Stopovers (überschreibt trips.json-Eintrag)."""
        trip_ids = {}
        for st in statuses:
            tid = (st.get("checkin") or {}).get("trip")
            if st.get("id") is not None and tid not in (None, ""):
                trip_ids[st["id"]] = tid
        if not trip_ids:
            self._set_status("Keine Trip-ID an der Fahrt – nichts neu zu laden.")
            return
        base = os.path.dirname(self.statuses_path)
        trips_path = os.path.join(base, "trips.json")

        def work():
            return refresh_trips(self.token, trip_ids, self.statuses_path,
                                 trips_path, self.stations_path)

        def done(result):
            fresh, errors = result
            for st in self.statuses:
                sts = fresh.get(st.get("id"))
                if sts:
                    st["trip"] = {"stopovers": sts}
                    st.pop("trip_error", None)
                    self._refresh_trip_row(st)
            if self.current is not None and self.current.get("id") in fresh:
                self._on_select()
            msg = f"Trip neu geladen: {len(fresh)} von {len(trip_ids)} Fahrten."
            if errors:
                msg += " Fehler: " + "; ".join(
                    f"{sid}: {err}" for sid, err in sorted(errors.items(), key=str))
            self._set_status(msg)

        self._run_async(work, done, busy_msg="Lade Trip von Träwelling …")

    def _selected_statuses(self):
        """Alle markierten Fahrten in Listenreihenfolge, sonst die aktuelle."""
        out = [s for s in (self._status_by_iid(i) for i in self.trips.selection()) if s]
        if not out and self.current is not None:
            out = [self.current]
        return out

    def _select_all_trips(self, _event=None):
        kids = self.trips.get_children()
        if kids:
            self.trips.selection_set(kids)
        return "break"

    def _merged_detail_tags(self):
        """Tabellen-Tags aus dem Status, übrige aus ``_tag_rows``.

        Die Reihenfolge folgt den Tags vom Laden. Neue Schlüssel hängen an.
        So bleibt eine unangetastete Fahrt beim Wechsel ungestagt.
        """
        pieces = []
        seen = set()
        for t in self.current.get("tags") or []:
            n = _norm_tag(t)
            if n["key"] in MANAGED_TAG_SET and n["key"] not in seen:
                pieces.append(n)
                seen.add(n["key"])
        for t in self._tag_rows:
            n = _norm_tag(t)
            if n["key"] and n["key"] not in MANAGED_TAG_SET and n["key"] not in seen:
                pieces.append(n)
                seen.add(n["key"])
        original = self._original_tags.get(self.current.get("id")) or []
        return _order_like_original(pieces, original)

    def _flush_detail(self):
        if self.current is None or not hasattr(self, "body"):
            return
        self._on_vehicle_field()
        new_body = self.body.get("1.0", "end-1c")
        merged = self._merged_detail_tags()
        if _snapshot_status(self.current) == (new_body or "", _tags_tuple(merged)):
            self._refresh_trip_row(self.current)
            return
        self.current["body"] = new_body
        self.current["tags"] = merged
        self._refresh_trip_row(self.current)
        self._refresh_nav_counts()

    def _clear_detail(self):
        self.title_var.set("Keine Fahrt gewählt.")
        self.meta_var.set("")
        self._refresh_vehicle_fields()
        self.body.delete("1.0", "end")
        self._update_body_count()
        self._tag_rows = []
        self._show_dubi_checks()
        self._refresh_extra_tree()
        self._refresh_edges()
        self._refresh_line_color()
        self._refresh_operator_field()
        self._refresh_line_field()
        self._refresh_boarding()
        self._sync_dubi_buttons()

    def _on_select(self):
        if self._busy or self._ignore_select:
            return
        self._refresh_operator_scope()
        nxt = self._selected_status()
        if nxt is None:
            return
        if self.current is not None and nxt.get("id") == self.current.get("id"):
            return
        self._flush_detail()
        self._load_status(nxt)

    def _select_id(self, status_id):
        iid = str(status_id)
        if not self.trips.exists(iid):
            return None
        self.trips.selection_set(iid)
        self.trips.see(iid)
        self.trips.focus(iid)
        return self._status_by_iid(iid)

    def _load_status(self, status):
        self.current = status
        self._apply_meta(status)
        body = status.get("body") or ""
        self.body.delete("1.0", "end")
        self.body.insert("1.0", body)
        self._update_body_count()
        self._tag_rows = [
            _norm_tag(t) for t in (status.get("tags") or [])
            if _norm_tag(t)["key"] and _norm_tag(t)["key"] not in MANAGED_TAG_SET
        ]
        self._show_dubi_checks()
        self._refresh_extra_tree()
        self._sync_dubi_buttons()
        self._refresh_edges()
        self._refresh_line_color()
        self._refresh_operator_field()
        self._refresh_line_field()
        self._refresh_boarding()
        self._refresh_vehicle_fields()

    def _shown(self, status):
        """Anzeige-Kopie mit lokalem Einstieg und effektiver Linie.

        Ohne Patch dasselbe Objekt.
        """
        if status is None:
            return None
        return lp.preview_status(
            bp.preview_status(status, self.boarding_patches), self.line_patches
        )

    def _matches_filter(self, status, query):
        blob = _search_blob(self._shown(status))
        api = _station_name((status.get("checkin") or {}).get("origin") or {})
        return query in blob or query in api.lower()

    def _apply_meta(self, status):
        shown = self._shown(status)
        bits = _checkin_bits(shown)
        self.title_var.set(f"{bits['line']} · {bits['origin']} → {bits['dest']}")
        dep, arr = bits["dep"], bits["arr"]
        dep_dt, arr_dt = _local_dt(dep), _local_dt(arr)
        arr_text = (
            arr_dt.strftime("%H:%M") if dep_dt and arr_dt
            and arr_dt.date() == dep_dt.date() else _fmt_when(arr)
        )
        when = f"{_fmt_when(dep)} → {arr_text}"
        day = weekday_label(dep)
        parts = [f"{day} {when}" if day else when]
        if operator_of(status):
            parts.append(operator_of(status))
        dist = ((status.get("checkin") or {}).get("distance") or 0) / 1000.0
        if dist:
            parts.append(f"{dist:.1f} km".replace(".", ","))
        self.meta_var.set(" · ".join(parts))

    def _on_body_changed(self):
        self._update_body_count()
        self._flush_detail()

    def _update_body_count(self):
        n = len(self.body.get("1.0", "end-1c"))
        self.body_count.set(f"{n}/{BODY_MAX}")

    def _show_dubi_checks(self):
        """Checkboxen aus den Tags, ohne sie umzuschreiben."""
        if not hasattr(self, "dubi_start_var"):
            return
        start, ende = build_dashboard.dubi_flags(self._tag_rows)
        self._ignore_dubi = True
        try:
            self.dubi_start_var.set(bool(start))
            self.dubi_ende_var.set(bool(ende))
        finally:
            self._ignore_dubi = False

    def _sync_dubi_buttons(self):
        if not hasattr(self, "dubi_start_btn"):
            return
        state = "normal" if self.current is not None and not self._busy else "disabled"
        self.dubi_start_btn.configure(state=state)
        self.dubi_ende_btn.configure(state=state)

    def _on_dubi_changed(self):
        if self._ignore_dubi or self.current is None or self._busy:
            return
        original = self._original_tags.get(self.current.get("id")) or []
        self._tag_rows = apply_dubi_checks(
            self._tag_rows,
            bool(self.dubi_start_var.get()),
            bool(self.dubi_ende_var.get()),
            original=original,
        )
        self._refresh_extra_tree()
        self._flush_detail()

    def _refresh_extra_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(self._tag_rows):
            if _dubi_kind(t):
                continue
            self.tree.insert(
                "", "end", iid=str(i),
                values=(t["key"], t["value"], VISIBILITY_LABEL.get(t["visibility"], t["visibility"])),
            )

    def _suggestion_keys(self):
        extra = []
        seen = set()

        def add(k):
            if (
                k and k not in seen and k not in MANAGED_TAG_SET
                and _dubi_kind({"key": k, "value": ""}) is None
            ):
                extra.append(k)
                seen.add(k)

        for t in self._tag_rows:
            add(t.get("key") or "")
        for s in self.statuses:
            for t in s.get("tags") or []:
                add((t.get("key") or "").strip())
        return extra

    def _add_tag(self):
        if self.current is None:
            return
        dlg = TagDialog(self.root, "Tag hinzufügen", extra_keys=self._suggestion_keys())
        if not dlg.result:
            return
        key = dlg.result["key"]
        if any(t["key"] == key for t in self._tag_rows):
            messagebox.showerror(
                "Tag", f"Schlüssel {key!r} ist bereits vergeben.", parent=self.root
            )
            return
        self._tag_rows.append(dlg.result)
        self._refresh_extra_tree()
        self._flush_detail()

    def _edit_tag(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        dlg = TagDialog(
            self.root, "Tag bearbeiten",
            extra_keys=self._suggestion_keys(),
            initial=self._tag_rows[idx],
        )
        if not dlg.result:
            return
        new_key = dlg.result["key"]
        for i, t in enumerate(self._tag_rows):
            if i != idx and t["key"] == new_key:
                messagebox.showerror(
                    "Tag", f"Schlüssel {new_key!r} ist bereits vergeben.", parent=self.root
                )
                return
        self._tag_rows[idx] = dlg.result
        self._refresh_extra_tree()
        self._flush_detail()

    def _delete_tag(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        del self._tag_rows[idx]
        self._refresh_extra_tree()
        self._flush_detail()

    LINE_SOURCE_LABEL = {"override": "lokal", "merge": "Merge", "tag": "Tag", "": "HAFAS"}

    def _refresh_line_field(self):
        if not hasattr(self, "line_apply_btn"):
            return
        has = self.current is not None
        if not has:
            self.line_var.set("")
            self._line_src_var.set("")
        else:
            line, _op, source = lp.effective_line(self.current, self.line_patches)
            self.line_var.set(line)
            self._line_src_var.set(self.LINE_SOURCE_LABEL[source])
        state = "normal" if has else "disabled"
        self.line_apply_btn.configure(state=state)
        self.line_reset_btn.configure(state=state)

    def _refresh_operator_field(self):
        if not hasattr(self, "operator_apply_btn"):
            return
        has = self.current is not None
        if not has:
            self.operator_field_var.set("")
            self._operator_src_var.set("")
        else:
            self.operator_field_var.set(operator_of(self.current))
            if lp.tag_operator(self.current):
                src = "Tag"
            elif lp.api_line_and_operator(self.current)[1]:
                src = "HAFAS"
            else:
                src = "fehlt"
            self._operator_src_var.set(src)
        names = {operator_of(s) for s in self.statuses}
        names |= {lp.api_line_and_operator(s)[1] for s in self.statuses}
        names.discard("")
        self.operator_field.configure(values=sorted(names, key=str.casefold))
        state = "normal" if has else "disabled"
        self.operator_apply_btn.configure(state=state)
        self.operator_reset_btn.configure(state=state)
        self._refresh_operator_scope()

    def _refresh_operator_scope(self):
        if not hasattr(self, "_operator_scope_var"):
            return
        n = len(self.trips.selection()) if hasattr(self, "trips") else 0
        self._operator_scope_var.set(
            f"Übernehmen/Zurücksetzen gilt für {n} markierte Fahrten" if n > 1 else "")

    def _set_operator(self, name):
        """walita:operator für alle markierten Fahrten; leer oder API-Name entfernt ihn."""
        if self.current is None:
            return
        targets = self._selected_statuses()
        name = (name or "").strip()
        if len(targets) > 1:
            question = (
                f"Betreiber „{name}“ für {len(targets)} Fahrten setzen?" if name
                else f"Betreiber-Tag von {len(targets)} Fahrten entfernen?")
            if not messagebox.askyesno("Betreiber", question, parent=self.root):
                return
        self._flush_detail()
        changed = 0
        for s in targets:
            api = lp.api_line_and_operator(s)[1]
            value = name if name and name != api else ""
            if lp.tag_operator(s) == value:
                continue
            set_table_tag(s, KEY_OPERATOR, value)
            self._refresh_trip_row(s)
            changed += 1
        self._fill_filter_choices()
        self._load_status(self.current)
        self._refresh_nav_counts()
        self._update_footer()
        self._set_status(
            f"Betreiber für {changed} Fahrten vorgemerkt; Upload mit Speichern."
            if changed else "Betreiber unverändert.")

    def _set_line(self, name):
        """Override für die aktuelle Fahrt; leer oder HAFAS-Name entfernt ihn."""
        if self.current is None or not self._config_ok(self.line_patches_path, "Linie"):
            return
        # Frisch lesen: war die Datei beim Start ungültig, ist der Speicher leer.
        self.line_patches = self._read_line_patches()
        name = (name or "").strip()
        sid = self.current.get("id")
        hafas = lp.line_and_operator(self.current)[0]
        if name and name != hafas:
            lp.set_override(self.line_patches, sid, name)
        else:
            lp.clear_override(self.line_patches, sid)
            # Ein schon hochgeladener Tag darf beim Zurücklesen nicht wieder
            # als Override zählen, solange er bei Träwelling noch steht.
            lp.mark_cleared(self.line_patches, sid)
        if not lp.save_patches(self.line_patches_path, self.line_patches):
            messagebox.showerror(
                "Linie", "line_patches.json nicht schreibbar.", parent=self.root)
            return
        line, _op, source = lp.effective_line(
            self.current, self.line_patches, use_tag=False)
        set_table_tag(self.current, KEY_LINE, line if source == "override" else "")
        self._apply_meta(self.current)
        self._refresh_line_field()
        self._refresh_trip_row(self.current)
        self._refresh_nav_counts()
        self._update_footer()
        self._set_status("Linie gespeichert (lokal); Tag beim nächsten Speichern.")

    def _refresh_line_color(self):
        if not hasattr(self, "_color_swatch"):
            return
        empty = LineColorDialog._EMPTY
        bg, _fg, patched = lcp.effective_colors(
            self.current, self.line_color_patches
        )
        has = self.current is not None
        if has and bg:
            self._color_swatch.configure(bg="#" + bg)
            self._color_hex_var.set("#" + bg)
            self._color_src_var.set("lokal" if patched else "HAFAS")
        else:
            self._color_swatch.configure(bg=empty)
            self._color_hex_var.set("—")
            self._color_src_var.set("" if not has else "keine")
        self.color_pick_btn.configure(state="normal" if has else "disabled")
        self.color_reset_btn.configure(
            state="normal" if has and patched else "disabled"
        )

    def _pick_line_color(self):
        if not self._config_ok(self.line_color_patches_path, "Linienfarbe"):
            return
        if self.current is None:
            return
        bg, _fg, _patched = lcp.effective_colors(
            self.current, self.line_color_patches
        )
        bits = _checkin_bits(self.current)
        dlg = LineColorDialog(self.root, bits["line"], initial_bg=bg)
        if not dlg.result:
            return
        sid = self.current.get("id")
        if not lcp.set_color(self.line_color_patches, sid, dlg.result):
            messagebox.showerror(
                "Linienfarbe", "Ungültige Farbe.", parent=self.root
            )
            return
        if not lcp.save_patches(
            self.line_color_patches_path, self.line_color_patches
        ):
            messagebox.showerror(
                "Linienfarbe",
                "line_color_patches.json nicht schreibbar.",
                parent=self.root,
            )
            return
        self._refresh_line_color()
        self._refresh_trip_row(self.current)
        self._set_status("Linienfarbe gespeichert (lokal).")

    def _reset_line_color(self):
        if not self._config_ok(self.line_color_patches_path, "Linienfarbe"):
            return
        if self.current is None:
            return
        lcp.clear_color(self.line_color_patches, self.current.get("id"))
        if not lcp.save_patches(
            self.line_color_patches_path, self.line_color_patches
        ):
            messagebox.showerror(
                "Linienfarbe",
                "line_color_patches.json nicht schreibbar.",
                parent=self.root,
            )
            return
        self._refresh_line_color()
        self._refresh_trip_row(self.current)
        self._set_status("Linienfarbe zurückgesetzt (lokal).")

    def _refresh_boarding(self):
        if not hasattr(self, "_board_var"):
            return
        status = self.current
        if status is None:
            self._board_var.set("—")
            self._board_src_var.set("")
            if hasattr(self, "board_pick_btn"):
                self.board_pick_btn.configure(state="disabled")
                self.board_reset_btn.configure(state="disabled")
            return
        shown = self._shown(status)
        name = _station_name((shown.get("checkin") or {}).get("origin") or {})
        api = _station_name((status.get("checkin") or {}).get("origin") or {})
        patched = shown is not status
        self._board_var.set(name)
        if patched:
            self._board_src_var.set(f"lokal · laut Träwelling: {api}")
        else:
            self._board_src_var.set("laut Träwelling")
        if not hasattr(self, "board_pick_btn") or self._busy:
            return
        can = bool(bp.candidate_stopovers(status))
        self.board_pick_btn.configure(state="normal" if can else "disabled")
        self.board_reset_btn.configure(
            state="normal" if patched else "disabled"
        )

    def _save_boarding(self):
        if bp.save_patches(self.boarding_patches_path, self.boarding_patches):
            return True
        messagebox.showerror(
            "Einstieg",
            "boarding_patches.json nicht schreibbar.",
            parent=self.root,
        )
        return False

    def _after_boarding_change(self, msg):
        if self.current is not None:
            self._refresh_trip_row(self.current)
            self._apply_meta(self.current)
            self._refresh_edges()
        self._refresh_boarding()
        self._set_status(msg)

    def _pick_boarding(self):
        if not self._config_ok(self.boarding_patches_path, "Einstieg"):
            return
        if self.current is None or self._busy:
            return
        if not bp.candidate_stopovers(self.current):
            messagebox.showinfo(
                "Einstieg",
                "Für diese Fahrt lässt sich der Einstieg nicht ändern.",
                parent=self.root,
            )
            return
        api_so = bp.stopover_id(
            ((self.current.get("checkin") or {}).get("origin") or {}).get("stopoverId")
        )
        selected = bp.override_stopover(self.boarding_patches, self.current)
        if selected is None:
            selected = api_so
        dlg = BoardingDialog(self.root, self.current, selected)
        if dlg.result is None:
            return
        sid = self.current.get("id")
        if bp.stopover_id(dlg.result) == api_so:
            bp.clear_origin(self.boarding_patches, sid)
            saved_msg = "Einstieg zurückgesetzt (lokal)."
        elif not bp.set_origin(self.boarding_patches, sid, dlg.result):
            messagebox.showerror(
                "Einstieg", "Ungültiger Halt.", parent=self.root
            )
            return
        else:
            saved_msg = "Einstieg gespeichert (lokal)."
        if not self._save_boarding():
            return
        self._after_boarding_change(saved_msg)

    def _reset_boarding(self):
        if not self._config_ok(self.boarding_patches_path, "Einstieg"):
            return
        if self.current is None or self._busy:
            return
        bp.clear_origin(self.boarding_patches, self.current.get("id"))
        if not self._save_boarding():
            return
        self._after_boarding_change("Einstieg zurückgesetzt (lokal).")

    def _refresh_edges(self):
        if not hasattr(self, "edges"):
            return
        self.edges.delete(*self.edges.get_children())
        self._edge_rows = []
        if not self.current:
            return
        sid = self.current.get("id")
        splits = sp.split_rules(self.station_patches)
        for a, b in ep.consecutive_pairs(
            dl.traveled_stopovers(self._shown(self.current))
        ):
            a_id, b_id = a.get("id"), b.get("id")
            kind = ep.patch_kind(self.patches, sid, a_id, b_id)
            via = ep.resolve_via(self.patches, sid, a_id, b_id) if kind else []
            self._edge_rows.append({
                "from_id": a_id,
                "to_id": b_id,
                "from_name": a.get("name") or ep.station_name(
                    {}, a_id, _station_name(a)
                ),
                "to_name": b.get("name") or ep.station_name(
                    {}, b_id, _station_name(b)
                ),
                "kind": kind,
                "via": via,
                "split": (a_id, b_id) in splits or (b_id, a_id) in splits,
            })
        for i, row in enumerate(self._edge_rows):
            self.edges.insert(
                "", "end", iid=str(i),
                values=(row["from_name"], row["to_name"], _edge_patch_label(row)),
            )
        kids = self.edges.get_children()
        if kids:
            self.edges.selection_set(kids[0])
            self.edges.focus(kids[0])
        self._sync_edge_menu()

    def _selected_edge(self):
        sel = self.edges.selection() if hasattr(self, "edges") else ()
        if not sel:
            return None
        try:
            idx = int(sel[0])
        except (TypeError, ValueError):
            return None
        if idx < 0 or idx >= len(self._edge_rows):
            return None
        return self._edge_rows[idx]

    def _split_edge_station(self):
        """Aufteilungs-Dialog mit den Enden der gewählten Kante."""
        row = self._selected_edge()
        if row is None:
            messagebox.showinfo(
                "Stationen", "Bitte zuerst eine Kante in der Liste wählen.",
                parent=self.root,
            )
            return
        es.ask_split(
            self.root, self.settings_ctx,
            on_saved=lambda: self._reload_visible_page("settings:stations"),
            edge=(row["from_id"], row["to_id"]),
        )

    def _fetch_station(self, station_id, on_ok):
        """Station per API in stations.json holen, dann `on_ok()` im UI-Thread."""
        def work():
            return fetch_station_into_file(self.token, station_id, self.stations_path)

        def done(entry):
            if entry is None:
                messagebox.showerror(
                    "Stationen",
                    f"Station {station_id} ist bei Träwelling unbekannt oder ohne "
                    "Koordinaten.",
                    parent=self.root,
                )
                return
            on_ok()

        self._run_async(work, done, busy_msg=f"Lade Station {station_id} …")

    def _load_stations_file(self):
        try:
            with open(self.stations_path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            messagebox.showerror(
                "Stationen",
                f"{self.stations_path} nicht lesbar: {e}",
                parent=self.root,
            )
            return None
        return data if isinstance(data, dict) else {}

    def _on_patches_saved(self, patches):
        def apply():
            self.patches = patches
            self._refresh_edges()
            self._apply_filter()
            self._reload_visible_page("settings:edges")
            self._set_status("Kanten-Patch gespeichert (lokal).")
        try:
            self.root.after(0, apply)
        except tk.TclError:
            pass

    def _on_station_patches_saved(self, patches):
        def apply():
            self.station_patches = patches
            self._reload_visible_page("settings:stations")
            self._set_status("Stations-Patch gespeichert (lokal).")
        try:
            self.root.after(0, apply)
        except tk.TclError:
            pass

    def _config_ok(self, path, title):
        """False samt Meldung, wenn `path` kein gültiges JSON ist (dann nie schreiben)."""
        err = es.check_config_file(path)
        if err is None:
            return True
        messagebox.showerror(
            title, err + "\n\nDie Datei bleibt unverändert; bitte von Hand korrigieren.",
            parent=self.root,
        )
        return False

    def _reload_visible_page(self, key):
        if self.ui_state.get("page") == key:
            self.settings_pages[key.split(":", 1)[1]].reload()

    def _open_station_map(self):
        if not (self._config_ok(self.station_patches_path, "Stationen")
                and self._config_ok(self.edge_patches_path, "Stationen")):
            return
        stations = self._load_stations_file()
        if stations is None:
            messagebox.showerror(
                "Stationen",
                f"Keine stations.json unter {self.stations_path}. "
                "Erst einen Export mit Stationen machen.",
                parent=self.root,
            )
            return
        self.station_patches = sp.load_patches(self.station_patches_path)
        self.patches = ep.load_patches(self.edge_patches_path)
        if self._station_server is None:
            self._station_server = sp.StationMapService(
                self.station_patches_path, on_saved=self._on_station_patches_saved
            )
            if not self._station_server.start():
                self._station_server = None
                messagebox.showerror(
                    "Stationen",
                    f"Port {sp.PATCH_MAP_PORT} ist belegt. "
                    "Anderen Prozess beenden und erneut versuchen.",
                    parent=self.root,
                )
                return
        self._station_server.set_data(
            stations=stations,
            statuses=self.statuses,
            edge_patches=self.patches,
            patches=self.station_patches,
        )
        webbrowser.open(
            "%s?t=%s" % (
                self._station_server.url().rstrip("/"),
                int(datetime.datetime.now().timestamp() * 1000),
            )
        )
        self._set_status("Stations-Karte im Browser geöffnet.")

    def _open_edge_map(self):
        if self.current is None:
            return
        row = self._selected_edge()
        if row is None:
            messagebox.showinfo(
                "Kanten", "Bitte zuerst eine Kante in der Liste wählen.",
                parent=self.root,
            )
            return
        self._launch_edge_map(self.current, row["from_id"], row["to_id"])

    def _launch_edge_map(self, status, from_id, to_id):
        if not (self._config_ok(self.edge_patches_path, "Kanten")
                and self._config_ok(self.station_patches_path, "Kanten")):
            return
        stations = self._load_stations_file()
        if stations is None:
            messagebox.showerror(
                "Kanten",
                f"Keine stations.json unter {self.stations_path}. "
                "Erst einen Export mit Stationen machen.",
                parent=self.root,
            )
            return
        self.station_patches = sp.load_patches(self.station_patches_path)
        stations = sp.apply_to_stations(self.station_patches, stations)
        self.patches = ep.load_patches(self.edge_patches_path)
        if self._patch_server is None:
            self._patch_server = ep.PatchMapService(
                self.edge_patches_path, on_saved=self._on_patches_saved
            )
            if not self._patch_server.start():
                self._patch_server = None
                messagebox.showerror(
                    "Kanten",
                    f"Port {ep.PATCH_MAP_PORT} ist belegt. "
                    "Anderen Prozess beenden und erneut versuchen.",
                    parent=self.root,
                )
                return
        names = {
            so.get("id"): _station_name(so)
            for so in dl.traveled_stopovers(self._shown(status))
        }
        served = ep.served_station_ids(dl.traveled_stopovers(status))
        self._patch_server.set_edge(
            status_id=status.get("id"),
            from_id=from_id,
            to_id=to_id,
            from_name=names.get(from_id) or ep.station_name(stations, from_id),
            to_name=names.get(to_id) or ep.station_name(stations, to_id),
            served_ids=served,
            stations=stations,
            patches=self.patches,
            seen_days=ep.seen_dates(self.statuses),
        )
        webbrowser.open(
            "%s?from=%s&to=%s&t=%s" % (
                self._patch_server.url().rstrip("/"),
                from_id, to_id,
                int(datetime.datetime.now().timestamp() * 1000),
            )
        )
        self._set_status("Patch-Karte im Browser geöffnet.")

    def _clear_edge_default(self):
        if not self._config_ok(self.edge_patches_path, "Kanten"):
            return
        row = self._selected_edge()
        if row is None:
            return
        ep.clear_default(self.patches, row["from_id"], row["to_id"])
        if not ep.save_patches(self.edge_patches_path, self.patches):
            messagebox.showerror(
                "Kanten", "edge_patches.json nicht schreibbar.", parent=self.root
            )
            return
        self._refresh_edges()
        self._refresh_current_row()
        self._set_status("Standard-Patch gelöscht.")

    def _clear_edge_override(self):
        if not self._config_ok(self.edge_patches_path, "Kanten"):
            return
        if self.current is None:
            return
        row = self._selected_edge()
        if row is None:
            return
        ep.clear_override(
            self.patches, self.current.get("id"), row["from_id"], row["to_id"]
        )
        if not ep.save_patches(self.edge_patches_path, self.patches):
            messagebox.showerror(
                "Kanten", "edge_patches.json nicht schreibbar.", parent=self.root
            )
            return
        self._refresh_edges()
        self._refresh_current_row()
        self._set_status("Fahrt-Override gelöscht.")

    def _col_at(self, event):
        region = self.trips.identify("region", event.x, event.y)
        if region != "cell":
            return None, None
        row = self.trips.identify_row(event.y)
        col_id = self.trips.identify_column(event.x)
        if not row or not col_id:
            return None, None
        try:
            idx = int(col_id.replace("#", "")) - 1
        except ValueError:
            return None, None
        if idx < 0 or idx >= len(TRIP_COLS):
            return None, None
        return row, TRIP_COLS[idx]

    def _on_trip_click(self, event):
        # Strg/Umschalt erweitern die Auswahl und starten keine Zellbearbeitung.
        if self._busy or event.state & 0x0005:
            return
        row, col = self._col_at(event)
        if col in EDIT_COLS and row:
            self.root.after_idle(lambda r=row, c=col: self._begin_edit(r, c))

    def _on_f2(self, _event):
        sel = self.trips.selection()
        if not sel:
            return "break"
        self._begin_edit(sel[0], "loc")
        return "break"

    def _begin_edit(self, iid, col):
        if self._busy or col not in EDIT_COLS:
            return
        if self._edit_entry is not None:
            if self._edit_iid == iid and self._edit_col == col:
                return
            self._commit_edit()
        if not self.trips.exists(iid):
            return
        self.trips.see(iid)
        self.trips.selection_set(iid)
        bbox = self.trips.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        value = self.trips.set(iid, col)
        entry = tk.Entry(self.trips, font=tkfont.nametofont("TkDefaultFont"))
        entry.place(x=x, y=y, width=max(w, 40), height=h)
        entry.insert(0, value)
        entry.select_range(0, "end")
        entry.focus_set()
        self._edit_entry = entry
        self._edit_iid = iid
        self._edit_col = col
        entry.bind("<Return>", lambda _e: self._commit_edit(move="down") or "break")
        entry.bind("<KP_Enter>", lambda _e: self._commit_edit(move="down") or "break")
        entry.bind("<Tab>", lambda _e: self._commit_edit(move="tab") or "break")
        entry.bind("<Shift-Tab>", lambda _e: self._commit_edit(move="back") or "break")
        entry.bind("<ISO_Left_Tab>", lambda _e: self._commit_edit(move="back") or "break")
        entry.bind("<Escape>", lambda _e: self._cancel_edit() or "break")
        entry.bind("<FocusOut>", self._on_edit_focus_out)

    def _on_edit_focus_out(self, _event):
        if self._edit_closing:
            return
        self.root.after_idle(self._commit_edit)

    def _cancel_edit(self):
        if self._edit_entry is None:
            return
        self._edit_closing = True
        self._edit_entry.destroy()
        self._edit_entry = None
        self._edit_iid = None
        self._edit_col = None
        self._edit_closing = False
        self.trips.focus_set()

    def _commit_edit(self, move=None):
        if self._edit_entry is None or self._edit_closing:
            return
        self._edit_closing = True
        value = self._edit_entry.get()
        iid = self._edit_iid
        col = self._edit_col
        self._edit_entry.destroy()
        self._edit_entry = None
        self._edit_iid = None
        self._edit_col = None
        self._edit_closing = False

        status = self._status_by_iid(iid)
        if status is not None and col in COL_TO_KEY:
            set_table_tag(status, COL_TO_KEY[col], value)
            self._refresh_trip_row(status)
            if self.current is not None and self.current.get("id") == status.get("id"):
                self._refresh_vehicle_fields()
            self._refresh_nav_counts()
            self._update_footer()

        if move:
            self.root.after_idle(lambda: self._move_edit(iid, col, move))

    def _move_edit(self, iid, col, move):
        kids = list(self.trips.get_children())
        if not kids or iid not in kids:
            return
        idx = kids.index(iid)
        next_iid, next_col = iid, col
        if move == "down":
            if idx + 1 < len(kids):
                next_iid = kids[idx + 1]
        elif move == "tab":
            if col == "loc":
                next_col = "veh"
            elif idx + 1 < len(kids):
                next_iid = kids[idx + 1]
                next_col = "loc"
            else:
                return
        elif move == "back":
            if col == "veh":
                next_col = "loc"
            elif idx > 0:
                next_iid = kids[idx - 1]
                next_col = "veh"
            else:
                return
        else:
            return
        self._begin_edit(next_iid, next_col)

    def _ask_reload_since(self):
        """Datum für „Seit Datum“; None bei Abbruch."""
        initial = (self.ui_state.get("reload_since") or self.since
                   or (datetime.date.today() - datetime.timedelta(days=30)).isoformat())
        while True:
            dlg = es.FieldsDialog(self.root, "Von API laden – seit Datum",
                                  [("Fahrten nach (JJJJ-MM-TT)", initial, [])])
            if not dlg.result:
                return None
            day = parse_day(dlg.result[0])
            if day:
                self.ui_state["reload_since"] = day.isoformat()
                return day.isoformat()
            messagebox.showerror("Von API laden", "Bitte ein Datum JJJJ-MM-TT angeben.",
                                 parent=self.root)
            initial = dlg.result[0]

    def _reload_from_api(self, mode="normal"):
        """Lädt Fahrten neu.

        normal: ab neuester bekannter Fahrt minus INCREMENTAL_OVERLAP_DAYS,
            ältere bleiben (mit --limit oder ohne lokale Fahrten: alles).
        full: alle Fahrten (--since und --limit gelten weiter).
        since: Fahrten nach dem gewählten Tag, ältere bleiben.
        """
        if self._busy:
            return
        self._commit_edit()
        if self._any_dirty():
            if not messagebox.askyesno(
                "Von API laden",
                "Ungespeicherte Änderungen gehen verloren. Fortfahren?",
                parent=self.root,
            ):
                return
        existing = list(self.statuses)
        since = self.since or None
        cutoff = None
        if mode == "since":
            since = self._ask_reload_since()
            if not since:
                return
            day = datetime.datetime.strptime(since, "%Y-%m-%d")
            cutoff = day.astimezone()
        elif mode == "normal" and not self.limit:
            cutoff = dl.incremental_cutoff(existing)
            if cutoff is None:
                mode = "full"
        else:
            mode = "full"

        def work():
            fetched = list(dl.iter_statuses(
                self.username, self.token, limit=None if cutoff else self.limit,
                since=since, stop_before=cutoff,
            ))
            trips_path = os.path.join(os.path.dirname(self.statuses_path), "trips.json")
            attach_local_trips(fetched, existing, dl.load_trip_cache(
                trips_path, statuses_path=self.statuses_path))
            if mode == "since":
                return fetched, merge_since(fetched, existing, since)
            if mode == "normal":
                return fetched, dl.merge_incremental(fetched, existing)
            return fetched, fetched

        def done(result):
            fetched, statuses = result
            if mode == "full":
                msg = f"{len(statuses)} Fahrten von der API geladen."
            else:
                scope = f"nach {since}" if mode == "since" else "neue + letzte 2 Tage"
                msg = (f"{len(fetched)} Fahrten von der API geladen ({scope}), "
                       f"{len(statuses)} insgesamt.")
            self._after_reload(statuses, msg)

        self._run_async(work, done, busy_msg="Lade Fahrten von der API …")

    def _after_reload(self, statuses, msg=None):
        self.statuses[:] = statuses
        self.current = None
        self._fill_filter_choices()
        self._reset_baselines()
        self._apply_filter()
        kids = self.trips.get_children()
        if kids:
            self.trips.selection_set(kids[0])
            self._on_select()
        page = self.ui_state.get("page", "")
        if page.startswith("settings:"):
            self._reload_visible_page(page)
        self._set_status(msg or f"{len(statuses)} Fahrten von der API geladen.")
        self._reconcile_lines()

    def _dirty_statuses(self):
        self._commit_edit()
        self._flush_detail()
        return [s for s in self.statuses if self._status_dirty(s)]

    def _save(self, then=None):
        if self._busy:
            return
        dirty = self._dirty_statuses()
        if not dirty:
            self._set_status("Nichts zu speichern.")
            if then:
                then()
            return
        for s in dirty:
            body = s.get("body") or ""
            if len(body) > BODY_MAX:
                messagebox.showerror(
                    "Status-Text",
                    f"Fahrt {s.get('id')}: Text ist {len(body)} Zeichen "
                    f"(Maximum {BODY_MAX}).",
                    parent=self.root,
                )
                return

        xfer = TransferWindow(
            self.root,
            [(s.get("id"), _trip_short(s)) for s in dirty],
        )
        self._xfer = xfer

        def work():
            ok = []
            errors = []
            for s in dirty:
                sid = s.get("id")
                self.root.after(0, lambda sid=sid: xfer.set_row(sid, "running"))
                try:
                    updated = apply_status_edits(
                        self.token, sid, s.get("body") or "", s.get("tags") or [],
                    )
                    ok.append((sid, updated))
                    self.root.after(
                        0, lambda sid=sid, u=updated: self._apply_saved_status(sid, u, xfer)
                    )
                except Exception as e:
                    errors.append((sid, e))
                    msg = format_api_error(e)
                    self.root.after(
                        0,
                        lambda sid=sid, msg=msg: self._xfer_fail(xfer, sid, msg),
                    )
            return ok, errors

        def done(result):
            ok, errors = result
            merges = {}
            for sid, updated in ok:
                new_body = updated.get("body") or ""
                new_tags = [
                    _norm_tag(t) for t in (updated.get("tags") or []) if t.get("key")
                ]
                merges[sid] = (new_body, new_tags)
            local_n = 0
            local_err = None
            if merges:
                try:
                    local_n = merge_local_body_tags_many(self.statuses_path, merges)
                except OSError as e:
                    local_err = e
                    local_n = -1
                    for sid, _ in ok:
                        xfer.set_row(sid, "local", f"Server ok, Datei nicht: {e}")
            bits = [f"{len(ok)}/{len(dirty)} Fahrten gespeichert"]
            if local_n > 0:
                bits.append(f"lokal {local_n} in {self.statuses_path}")
            if local_err:
                bits.append(f"lokal nicht geschrieben ({local_err})")
            if errors:
                bits.append(f"{len(errors)} Fehler")
            summary = "; ".join(bits) + "."
            self._after_save_done()
            self._set_status(summary)
            if xfer.winfo_exists():
                xfer.finish(summary)
            self._xfer = None
            if then and not errors:
                then()

        self._run_async(work, done, busy_msg="Speichere nach Träwelling …")

    def _apply_saved_status(self, sid, updated, xfer):
        """Übernimmt eine erfolgreich gesendete Fahrt in die Tabelle (UI-Thread)."""
        new_body = updated.get("body") or ""
        new_tags = [
            _norm_tag(t) for t in (updated.get("tags") or []) if t.get("key")
        ]
        for s in self.statuses:
            if s.get("id") == sid:
                s["body"] = new_body
                s["tags"] = new_tags
                self._baseline[sid] = _snapshot_status(s)
                self._original_tags[sid] = list(new_tags)
                self._refresh_trip_row(s)
                if self.current is not None and self.current.get("id") == sid:
                    self._load_status(s)
                break
        if xfer.winfo_exists():
            xfer.set_row(sid, "ok")
            xfer.bump()

    def _xfer_fail(self, xfer, sid, msg):
        if xfer.winfo_exists():
            xfer.set_row(sid, "error", msg)
            xfer.bump()

    def _on_rebuild_dashboard(self):
        self._commit_edit()
        if self._any_dirty():
            ans = messagebox.askyesnocancel(
                "Dashboard neu bauen",
                "Es gibt ungespeicherte Änderungen, die nicht in der Datei stehen.\n\n"
                "Ja: zuerst nach Träwelling speichern, dann Dashboard bauen.\n"
                "Nein: Dashboard aus der vorhandenen Datei bauen.\n"
                "Abbrechen: nichts tun.",
                parent=self.root,
            )
            if ans is None:
                return
            if ans:
                self._save(then=self._build_dashboard)
                return
        self._build_dashboard()

    def _build_dashboard(self):
        if not os.path.isfile(self.statuses_path):
            messagebox.showerror(
                "Dashboard",
                f"Keine Status-Datei {self.statuses_path}. "
                "Erst speichern oder einen Export machen.",
                parent=self.root,
            )
            return
        argv = [
            "--statuses", self.statuses_path,
            "--stations", self.stations_path,
            "-o", self.dashboard_path,
            "--open",
            "--loc-class-families", self.loc_class_families,
            "--edge-patches", self.edge_patches_path,
            "--station-patches", self.station_patches_path,
            "--line-color-patches", self.line_color_patches_path,
            "--home-region", self.home_region_path,
            "--boarding-patches", self.boarding_patches_path,
            "--vehicle-roster", self.vehicle_roster_path,
            "--operator-line-patches", self.operator_line_patches_path,
            "--line-patches", self.line_patches_path,
        ]
        if self.ignore_plus:
            argv.append("--ignore-plus")

        def work():
            rc = build_dashboard.main(argv)
            return rc

        def done(rc):
            if rc:
                self._set_status(f"Dashboard-Bau fehlgeschlagen (Exit {rc}).")
                messagebox.showerror(
                    "Dashboard",
                    f"build_dashboard endete mit Code {rc}.",
                    parent=self.root,
                )
            else:
                self._set_status(f"Dashboard geschrieben: {self.dashboard_path}")

        self._run_async(work, done, busy_msg="Baue Dashboard …")

    def _on_close(self):
        if self._busy:
            return
        self._commit_edit()
        if self._any_dirty():
            ans = messagebox.askyesnocancel(
                "Beenden",
                "Ungespeicherte Änderungen. Jetzt nach Träwelling speichern?",
                parent=self.root,
            )
            if ans is None:
                return
            if ans:
                self._save(then=self._close_window)
                return
        self._close_window()

    def _close_window(self):
        self._save_ui_state()
        self.root.destroy()


def parse_args(argv=None):
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
        help="Personal Access Token (sonst Env TRWL_TOKEN). Braucht write-statuses.",
    )
    parser.add_argument(
        "--scale", type=float, default=None,
        help="Zoom der Oberfläche, z.B. 1.5 (Default: zuletzt gewählter Wert, "
             "beim ersten Start die Systemskalierung). Im Editor: Strg +/−/0.",
    )
    parser.add_argument(
        "--login", action="store_true",
        help="OAuth-Login im Browser erzwingen (Scopes read-statuses write-statuses).",
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
        help="OAuth-Redirect-URL, muss exakt zum registrierten Client passen.",
    )
    parser.add_argument(
        "--manual", action="store_true",
        help="OAuth-Code manuell einfügen statt lokalem Callback-Server.",
    )
    parser.add_argument(
        "--oauth-token-file", default="data/oauth_token.json",
        help="Ablageort des OAuth-Tokens (Default: data/oauth_token.json).",
    )
    parser.add_argument(
        "--scopes", default=EDITOR_SCOPES,
        help=f"OAuth-Scopes (Default: {EDITOR_SCOPES}).",
    )
    parser.add_argument(
        "--statuses", default="data/statuses.json",
        help="Lokale Status-Datei zum Anzeigen/Mergen (Default: data/statuses.json).",
    )
    parser.add_argument(
        "--stations", default="data/stations.json",
        help="stations.json fürs Dashboard-Neu-Bauen (Default: data/stations.json).",
    )
    parser.add_argument(
        "--dashboard-output", default="data/dashboard.html",
        help="Zieldatei fürs Dashboard (Default: data/dashboard.html).",
    )
    parser.add_argument(
        "--loc-class-families", default="data/loc_class_families.txt",
        help="Baureihe→Familie fürs Dashboard (Default: data/loc_class_families.txt).",
    )
    parser.add_argument(
        "--ignore-plus", action="store_true",
        help="Wagennummern-Tags beim Dashboard-Bau nicht am '+' trennen.",
    )
    parser.add_argument(
        "--edge-patches", default="data/edge_patches.json",
        help="Lokale Kanten-Patches (Default: data/edge_patches.json).",
    )
    parser.add_argument(
        "--station-patches", default="data/station_patches.json",
        help="Lokale Stations-Patches (Default: data/station_patches.json).",
    )
    parser.add_argument(
        "--line-color-patches", default="data/line_color_patches.json",
        help="Lokale Linienfarben-Patches (Default: data/line_color_patches.json).",
    )
    parser.add_argument(
        "--home-region", default="data/home_region.json",
        help="Lokale Operator-Liste der Heimatregion (Default: data/home_region.json).",
    )
    parser.add_argument(
        "--boarding-patches", default="data/boarding_patches.json",
        help="Lokale Einstiegs-Patches (Default: data/boarding_patches.json).",
    )
    parser.add_argument(
        "--vehicle-roster", default="data/vehicle_roster.json",
        help="Lokaler Fuhrpark je Baureihe (Default: data/vehicle_roster.json).",
    )
    parser.add_argument(
        "--operator-line-patches", default="data/operator_line_patches.json",
        help="Operator einer Linie überschreiben (Default: data/operator_line_patches.json).",
    )
    parser.add_argument(
        "--line-patches", default="data/line_patches.json",
        help="Liniennamen je Fahrt und Merges (Default: data/line_patches.json).",
    )
    parser.add_argument(
        "--operator-replacements", default="data/operator_replacements.json",
        help="Betreibernamen Rohname → kanonisch (Default: data/operator_replacements.json).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max. Anzahl Statuses beim Laden von der API.",
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", default="",
        help="Nur Statuses mit Abfahrt strikt nach diesem Tag (API-Laden).",
    )
    return parser.parse_args(argv)


def resolve_token(args):
    """PAT oder OAuth; gleicher Ablauf wie download_statuses.main."""
    client_secret = os.environ.get("TRWL_CLIENT_SECRET")
    if args.token and not args.login:
        return args.token
    return auth.get_access_token(
        args.oauth_token_file, args.client_id, redirect=args.redirect_uri,
        scopes=args.scopes, secret=client_secret, force_login=args.login,
        manual=args.manual,
    )


def main(argv=None):
    args = parse_args(argv)

    if args.logout:
        return dl.main(["--logout", "--oauth-token-file", args.oauth_token_file])

    if args.since:
        try:
            datetime.datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            log("Fehler: --since erwartet ein Datum im Format YYYY-MM-DD.")
            return 2

    try:
        token = resolve_token(args)
    except auth.AuthError as e:
        log(f"OAuth-Login fehlgeschlagen: {e}")
        return 1
    if not token:
        log("Fehler: Kein Token. Setze TRWL_TOKEN, nutze --token oder --login.")
        return 2

    try:
        username = dl.get_username(token)
    except dl.ApiError as e:
        log(f"Authentifizierung fehlgeschlagen: {e}")
        return 1
    log(f"Angemeldet als: {username}")

    statuses = load_local_statuses(args.statuses)
    if statuses is None:
        log("Keine lokale statuses.json – lade Liste von der API …")
        try:
            statuses = list(dl.iter_statuses(
                username, token, limit=args.limit, since=args.since or None
            ))
        except dl.ApiError as e:
            log(f"Abruf der Statuses fehlgeschlagen: {e}")
            return 1
        log(f"{len(statuses)} Statuses von der API geladen.")
    else:
        log(f"{len(statuses)} Statuses aus {args.statuses} geladen.")

    _enable_dpi_awareness()
    root = tk.Tk()
    EditorApp(
        root, token, username, statuses, args.statuses,
        limit=args.limit, since=args.since,
        stations_path=args.stations,
        dashboard_path=args.dashboard_output,
        loc_class_families=args.loc_class_families,
        ignore_plus=args.ignore_plus,
        edge_patches_path=args.edge_patches,
        station_patches_path=args.station_patches,
        line_color_patches_path=args.line_color_patches,
        home_region_path=args.home_region,
        boarding_patches_path=args.boarding_patches,
        vehicle_roster_path=args.vehicle_roster,
        operator_line_patches_path=args.operator_line_patches,
        operator_replacements_path=args.operator_replacements,
        line_patches_path=args.line_patches,
        zoom=args.scale, detect=True,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Abgebrochen.", file=sys.stderr, flush=True)
        sys.exit(130)
