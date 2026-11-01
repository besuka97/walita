#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Einstellungsseiten des Tag-Editors: je Konfig-Datei eine Tabellenseite."""

import json
import os
import re
import tkinter as tk
from tkinter import messagebox, ttk

import boarding_patches as bp
import download_statuses as dl
import edge_patches as ep
import home_region as hr
import line_color_patches as lcp
import line_patches as lp
import loc_class_families as lcf
import operator_line_patches as olp
import operator_replacements as orp
import station_patches as sp
import vehicle_roster as vr

# Benannte Schriften; der Editor legt sie an und skaliert sie mit dem Zoom.
FONT_SECTION = "WalitaSection"
FONT_TITLE = "WalitaTitle"
FONT_HEADING = "WalitaHeading"
FONT_BOLD = "WalitaBold"


def check_config_file(path, pairs=False):
    """Fehlertext, wenn `path` kein gültiges JSON-Objekt ist, sonst None.

    Fehlende Datei gilt als gültig (leere Seite). `pairs=True` liest mit
    `object_pairs_hook`, damit doppelte Schlüssel kein Fehler sind.
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            if pairs:
                data = json.load(f, object_pairs_hook=list)
                ok = isinstance(data, list)
            else:
                data = json.load(f)
                ok = isinstance(data, dict)
    except (OSError, ValueError) as e:
        return f"{os.path.basename(path)} nicht lesbar: {e}"
    if not ok:
        return f"{os.path.basename(path)}: erwartet ein JSON-Objekt."
    return None


def _line(status):
    v = ((status or {}).get("checkin") or {}).get("lineName")
    return v.strip() if isinstance(v, str) else ""


def _operator(status):
    """Betreiber aus walita:operator, sonst aus dem Check-in."""
    return lp.line_and_operator(status or {})[1]


def operators_in(statuses):
    return sorted({_operator(s) for s in statuses if _operator(s)}, key=str.casefold)


def lines_in(statuses):
    return sorted({_line(s) for s in statuses if _line(s)}, key=str.casefold)


def line_rule_matches(rules, statuses):
    return [
        sum(1 for s in statuses if _line(s) == line and _operator(s) == operator)
        for line, operator, _name in rules
    ]


def sort_value(value):
    """Sortierschlüssel: Zahlen numerisch vor Text, Text ohne Groß/klein."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, value, "")
    return (1, 0, str(value).casefold())


class SettingsContext:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TablePage(ttk.Frame):
    """Kopf, Suche, Tabelle, Knöpfe. Unterklassen füllen die Hooks."""

    pairs = False
    can_add = True

    def __init__(self, parent, ctx, title, path, effect, columns, headings,
                 widths=None):
        super().__init__(parent)
        self.ctx = ctx
        self.path = path
        self.read_only = False
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text=title, font=FONT_HEADING).pack(anchor="w")
        ttk.Label(head, text=f"{path} · {effect}").pack(anchor="w")
        self.error_var = tk.StringVar()
        ttk.Label(head, textvariable=self.error_var, foreground="#c01c28",
                  wraplength=700).pack(anchor="w")
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(6, 4))
        ttk.Label(bar, text="🔍").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._fill())
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=30)
        self.search_entry.pack(side="left", padx=4)
        table = ttk.Frame(self)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=columns, show="headings",
                                 selectmode="extended")
        self._sort = (columns[0], False)
        for c in columns:
            self.tree.heading(c, text=headings[c], command=lambda c=c: self._sort_by(c))
            self.tree.column(c, width=(widths or {}).get(c, 160))
        ys = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tree.tag_configure("orphan", foreground="#888888")
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Delete>", lambda _e: self._delete_selected())
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=6)
        self.buttons = []
        if self.can_add:
            self._btn(btns, "Hinzufügen", self._add)
        self._btn(btns, "Bearbeiten", self._edit_selected)
        self._btn(btns, "Löschen", self._delete_selected)
        self.orphan_btn = self._btn(btns, "Verwaiste entfernen", self._delete_orphans)
        self.extra_buttons(btns)
        self.columns = columns

    def _btn(self, parent, text, cmd, side="left"):
        b = ttk.Button(parent, text=text, command=cmd)
        b.pack(side=side, padx=(0, 6))
        self.buttons.append(b)
        return b

    def _on_double(self, event):
        """Doppelklick auf eine Zeile bearbeitet; Kopf und Leerraum nicht."""
        if self.tree.identify_region(event.x, event.y) in ("cell", "tree"):
            self._edit_selected()

    def _count(self, iids):
        return len(iids)

    def _ordered(self, items, values_of):
        """Einträge einer Gruppe nach der gewählten Spalte sortieren."""
        col, desc = self._sort
        idx = self.columns.index(col) if col in self.columns else 0
        return sorted(items, key=lambda it: sort_value(values_of(it)[idx]), reverse=desc)

    # Hooks
    def load(self):
        raise NotImplementedError

    def rows(self):
        return []

    def add(self):
        pass

    def edit(self, iid):
        pass

    def delete(self, iids):
        pass

    def orphans(self):
        return set()

    def extra_buttons(self, frame):
        pass

    # Ablauf
    def reload(self):
        err = check_config_file(self.path, pairs=self.pairs)
        self.read_only = err is not None
        self.error_var.set(
            (err + " – Datei bleibt unverändert, bitte von Hand korrigieren.") if err else "")
        if not err:
            try:
                self.load()
            except ValueError as e:
                self.read_only = True
                self.error_var.set(str(e))
        self.set_busy(False)
        self._fill()

    def set_busy(self, busy):
        state = "disabled" if busy or self.read_only else "normal"
        for b in self.buttons:
            b.configure(state=state)

    def _fill(self):
        q = (self.search_var.get() or "").strip().casefold()
        self.tree.delete(*self.tree.get_children())
        if self.read_only:
            return
        orphan = self.orphans()
        col, desc = self._sort
        idx = self.columns.index(col)
        rows = [r for r in self.rows()
                if not q or q in " ".join(str(v) for v in r[1]).casefold()]
        rows.sort(key=lambda r: sort_value(r[1][idx]), reverse=desc)
        for iid, values, tags in rows:
            tags = tuple(tags) + (("orphan",) if iid in orphan else ())
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)
        self.orphan_btn.configure(
            state="normal" if orphan and not self.read_only else "disabled")

    def _sort_by(self, col):
        c, desc = self._sort
        self._sort = (col, not desc if c == col else False)
        self._fill()

    def _changed(self, msg):
        self.ctx.set_status(msg)
        self.ctx.after_change(self.__class__.__name__)
        self._fill()

    def _add(self):
        if not self.read_only:
            self.add()

    def _edit_selected(self):
        sel = self.tree.selection()
        if sel and not self.read_only:
            self.edit(sel[0])

    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel or self.read_only:
            return
        if messagebox.askyesno("Löschen", f"{self._count(sel)} Eintrag/Einträge löschen?",
                               parent=self):
            self.delete(list(sel))

    def _delete_orphans(self):
        orphan = sorted(self.orphans())
        if orphan and messagebox.askyesno(
                "Verwaiste entfernen", f"{len(orphan)} verwaiste Einträge löschen?",
                parent=self):
            self.delete(orphan)

    def save_failed(self):
        messagebox.showerror("Speichern", f"{self.path} nicht schreibbar.", parent=self)

class FieldsDialog(tk.Toplevel):
    """Modaler Dialog mit Comboboxen; result = Liste der Werte oder None."""

    def __init__(self, master, title, fields, allow_empty=False):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        self.allow_empty = allow_empty
        self.vars = []
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        for row, (label, value, choices) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=value)
            box = ttk.Combobox(frame, textvariable=var, values=choices, width=40)
            box.grid(row=row, column=1, sticky="we", pady=3, padx=(8, 0))
            if row == 0:
                box.focus_set()
            self.vars.append(var)
        btns = ttk.Frame(frame)
        btns.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window(self)

    def _ok(self):
        values = [v.get().strip() for v in self.vars]
        if not self.allow_empty and not all(values):
            messagebox.showerror("Eingabe", "Bitte alle Felder ausfüllen.", parent=self)
            return
        self.result = values
        self.destroy()

class ReplacementsPage(TablePage):
    def __init__(self, parent, ctx):
        super().__init__(
            parent, ctx, "Betreibernamen", ctx.paths["replacements"],
            "wirkt beim nächsten Export (Rohname aus der API → Name in statuses.json)",
            ("raw", "canonical"), {"raw": "Rohname", "canonical": "Kanonischer Name"},
            widths={"raw": 320, "canonical": 320})
        self.doc = {"comments": [], "pairs": []}

    def load(self):
        self.doc = orp.load_document(self.path)

    def rows(self):
        return [(str(i), (raw, canon), ()) for i, (raw, canon) in enumerate(self.doc["pairs"])]

    def _dialog(self, raw="", canon=""):
        names = operators_in(self.ctx.statuses)
        dlg = FieldsDialog(self, "Betreibername",
                           [("Rohname", raw, names), ("Kanonischer Name", canon, names)])
        return dlg.result

    def _store(self, pairs, msg):
        doc = {"comments": self.doc["comments"], "pairs": pairs}
        if not orp.save_document(self.path, doc):
            self.save_failed()
            return
        self.doc = doc
        self._changed(msg)

    def add(self):
        res = self._dialog()
        if res:
            pairs = [p for p in self.doc["pairs"] if p[0] != res[0]] + [tuple(res)]
            self._store(pairs, f"Betreibername gespeichert: {res[0]} → {res[1]}.")

    def edit(self, iid):
        raw, canon = self.doc["pairs"][int(iid)]
        res = self._dialog(raw, canon)
        if res:
            self.replace_pair(int(iid), tuple(res))

    def replace_pair(self, index, pair):
        """Ersetzt Eintrag `index`; ein anderer mit demselben Rohnamen fällt weg."""
        pairs = [p for i, p in enumerate(self.doc["pairs"])
                 if i != index and p[0] != pair[0]]
        pairs.insert(min(index, len(pairs)), pair)
        self._store(pairs, "Betreibername geändert.")

    def delete(self, iids):
        drop = {int(i) for i in iids}
        self._store([p for i, p in enumerate(self.doc["pairs"]) if i not in drop],
                    f"{len(drop)} Betreibername(n) gelöscht.")

class LineRulesPage(TablePage):
    def __init__(self, parent, ctx):
        super().__init__(
            parent, ctx, "Betreiber je Linie", ctx.paths["line_rules"],
            "wirkt beim nächsten Dashboard-Bau",
            ("line", "operator", "name", "count"),
            {"line": "Linie", "operator": "Betreiber", "name": "Neuer Betreiber",
             "count": "Fahrten"},
            widths={"line": 90, "operator": 260, "name": 260, "count": 70})
        self.doc = {"comments": [], "rules": []}

    def load(self):
        self.doc = olp.load_document(self.path)

    def rows(self):
        counts = line_rule_matches(self.doc["rules"], self.ctx.statuses)
        return [(str(i), (l, o, n, counts[i]), ())
                for i, (l, o, n) in enumerate(self.doc["rules"])]

    def orphans(self):
        counts = line_rule_matches(self.doc["rules"], self.ctx.statuses)
        return {str(i) for i, c in enumerate(counts) if c == 0}

    def _dialog(self, rule=("", "", "")):
        ops = operators_in(self.ctx.statuses)
        dlg = FieldsDialog(self, "Betreiber je Linie", [
            ("Linie", rule[0], lines_in(self.ctx.statuses)),
            ("Betreiber", rule[1], ops),
            ("Neuer Betreiber", rule[2], ops),
        ])
        return tuple(dlg.result) if dlg.result else None

    def _store(self, rules, msg):
        if not olp.save_rules(self.path, rules, comments=self.doc["comments"]):
            self.save_failed()
            return
        self.doc = {"comments": self.doc["comments"], "rules": rules}
        self._changed(msg)

    def add(self):
        res = self._dialog()
        if res:
            self._store(self.doc["rules"] + [res], f"Regel für Linie {res[0]} gespeichert.")

    def edit(self, iid):
        res = self._dialog(self.doc["rules"][int(iid)])
        if res:
            rules = list(self.doc["rules"])
            rules[int(iid)] = res
            self._store(rules, "Regel geändert.")

    def delete(self, iids):
        drop = {int(i) for i in iids}
        self._store([r for i, r in enumerate(self.doc["rules"]) if i not in drop],
                    f"{len(drop)} Regel(n) gelöscht.")


class LinesPage(TablePage):
    """Linien aus den Fahrten (nach Override), Merges und Tag-Übertragung."""

    can_add = False

    def __init__(self, parent, ctx):
        super().__init__(
            parent, ctx, "Linien", ctx.paths["lines"],
            "wirkt beim nächsten Dashboard-Bau; Tags beim Speichern",
            ("line", "operator", "count", "target", "label"),
            {"line": "Linie", "operator": "Betreiber", "count": "Fahrten",
             "target": "Zusammengeführt in", "label": "Kartenname"},
            widths={"line": 110, "operator": 240, "count": 70, "target": 280,
                    "label": 120})
        self.patches = lp.empty_patches()

    @staticmethod
    def iid_of(key):
        return json.dumps(list(key), ensure_ascii=False)

    @staticmethod
    def key_of(iid):
        line, op = json.loads(iid)
        return line, op

    def load(self):
        self.patches = lp.load_patches(self.path)

    def _counts(self):
        return lp.line_counts(self.ctx.statuses, self.patches)

    def rows(self):
        counts = self._counts()
        aliases = lp.alias_map(self.patches)
        labels = self.patches["labels"]
        keys = set(counts) | set(self.patches["merges"]) | set(labels)
        out = []
        for key in sorted(keys):
            dest = aliases.get(key)
            target = "→ " + " · ".join(p for p in dest if p) if dest else ""
            out.append((self.iid_of(key),
                        (key[0], key[1], counts.get(key, 0), target,
                         labels.get(key, "")),
                        ("orphan",) if dest else ()))
        return out

    def orphans(self):
        counts = self._counts()
        keys = set(self.patches["merges"]) | set(self.patches["labels"])
        return {self.iid_of(k) for k in keys if not counts.get(k)}

    def _store(self, msg):
        if not lp.save_patches(self.path, self.patches):
            self.save_failed()
            return False
        self._changed(msg)
        return True

    def merge_keys(self, keys, dest):
        """Führt `keys` in `dest` zusammen; Ziel selbst verliert seinen Merge.

        Ergäbe das einen Zyklus, bleibt die Datei unverändert (False).
        """
        trial = dict(self.patches, merges=dict(self.patches["merges"]))
        for k in keys:
            if k == dest:
                lp.clear_merge(trial, dest)
            else:
                lp.set_merge(trial, k, dest)
        aliases = lp.alias_map(trial)
        if any(k != dest and k not in aliases for k in keys):
            self.ctx.set_status(
                f"Nicht zusammengeführt: {dest[0]} · {dest[1]} ergäbe einen Kreis.")
            return False
        self.patches = trial
        n = sum(1 for k in keys if k != dest)
        return self._store(f"{n} Linie(n) in {dest[0]} · {dest[1]} zusammengeführt.")

    def _merge_selected(self):
        sel = list(self.tree.selection())
        if not sel or self.read_only:
            return
        keys = [self.key_of(i) for i in sel]
        dlg = FieldsDialog(self, "Zusammenführen", [
            ("Ziel-Linie", keys[0][0], sorted({k[0] for k in keys}, key=str.casefold)),
            ("Ziel-Betreiber", keys[0][1], sorted({k[1] for k in keys}, key=str.casefold)),
        ])
        if dlg.result:
            self.merge_keys(keys, (dlg.result[0], dlg.result[1]))

    def edit(self, iid):
        key = self.key_of(iid)
        dest = self.patches["merges"].get(key) or key
        dlg = FieldsDialog(self, "Zusammenführen", [
            ("Ziel-Linie", dest[0], []), ("Ziel-Betreiber", dest[1], []),
        ])
        if dlg.result:
            self.merge_keys([key], (dlg.result[0], dlg.result[1]))

    def _set_label(self):
        sel = list(self.tree.selection())
        if not sel or self.read_only:
            return
        keys = [self.key_of(i) for i in sel]
        current = self.patches["labels"].get(keys[0], "")
        dlg = FieldsDialog(self, "Kartenname (leer = Linienname)",
                           [("Kartenname", current, [])], allow_empty=True)
        if dlg.result is None:
            return
        label = dlg.result[0].strip()
        for k in keys:
            lp.set_label(self.patches, k, label)
        what = f"„{label}“" if label else "entfernt"
        self._store(f"Kartenname {what} für {len(keys)} Linie(n).")

    def delete(self, iids):
        """Löst Merges; gespiegelte Tags mit dem alten Ziel werden entfernt.

        Zeilen ohne Merge verlieren ihren Kartennamen.
        """
        aliases = lp.alias_map(self.patches)
        n_merges = n_tags = n_labels = 0
        for iid in iids:
            key = self.key_of(iid)
            if key not in self.patches["merges"]:
                if key in self.patches["labels"]:
                    lp.set_label(self.patches, key, "")
                    n_labels += 1
                continue
            dest = aliases.get(key)
            lp.clear_merge(self.patches, key)
            n_merges += 1
            if dest:
                for sid in self.ctx.clear_line_tags(key, dest[0]):
                    lp.mark_cleared(self.patches, sid)
                    n_tags += 1
        msg = f"{n_merges} Merge(s) gelöst."
        if n_labels:
            msg += f" {n_labels} Kartenname(n) entfernt."
        if n_tags:
            msg += f" walita:line auf {n_tags} Fahrt(en) zum Löschen vorgemerkt."
        self._store(msg)

    def stage_tags(self, iids):
        n = sum(self.ctx.stage_line_tags(self.key_of(i)) for i in iids
                if self.key_of(i) in self.patches["merges"])
        self.ctx.set_status(f"walita:line auf {n} Fahrt(en) vorgemerkt – Speichern lädt hoch.")
        return n

    def extra_buttons(self, frame):
        self._btn(frame, "Zusammenführen …", self._merge_selected)
        self._btn(frame, "Kartenname …", self._set_label)
        self._btn(frame, "Als Tag auf Fahrten übertragen",
                  lambda: self.stage_tags(list(self.tree.selection())))
        self._btn(frame, "Fahrten zeigen", self._show_trips)

    def _show_trips(self):
        sel = self.tree.selection()
        if sel:
            self.ctx.filter_trips(self.key_of(sel[0]))


def _tag(status, key):
    for t in (status or {}).get("tags") or []:
        if t.get("key") == key and t.get("value") not in (None, ""):
            return str(t["value"]).strip()
    return ""


def ridden_numbers(statuses):
    """{Baureihe: {Nummer: Fahrten}}; Nummern getrennt an `,`, `;`, `+`."""
    out = {}
    for s in statuses:
        loc = _tag(s, "trwl:locomotive_class")
        if not loc:
            continue
        for n in re.split(r"[,;+]", _tag(s, "trwl:vehicle_number")):
            n = n.strip()
            if n:
                per = out.setdefault(loc, {})
                per[n] = per.get(n, 0) + 1
    return out


def ride_counts_by_operator(statuses):
    out = {}
    for s in statuses:
        name = hr.operator_name(s)
        out[name] = out.get(name, 0) + 1
    return out


def _page_head(frame, title, path, effect):
    ttk.Label(frame, text=title, font=FONT_HEADING).pack(anchor="w")
    ttk.Label(frame, text=f"{path} · {effect}", foreground="#666666").pack(anchor="w")
    error_var = tk.StringVar()
    ttk.Label(frame, textvariable=error_var, foreground="#c01c28",
              wraplength=760).pack(anchor="w")
    return error_var


class _WithdrawnDateDialog(tk.Toplevel):
    """Optionales Ausmusterungsdatum. result = YYYY-MM-DD, \"\" oder None."""

    def __init__(self, master, initial):
        super().__init__(master)
        self.title("Ausmusterungsdatum")
        self.transient(master)
        self.result = None
        self.resizable(False, False)

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm, text="Datum (JJJJ-MM-TT). Leer lassen, wenn es unbekannt ist.",
        ).pack(anchor="w")
        self.var = tk.StringVar(value=initial or "")
        entry = ttk.Entry(frm, textvariable=self.var, width=16)
        entry.pack(anchor="w", pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        entry.focus_set()
        entry.selection_range(0, "end")

    def _ok(self):
        text = self.var.get().strip()
        if not text:
            self.result = ""
            self.destroy()
            return
        parsed = vr.parse_withdrawn_on(text)
        if not parsed:
            messagebox.showerror(
                "Ausmusterungsdatum",
                "Bitte JJJJ-MM-TT angeben oder das Feld leer lassen.",
                parent=self,
            )
            return
        self.result = parsed
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class MasterDetailPage(ttk.Frame):
    """Kopf, links Gruppen (Name, Anzahl), rechts Einträge der Gruppe."""

    pairs = False

    def __init__(self, parent, ctx, title, path, effect, master_heading,
                 columns, headings, widths=None):
        super().__init__(parent)
        self.ctx = ctx
        self.path = path
        self.read_only = False
        self.error_var = _page_head(self, title, path, effect)
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(6, 4))
        ttk.Label(bar, text="Suche").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.fill_master())
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=30)
        self.search_entry.pack(side="left", padx=4)
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, weight=1)
        panes.add(right, weight=2)
        self.master_tree = ttk.Treeview(
            left, columns=("name", "count"), show="headings", selectmode="browse"
        )
        self.master_tree.heading("name", text=master_heading)
        self.master_tree.heading("count", text="Anzahl")
        self.master_tree.column("name", width=240)
        self.master_tree.column("count", width=70, anchor="e")
        self.master_tree.pack(fill="both", expand=True)
        self.master_tree.tag_configure("orphan", foreground="#888888")
        self.master_tree.bind("<<TreeviewSelect>>", lambda _e: self._fill_detail())
        self.master_btns = ttk.Frame(left)
        self.master_btns.pack(fill="x", pady=6)
        table = ttk.Frame(right)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=columns, show="headings",
                                 selectmode="extended")
        for c in columns:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=(widths or {}).get(c, 140))
        ys = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tree.tag_configure("orphan", foreground="#888888")
        self.tree.tag_configure("withdrawn", foreground="#888888")
        self.detail_btns = ttk.Frame(right)
        self.detail_btns.pack(fill="x", pady=6)
        self.buttons = []
        self.build_buttons()

    def btn(self, parent, text, cmd, side="left"):
        b = ttk.Button(parent, text=text,
                       command=lambda: None if self.read_only else cmd())
        b.pack(side=side, padx=(0, 6))
        self.buttons.append(b)
        return b

    def selected_master(self):
        sel = self.master_tree.selection()
        return sel[0] if sel else None

    def reload(self):
        err = check_config_file(self.path, pairs=self.pairs)
        self.read_only = err is not None
        self.error_var.set(
            (err + " – Datei bleibt unverändert, bitte von Hand korrigieren.")
            if err else ""
        )
        if not err:
            try:
                self.load()
            except ValueError as e:
                self.read_only = True
                self.error_var.set(str(e))
        self.set_busy(False)
        self.fill_master()

    def set_busy(self, busy):
        for b in self.buttons:
            b.configure(state="disabled" if busy or self.read_only else "normal")

    def fill_master(self, select=None):
        keep = select or self.selected_master()
        self.master_tree.delete(*self.master_tree.get_children())
        if self.read_only:
            self.tree.delete(*self.tree.get_children())
            return
        q = (self.search_var.get() or "").strip().casefold()
        for name, count, orphan in self.master_rows():
            if q and q not in name.casefold() and name != select:
                continue
            self.master_tree.insert("", "end", iid=name, values=(name, count),
                                    tags=("orphan",) if orphan else ())
        kids = self.master_tree.get_children()
        target = keep if keep in kids else (kids[0] if kids else None)
        if target:
            self.master_tree.selection_set(target)
            self.master_tree.see(target)
        self._fill_detail()

    def _fill_detail(self):
        self.tree.delete(*self.tree.get_children())
        name = self.selected_master()
        if name is None:
            return
        for iid, values, tags in self.detail_rows(name):
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)

    def save_failed(self):
        messagebox.showerror("Speichern", f"{self.path} nicht schreibbar.", parent=self)

    def ask_name(self, title, initial=""):
        dlg = FieldsDialog(self, title, [("Name", initial, [])])
        return dlg.result[0] if dlg.result else None

    def load(self):
        raise NotImplementedError

    def master_rows(self):
        return []

    def detail_rows(self, name):
        return []

    def build_buttons(self):
        pass


class RosterPage(MasterDetailPage):
    def __init__(self, parent, ctx):
        self.types = {}
        self.ridden = {}
        super().__init__(
            parent, ctx, "Fuhrpark", ctx.paths["roster"],
            "wirkt beim nächsten Dashboard-Bau (Fahrzeuge-Seite, Abdeckung)",
            "Baureihe", ("number", "state", "withdrawn_on", "rides"),
            {"number": "Nummer", "state": "Status",
             "withdrawn_on": "ausgemustert am", "rides": "gefahren"},
            widths={"number": 110, "state": 110, "withdrawn_on": 120, "rides": 80},
        )

    def build_buttons(self):
        self.btn(self.master_btns, "Hinzufügen", self._add_class)
        self.btn(self.master_btns, "Umbenennen", self._rename_class)
        self.btn(self.master_btns, "Löschen", self._delete_class)
        self.btn(self.detail_btns, "Ausgemustert ein/aus", self._toggle)
        self.btn(self.detail_btns, "Datum…", self._set_date)
        self.btn(self.detail_btns, "Löschen", self._delete_numbers)
        rng = ttk.Frame(self.detail_btns)
        rng.pack(side="right")
        self.from_var = tk.StringVar()
        self.to_var = tk.StringVar()
        self.step_var = tk.StringVar(value="1")
        for label, var, width in (("Von", self.from_var, 7), ("Bis", self.to_var, 7),
                                  ("Schritt", self.step_var, 4)):
            ttk.Label(rng, text=label).pack(side="left")
            ttk.Entry(rng, textvariable=var, width=width).pack(side="left", padx=(3, 6))
        self.btn(rng, "Nummern hinzufügen", self._add_range_from_fields)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Delete>", lambda _e: self._delete_numbers())

    def _on_double(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell":
            self._toggle()

    def load(self):
        self.types = {
            k: [dict(e) for e in v]
            for k, v in (vr.load_roster(self.path).get("types") or {}).items()
        }
        self.ridden = ridden_numbers(self.ctx.statuses)

    def master_rows(self):
        names = vr.collect_loc_classes(self.ctx.statuses, self.types.keys())
        return [(n, len(self.types.get(n, [])), False) for n in names]

    def detail_rows(self, name):
        rides = self.ridden.get(name, {})
        rows = []
        for e in self.types.get(name, []):
            n = e["number"]
            rows.append((
                n,
                (n, "ausgemustert" if e.get("withdrawn") else "aktiv",
                 e.get("withdrawnOn", ""), rides.get(n, 0)),
                ("withdrawn",) if e.get("withdrawn") else (),
            ))
        return rows

    def _store(self, msg, select=None):
        if not vr.save_roster(self.path, {"types": self.types}):
            self.save_failed()
            return False
        self.ctx.set_status(msg)
        self.ctx.after_change("roster")
        keep = list(self.tree.selection())
        self.fill_master(select)
        for iid in keep:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)
        return True

    def _add_class(self):
        name = self.ask_name("Baureihe hinzufügen")
        if name:
            self.types.setdefault(name, [])
            self.fill_master(select=name)
            self.ctx.set_status(
                f"Baureihe {name} angelegt; gespeichert wird sie mit der ersten Nummer."
            )

    def _rename_class(self):
        old = self.selected_master()
        if not old:
            return
        new = self.ask_name("Baureihe umbenennen", old)
        if not new or new == old:
            return
        entries = self.types.pop(old, [])
        self.types[new] = vr.add_numbers(self.types.get(new, []), entries)
        self._store(f"Baureihe {old} → {new}.", select=new)

    def _delete_class(self):
        name = self.selected_master()
        if not name or name not in self.types:
            return
        if messagebox.askyesno(
            "Fuhrpark", f"Alle {len(self.types[name])} Nummern von {name} löschen?",
            parent=self,
        ):
            del self.types[name]
            self._store(f"Baureihe {name} aus dem Fuhrpark entfernt.")

    def add_range(self, name, start, end, step):
        numbers = vr.expand_range(start, end, step)
        before = len(self.types.get(name, []))
        self.types[name] = vr.add_numbers(self.types.get(name, []), numbers)
        added = len(self.types[name]) - before
        self._store(
            f"{len(numbers)} Nummern in der Spanne, {added} neu ({name}).", select=name
        )

    def _add_range_from_fields(self):
        name = self.selected_master()
        if not name:
            messagebox.showerror("Fuhrpark", "Zuerst eine Baureihe wählen.", parent=self)
            return
        try:
            bounds = []
            for label, var in (("Von", self.from_var), ("Bis", self.to_var),
                               ("Schritt", self.step_var)):
                text = (var.get() or "").strip() or ("1" if label == "Schritt" else "")
                if not text.isdigit():
                    raise ValueError(f"{label} muss eine ganze Zahl ab 0 sein.")
                bounds.append(int(text))
            self.add_range(name, *bounds)
        except ValueError as exc:
            messagebox.showerror("Fuhrpark", str(exc), parent=self)

    def _selected_entries(self):
        name = self.selected_master()
        sel = set(self.tree.selection())
        return [e for e in self.types.get(name, []) if e["number"] in sel]

    def _toggle(self):
        entries = self._selected_entries()
        for e in entries:
            e["withdrawn"] = not e.get("withdrawn")
            if not e["withdrawn"]:
                e.pop("withdrawnOn", None)
        if entries:
            self._store(f"{len(entries)} Nummer(n) umgeschaltet.")

    def _set_date(self):
        entries = [e for e in self._selected_entries() if e.get("withdrawn")]
        if not entries:
            messagebox.showinfo(
                "Fuhrpark", "Ein Datum gibt es nur für ausgemusterte Nummern.",
                parent=self,
            )
            return
        dlg = _WithdrawnDateDialog(self, entries[0].get("withdrawnOn") or "")
        self.wait_window(dlg)
        if dlg.result is None:
            return
        for e in entries:
            if dlg.result:
                e["withdrawnOn"] = dlg.result
            else:
                e.pop("withdrawnOn", None)
        self._store("Ausmusterungsdatum gesetzt.")

    def _delete_numbers(self):
        name = self.selected_master()
        drop = set(self.tree.selection())
        if not name or not drop:
            return
        if messagebox.askyesno("Fuhrpark", f"{len(drop)} Nummer(n) löschen?",
                               parent=self):
            self.types[name] = [e for e in self.types[name] if e["number"] not in drop]
            self._store(f"{len(drop)} Nummer(n) gelöscht.", select=name)


class FamiliesPage(MasterDetailPage):
    pairs = True

    def __init__(self, parent, ctx):
        self.doc = {"comments": [], "pairs": []}
        self._pending = []
        super().__init__(
            parent, ctx, "Baureihenfamilien", ctx.paths["families"],
            "wirkt beim nächsten Dashboard-Bau (Kartenfilter Baureihe)",
            "Familie", ("loc", "rides"), {"loc": "Baureihe", "rides": "Fahrten"},
            widths={"loc": 260, "rides": 80},
        )

    def build_buttons(self):
        self.btn(self.master_btns, "Hinzufügen", self._add_family)
        self.btn(self.master_btns, "Umbenennen", self._rename_family)
        self.btn(self.master_btns, "Löschen", self._delete_family)
        self.btn(self.detail_btns, "Baureihe zuordnen…", self._add_member)
        self.btn(self.detail_btns, "Entfernen", self._remove_members)
        self.btn(self.detail_btns, "Verwaiste entfernen", self._remove_orphans)
        self.tree.bind("<Delete>", lambda _e: self._remove_members())

    def load(self):
        self.doc = lcf.load_document(self.path)

    def _rides(self):
        out = {}
        for s in self.ctx.statuses:
            loc = _tag(s, "trwl:locomotive_class")
            if loc:
                out[loc] = out.get(loc, 0) + 1
        return out

    def master_rows(self):
        fams = lcf.families(self.doc)
        names = list(fams) + [p for p in self._pending if p not in fams]
        return [(f, len(fams.get(f, [])), False) for f in names]

    def detail_rows(self, family):
        rides = self._rides()
        members = lcf.families(self.doc).get(family, [])
        return [(m, (m, rides.get(m, 0)), () if rides.get(m) else ("orphan",))
                for m in members]

    def _store(self, pairs, msg, select=None):
        doc = {"comments": self.doc["comments"], "pairs": pairs}
        if not lcf.save_document(self.path, doc):
            self.save_failed()
            return
        self.doc = doc
        self.ctx.set_status(msg)
        self.ctx.after_change("families")
        self.fill_master(select)

    def _add_family(self):
        name = self.ask_name("Familie hinzufügen")
        if name and name not in self._pending:
            self._pending.append(name)
            self.fill_master(select=name)
            self.ctx.set_status(
                f"Familie {name} angelegt; gespeichert wird sie mit der ersten Baureihe."
            )

    def assign(self, family, loc_class):
        if (loc_class, family) not in self.doc["pairs"]:
            pairs = self.doc["pairs"] + [(loc_class, family)]
            if family in self._pending:
                self._pending.remove(family)
            self._store(pairs, f"{loc_class} → Familie {family}.", select=family)

    def _add_member(self):
        family = self.selected_master()
        if not family:
            return
        dlg = FieldsDialog(self, "Baureihe zuordnen", [
            ("Baureihe", "", self.member_choices()),
        ])
        if dlg.result:
            self.assign(family, dlg.result[0])

    def member_choices(self):
        """Baureihen aus Fahrten und Fuhrpark für die Zuordnung."""
        roster = vr.load_roster(self.ctx.paths["roster"]).get("types") or {}
        return vr.collect_loc_classes(self.ctx.statuses, roster.keys())

    def _remove(self, family, members):
        pairs = [p for p in self.doc["pairs"]
                 if not (p[1] == family and p[0] in members)]
        self._store(pairs, f"{len(members)} Baureihe(n) aus {family} entfernt.",
                    select=family)

    def _remove_members(self):
        family = self.selected_master()
        members = set(self.tree.selection())
        if family and members:
            self._remove(family, members)

    def _remove_orphans(self):
        family = self.selected_master()
        if not family:
            return
        rides = self._rides()
        members = {m for m in lcf.families(self.doc).get(family, []) if not rides.get(m)}
        if members and messagebox.askyesno(
            "Verwaiste entfernen",
            f"{len(members)} Baureihe(n) ohne Fahrt aus {family} entfernen?",
            parent=self,
        ):
            self._remove(family, members)

    def _rename_family(self):
        old = self.selected_master()
        if not old:
            return
        new = self.ask_name("Familie umbenennen", old)
        if not new or new == old:
            return
        if old in self._pending:
            self._pending[self._pending.index(old)] = new
            self.fill_master(select=new)
            return
        pairs = []
        for loc, fam in self.doc["pairs"]:
            pair = (loc, new if fam == old else fam)
            if pair not in pairs:
                pairs.append(pair)
        self._store(pairs, f"Familie {old} → {new}.", select=new)

    def _delete_family(self):
        family = self.selected_master()
        if not family:
            return
        if family in self._pending:
            self._pending.remove(family)
            self.fill_master()
            return
        if messagebox.askyesno("Baureihenfamilien", f"Familie {family} löschen?",
                               parent=self):
            self._store([p for p in self.doc["pairs"] if p[1] != family],
                        f"Familie {family} gelöscht.")


class HomeRegionPage(TablePage):
    can_add = False

    def __init__(self, parent, ctx):
        self.selected = []
        super().__init__(
            parent, ctx, "Heimatregion", ctx.paths["home"],
            "wirkt beim nächsten Dashboard-Bau (Umschalter Heimatregion)",
            ("on", "operator", "rides"),
            {"on": "✓", "operator": "Betreiber", "rides": "Fahrten"},
            widths={"on": 40, "operator": 380, "rides": 80},
        )
        self.buttons[0].configure(text="Umschalten")
        self.buttons[1].configure(text="Abwählen")
        self.tree.bind("<space>", lambda _e: self._toggle(list(self.tree.selection())))
        self.tree.bind("<Button-1>", self._on_click, add="+")

    def extra_buttons(self, frame):
        self.only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="nur angehakte", variable=self.only_var,
                        command=self._fill).pack(side="right")

    def load(self):
        self.selected = list(hr.load_operators(self.path))

    def rows(self):
        counts = ride_counts_by_operator(self.ctx.statuses)
        rows = []
        for name in hr.collect_operator_names(self.ctx.statuses, self.selected):
            on = name in self.selected
            if self.only_var.get() and not on:
                continue
            rows.append(("op:" + name, (
                "✓" if on else "", name or "(ohne Betreiber)", counts.get(name, 0),
            ), ()))
        return rows

    def orphans(self):
        counts = ride_counts_by_operator(self.ctx.statuses)
        return {"op:" + n for n in self.selected if not counts.get(n)}

    def _on_double(self, event):
        if self.tree.identify_column(event.x) == "#1":
            return
        super()._on_double(event)

    def _on_click(self, event):
        if self.tree.identify_column(event.x) == "#1":
            iid = self.tree.identify_row(event.y)
            if iid:
                self._toggle([iid])

    def _toggle(self, iids):
        if self.read_only or not iids:
            return
        sel = list(self.selected)
        for iid in iids:
            name = iid[3:]
            if name in sel:
                sel.remove(name)
            else:
                sel.append(name)
        if not hr.save_operators(self.path, sel):
            self.save_failed()
            return
        self.selected = list(hr.load_operators(self.path))
        self._changed(f"Heimatregion: {len(self.selected)} Betreiber.")
        for iid in iids:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)

    def edit(self, iid):
        self._toggle(list(self.tree.selection()) or [iid])

    def delete(self, iids):
        self._toggle([i for i in iids if i[3:] in self.selected])


def load_stations(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _station(stations, sid):
    return stations.get(str(sid)) or stations.get(sid)


def station_label(stations, sid):
    st = _station(stations, sid)
    name = st.get("name") if isinstance(st, dict) else None
    return f"{name} ({sid})" if name else f"#{sid}"


def status_label(status):
    checkin = (status or {}).get("checkin") or {}
    dep = dl.local_day((checkin.get("origin") or {}).get("departurePlanned")) or ""
    day = ".".join(reversed(dep.split("-"))) if dep else ""
    return "%s %s %s → %s" % (
        day, checkin.get("lineName") or "",
        (checkin.get("origin") or {}).get("name") or "?",
        (checkin.get("destination") or {}).get("name") or "?",
    )


def find_status_with_edge(statuses, a, b, patches=None):
    """Eine Fahrt über a → b; bevorzugt eine ohne eigenen Override auf der Kante."""
    overrides = (patches or {}).get("overrides") or {}
    fallback = None
    for s in statuses:
        for x, y in ep.consecutive_pairs(dl.traveled_stopovers(s)):
            if x.get("id") == a and y.get("id") == b:
                if (s.get("id"), a, b) not in overrides:
                    return s
                fallback = fallback or s
                break
    return fallback


def move_distance_m(stations, sid, lat, lon):
    st = _station(stations, sid)
    if not isinstance(st, dict) or st.get("latitude") is None:
        return None
    return int(round(
        ep.haversine_km(st["latitude"], st["longitude"], lat, lon) * 1000
    ))


class _GroupedPage(TablePage):
    """Tabellenseite mit Gruppenknoten; Unterklassen überschreiben `_fill`."""

    can_add = False
    group_prefixes = ("g:", "h:")

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.tree.configure(show="tree headings")
        self.tree.column("#0", width=190, stretch=False)

    def _entries(self, iids):
        return [i for i in iids if not i.startswith(self.group_prefixes)]

    def _delete_selected(self):
        sel = self._entries(self.tree.selection())
        if not sel or self.read_only:
            return
        if messagebox.askyesno("Löschen", f"{self._count(sel)} Eintrag/Einträge löschen?",
                               parent=self):
            self.delete(sel)

    def _edit_selected(self):
        sel = self._entries(self.tree.selection())
        if sel and not self.read_only:
            self.edit(sel[0])


class ColorsPage(TablePage):
    can_add = False

    def __init__(self, parent, ctx):
        self.patches = lcp.empty_patches()
        super().__init__(
            parent, ctx, "Linienfarben", ctx.paths["colors"],
            "wirkt beim nächsten Dashboard-Bau",
            ("trip", "bg", "fg"),
            {"trip": "Fahrt", "bg": "Hintergrund", "fg": "Text"},
            widths={"trip": 420, "bg": 100, "fg": 80},
        )
        self.tree.configure(show="tree headings")
        self.tree.column("#0", width=240, stretch=False)

    def extra_buttons(self, frame):
        ttk.Button(frame, text="Zur Fahrt", command=self._goto).pack(side="right")

    def load(self):
        self.patches = lcp.load_patches(self.path)

    def _by_id(self):
        return {s.get("id"): s for s in self.ctx.statuses}

    def orphans(self):
        known = self._by_id()
        return {"s:%d" % sid for sid in self.patches["overrides"] if sid not in known}

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        if self.read_only:
            return
        q = (self.search_var.get() or "").strip().casefold()
        known = self._by_id()
        groups = {}
        for sid, (bg, fg) in self.patches["overrides"].items():
            st = known.get(sid)
            line = ((st or {}).get("checkin") or {}).get("lineName") or "?"
            op = _operator(st) if st else "?"
            label = status_label(st) if st else f"Fahrt {sid} (unbekannt)"
            if q and q not in f"{line} {op} {label}".casefold():
                continue
            groups.setdefault((line, op), []).append((sid, label, bg, fg))
        orphan = self.orphans()
        for (line, op), items in sorted(
            groups.items(), key=lambda kv: (kv[0][0].casefold(), kv[0][1].casefold())
        ):
            gid = "g:%s|%s" % (line, op)
            self.tree.insert("", "end", iid=gid, text=f"{line} · {op}", open=True)
            for sid, label, bg, fg in self._ordered(
                items, lambda x: (x[1], "#" + x[2], "#" + x[3])
            ):
                tag = "c%s%s" % (bg, fg)
                self.tree.tag_configure(tag, background="#" + bg, foreground="#" + fg)
                iid = "s:%d" % sid
                tags = ("orphan",) if iid in orphan else (tag,)
                self.tree.insert(gid, "end", iid=iid,
                                 values=(label, "#" + bg, "#" + fg), tags=tags)
        self.orphan_btn.configure(state="normal" if orphan else "disabled")

    def _count(self, iids):
        return len(self._sids(iids))

    def _sids(self, iids):
        out = []
        for iid in iids:
            if iid.startswith("g:"):
                out.extend(int(c[2:]) for c in self.tree.get_children(iid))
            elif iid.startswith("s:"):
                out.append(int(iid[2:]))
        return sorted(set(out))

    def edit(self, iid):
        sids = self._sids([iid])
        if not sids:
            return
        bg, _fg = self.patches["overrides"][sids[0]]
        label = self.tree.item(iid, "text") or self.tree.item(
            self.tree.parent(iid), "text") or "Linie"
        new_bg = self.ctx.pick_color(self, label.split(" · ")[0], bg)
        if not new_bg:
            return
        for sid in sids:
            lcp.set_color(self.patches, sid, new_bg)
        self._store(f"Farbe für {len(sids)} Fahrt(en) geändert.")

    def delete(self, iids):
        sids = self._sids(iids)
        for sid in sids:
            lcp.clear_color(self.patches, sid)
        self._store(f"{len(sids)} Linienfarbe(n) gelöscht.")

    def _store(self, msg):
        if not lcp.save_patches(self.path, self.patches):
            self.save_failed()
            return
        self._changed(msg)

    def _goto(self):
        sel = self.tree.selection()
        if sel and sel[0].startswith("s:"):
            self.ctx.goto_status(int(sel[0][2:]))


class BoardingPage(TablePage):
    can_add = False

    def __init__(self, parent, ctx):
        self.patches = bp.empty_patches()
        super().__init__(
            parent, ctx, "Einstiege", ctx.paths["boarding"],
            "wirkt beim nächsten Dashboard-Bau",
            ("trip", "api", "local"),
            {"trip": "Fahrt", "api": "Einstieg laut Träwelling",
             "local": "Einstieg lokal"},
            widths={"trip": 360, "api": 230, "local": 230},
        )

    def extra_buttons(self, frame):
        ttk.Button(frame, text="Zur Fahrt", command=self._goto).pack(side="right")

    def load(self):
        self.patches = bp.load_patches(self.path)

    def _resolve(self, sid, so_id):
        st = next((s for s in self.ctx.statuses if s.get("id") == sid), None)
        if st is None:
            return None, None, None
        api = ((st.get("checkin") or {}).get("origin") or {}).get("name") or "?"
        local = next(
            (c.get("name") for c in bp.candidate_stopovers(st)
             if bp.stopover_id(c.get("stopoverId")) == so_id),
            None,
        )
        return st, api, local

    def rows(self):
        out = []
        for sid, so_id in self.patches["overrides"].items():
            st, api, local = self._resolve(sid, so_id)
            label = status_label(st) if st else f"Fahrt {sid} (unbekannt)"
            out.append(("s:%d" % sid, (
                label, api or "?", local or f"Halt {so_id} (unbekannt)",
            ), ()))
        return out

    def orphans(self):
        bad = set()
        for sid, so_id in self.patches["overrides"].items():
            st, _api, local = self._resolve(sid, so_id)
            if st is None or local is None:
                bad.add("s:%d" % sid)
        return bad

    def edit(self, iid):
        sid = int(iid[2:])
        so_id = self.patches["overrides"][sid]
        st, _api, _local = self._resolve(sid, so_id)
        if st is None:
            return
        chosen = self.ctx.pick_boarding(self, st, so_id)
        if chosen is None:
            return
        api_so = bp.stopover_id(
            ((st.get("checkin") or {}).get("origin") or {}).get("stopoverId")
        )
        if bp.stopover_id(chosen) == api_so:
            bp.clear_origin(self.patches, sid)
        else:
            bp.set_origin(self.patches, sid, chosen)
        self._store("Einstieg geändert.")

    def delete(self, iids):
        for iid in iids:
            bp.clear_origin(self.patches, int(iid[2:]))
        self._store(f"{len(iids)} Einstieg(e) zurückgesetzt.")

    def _store(self, msg):
        if not bp.save_patches(self.path, self.patches):
            self.save_failed()
            return
        self._changed(msg)

    def _goto(self):
        sel = self.tree.selection()
        if sel:
            self.ctx.goto_status(int(sel[0][2:]))


class EdgesPage(_GroupedPage):
    def __init__(self, parent, ctx):
        self.patches = ep.empty_patches()
        self.stations = {}
        super().__init__(
            parent, ctx, "Kanten", ctx.paths["edges"],
            "wirkt beim nächsten Dashboard-Bau",
            ("edge", "via", "trip"),
            {"edge": "Von → Nach", "via": "Via", "trip": "Fahrt"},
            widths={"edge": 340, "via": 340, "trip": 260},
        )

    def extra_buttons(self, frame):
        self._btn(frame, "Auf Karte bearbeiten", lambda: self._map(), side="right")

    def load(self):
        self.patches = ep.load_patches(self.path)
        self.stations = load_stations(self.ctx.stations_path)

    def _via_text(self, via):
        if not via:
            return "aus"
        return ", ".join(station_label(self.stations, v).split(" (")[0] for v in via)

    def orphans(self):
        known_ids = {s.get("id") for s in self.ctx.statuses}
        bad = set()
        for (a, b) in self.patches["defaults"]:
            if _station(self.stations, a) is None or _station(self.stations, b) is None:
                bad.add("d:%d:%d" % (a, b))
        for (sid, a, b) in self.patches["overrides"]:
            unknown = _station(self.stations, a) is None or _station(self.stations, b) is None
            if sid not in known_ids or unknown:
                bad.add("o:%d:%d:%d" % (sid, a, b))
        return bad

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        if self.read_only:
            return
        q = (self.search_var.get() or "").strip().casefold()
        orphan = self.orphans()
        by_id = {s.get("id"): s for s in self.ctx.statuses}

        def edge(a, b):
            return "%s → %s" % (
                station_label(self.stations, a), station_label(self.stations, b)
            )

        self.tree.insert("", "end", iid="g:d", text="Standards", open=True)
        for (a, b), via in self._ordered(
            self.patches["defaults"].items(),
            lambda kv: (edge(*kv[0]), self._via_text(kv[1]), ""),
        ):
            vals = (edge(a, b), self._via_text(via), "")
            if q and q not in " ".join(vals).casefold():
                continue
            iid = "d:%d:%d" % (a, b)
            self.tree.insert("g:d", "end", iid=iid, values=vals,
                             tags=("orphan",) if iid in orphan else ())
        self.tree.insert("", "end", iid="g:o", text="Fahrt-Overrides", open=True)
        def ov_values(kv):
            (sid, a, b), via = kv
            st = by_id.get(sid)
            return (edge(a, b), self._via_text(via),
                    status_label(st) if st else f"Fahrt {sid} (unbekannt)")

        for (sid, a, b), via in self._ordered(self.patches["overrides"].items(), ov_values):
            st = by_id.get(sid)
            vals = (edge(a, b), self._via_text(via),
                    status_label(st) if st else f"Fahrt {sid} (unbekannt)")
            if q and q not in " ".join(vals).casefold():
                continue
            iid = "o:%d:%d:%d" % (sid, a, b)
            self.tree.insert("g:o", "end", iid=iid, values=vals,
                             tags=("orphan",) if iid in orphan else ())
        self.orphan_btn.configure(state="normal" if orphan else "disabled")

    def delete(self, iids):
        n = 0
        for iid in iids:
            parts = iid.split(":")
            if parts[0] == "d":
                ep.clear_default(self.patches, int(parts[1]), int(parts[2]))
                n += 1
            elif parts[0] == "o":
                ep.clear_override(
                    self.patches, int(parts[1]), int(parts[2]), int(parts[3])
                )
                n += 1
        if not n:
            return
        if not ep.save_patches(self.path, self.patches):
            self.save_failed()
            return
        self._changed(f"{n} Kanten-Patch(es) gelöscht.")

    def edit(self, iid):
        self._map(iid)

    def _map(self, iid=None):
        if iid is None:
            sel = self._entries(self.tree.selection())
            iid = sel[0] if sel else None
        if not iid:
            return
        parts = [int(p) for p in iid.split(":")[1:]]
        if iid.startswith("d:"):
            self.ctx.open_edge_map(parts[0], parts[1], None)
        else:
            self.ctx.open_edge_map(parts[1], parts[2], parts[0])


_LABEL_ID = re.compile(r"\((-?\d+)\)(?:\s*·.*)?$")


def parse_station_ref(text):
    """ID aus „Name (123) · …“ oder einer bloßen Zahl, sonst None."""
    text = (text or "").strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    m = _LABEL_ID.search(text)
    return int(m.group(1)) if m else None


SPLIT_NEAR_KM = 3.0


class SplitDialog(tk.Toplevel):
    """Aufteilung anlegen: falsche Station, Nachbar, richtige Station.

    `edge` (a, b) beschränkt die falsche Station auf die Kantenenden; das
    andere Ende wird Nachbar. result = (station, neighbor, to) oder None.
    """

    def __init__(self, master, stations, statuses, station=None, neighbor=None,
                 to=None, edge=None):
        super().__init__(master)
        self.title("Station aufteilen")
        self.transient(master)
        self.resizable(False, False)
        self.stations = stations
        self.statuses = statuses
        self.edge = tuple(edge) if edge else None
        self.result = None
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, wraplength=520, justify="left",
            text="Halte der falschen Station, die im Laufweg neben dem Nachbarn "
                 "liegen, werden auf die richtige Station umgeschrieben "
                 "(beide Richtungen, beide Kanten des Halts).",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.station_var = tk.StringVar()
        self.neighbor_var = tk.StringVar()
        self.to_var = tk.StringVar()
        rows = (("Falsche Station", self.station_var),
                ("Nachbar", self.neighbor_var),
                ("Richtige Station (oder ID)", self.to_var))
        self.boxes = []
        for i, (label, var) in enumerate(rows, start=1):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", pady=3)
            box = ttk.Combobox(frame, textvariable=var, width=52)
            box.grid(row=i, column=1, sticky="we", pady=3, padx=(8, 0))
            self.boxes.append(box)
        self.boxes[0].configure(values=self._station_choices())
        self.boxes[0].bind("<<ComboboxSelected>>", lambda _e: self._station_changed())
        self.boxes[0].bind("<FocusOut>", lambda _e: self._station_changed())
        if station is not None:
            self.station_var.set(station_label(stations, station))
        self._station_changed(neighbor=neighbor)
        if to is not None:
            self.to_var.set(station_label(stations, to))
        btns = ttk.Frame(frame)
        btns.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        (self.boxes[2] if station is not None else self.boxes[0]).focus_set()
        self.grab_set()
        self.wait_window(self)

    def _station_choices(self):
        if self.edge:
            return [station_label(self.stations, sid) for sid in self.edge]
        ids = sp.collect_used_ids(self.statuses)
        return sorted((station_label(self.stations, sid) for sid in ids),
                      key=str.casefold)

    def _station_changed(self, neighbor=None):
        sid = parse_station_ref(self.station_var.get())
        if sid is None:
            return
        counts = sp.neighbor_counts(self.statuses, sid)
        order = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
        self.boxes[1].configure(values=[
            "%s · %d Kanten" % (station_label(self.stations, nb), n) for nb, n in order
        ])
        if neighbor is None and self.edge and sid in self.edge:
            neighbor = self.edge[1] if sid == self.edge[0] else self.edge[0]
        if neighbor is not None:
            self.neighbor_var.set(station_label(self.stations, neighbor))
        self.boxes[2].configure(values=self._near_choices(sid))

    def _near_choices(self, sid):
        st = _station(self.stations, sid)
        if not isinstance(st, dict) or st.get("latitude") is None:
            return []
        near = []
        for key, other in self.stations.items():
            if not isinstance(other, dict) or other.get("latitude") is None:
                continue
            oid = ep.station_id(other.get("id", key))
            if oid == sid:
                continue
            km = ep.haversine_km(st["latitude"], st["longitude"],
                                 other["latitude"], other["longitude"])
            if km <= SPLIT_NEAR_KM:
                near.append((km, oid))
        near.sort(key=lambda r: (r[0], str(r[1])))
        return ["%s · %d m" % (station_label(self.stations, oid), round(km * 1000))
                for km, oid in near]

    def _ok(self):
        ids = [parse_station_ref(v.get())
               for v in (self.station_var, self.neighbor_var, self.to_var)]
        if None in ids:
            messagebox.showerror(
                "Eingabe", "Bitte alle drei Stationen wählen oder eine ID eintragen.",
                parent=self)
            return
        if len(set(ids)) < 3:
            messagebox.showerror(
                "Eingabe", "Falsche, Nachbar- und richtige Station müssen "
                "verschieden sein.", parent=self)
            return
        self.result = tuple(ids)
        self.destroy()


def ask_split(master, ctx, replace=None, on_saved=None, **prefill):
    """Dialog → Regel in station_patches.json; unbekannte Ziel-ID per API holen.

    `replace` (station, neighbor) ist die bearbeitete Regel und fällt weg, wenn
    sich ihr Schlüssel ändert. `on_saved` läuft nach dem Schreiben (auch nach
    einem Abruf im Hintergrund).
    """
    path = ctx.paths["stations"]
    err = check_config_file(path)
    if err:
        messagebox.showerror("Stationen", err, parent=master)
        return False
    stations = load_stations(ctx.stations_path)
    dlg = SplitDialog(master, stations, ctx.statuses, **prefill)
    if not dlg.result:
        return
    station, neighbor, to = dlg.result

    def save():
        patches = sp.load_patches(path)
        if replace and tuple(replace) != (station, neighbor):
            sp.clear_split(patches, *replace)
        if not sp.set_split(patches, station, neighbor, to):
            return
        if not sp.save_patches(path, patches):
            messagebox.showerror("Speichern", f"{path} nicht schreibbar.", parent=master)
            return
        fresh = load_stations(ctx.stations_path)
        ctx.set_status("Aufteilung gespeichert: %s neben %s → %s." % (
            station_label(fresh, station), station_label(fresh, neighbor),
            station_label(fresh, to)))
        ctx.after_change("StationsPage")
        if on_saved:
            on_saved()

    if _station(stations, to) is not None:
        save()
    else:
        ctx.fetch_station(to, save)


class StationsPage(_GroupedPage):
    def __init__(self, parent, ctx):
        self.patches = sp.empty_patches()
        self.stations = {}
        super().__init__(
            parent, ctx, "Stationen", ctx.paths["stations"],
            "wirkt beim nächsten Dashboard-Bau",
            ("station", "detail"),
            {"station": "Station", "detail": "Änderung"},
            widths={"station": 380, "detail": 380},
        )

    def extra_buttons(self, frame):
        self._btn(frame, "Aufteilung hinzufügen …", self._add_split)
        self._btn(frame, "Stationskarte öffnen", self.ctx.open_station_map, side="right")

    def _add_split(self):
        if not self.read_only:
            ask_split(self, self.ctx, on_saved=self.reload)

    def load(self):
        self.patches = sp.load_patches(self.path)
        self.stations = load_stations(self.ctx.stations_path)

    def orphans(self):
        bad = set()
        for sid in self.patches["moves"]:
            if _station(self.stations, sid) is None:
                bad.add("m:%d" % sid)
        for src, dest in self.patches["merges"].items():
            if _station(self.stations, dest) is None:
                bad.add("j:%d" % src)
        for (sid, nb), dest in self.patches["splits"].items():
            if _station(self.stations, dest) is None:
                bad.add("s:%d:%d" % (sid, nb))
        return bad

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        if self.read_only:
            return
        q = (self.search_var.get() or "").strip().casefold()
        orphan = self.orphans()
        self.tree.insert("", "end", iid="h:m", text="Verschiebungen", open=True)
        def move_values(kv):
            sid, (lat, lon) = kv
            dist = move_distance_m(self.stations, sid, lat, lon)
            return (station_label(self.stations, sid), dist if dist is not None else -1)

        for sid, (lat, lon) in self._ordered(self.patches["moves"].items(), move_values):
            dist = move_distance_m(self.stations, sid, lat, lon)
            vals = (station_label(self.stations, sid),
                    f"um {dist} m verschoben" if dist is not None else "verschoben")
            if q and q not in " ".join(vals).casefold():
                continue
            iid = "m:%d" % sid
            self.tree.insert("h:m", "end", iid=iid, values=vals,
                             tags=("orphan",) if iid in orphan else ())
        self.tree.insert("", "end", iid="h:j", text="Zusammenlegungen", open=True)
        for src, dest in self._ordered(
            self.patches["merges"].items(),
            lambda kv: (station_label(self.stations, kv[0]),
                        station_label(self.stations, kv[1])),
        ):
            vals = (station_label(self.stations, src),
                    "→ " + station_label(self.stations, dest))
            if q and q not in " ".join(vals).casefold():
                continue
            iid = "j:%d" % src
            self.tree.insert("h:j", "end", iid=iid, values=vals,
                             tags=("orphan",) if iid in orphan else ())
        self.tree.insert("", "end", iid="h:s", text="Aufteilungen", open=True)
        for (sid, nb), dest in self._ordered(
            self.patches["splits"].items(),
            lambda kv: (station_label(self.stations, kv[0][0]) + " neben "
                        + station_label(self.stations, kv[0][1]),
                        station_label(self.stations, kv[1])),
        ):
            vals = (station_label(self.stations, sid) + " neben "
                    + station_label(self.stations, nb),
                    "→ " + station_label(self.stations, dest))
            if q and q not in " ".join(vals).casefold():
                continue
            iid = "s:%d:%d" % (sid, nb)
            self.tree.insert("h:s", "end", iid=iid, values=vals,
                             tags=("orphan",) if iid in orphan else ())
        self.orphan_btn.configure(state="normal" if orphan else "disabled")

    def edit(self, iid):
        kind, _, rest = iid.partition(":")
        if kind != "s":
            self.ctx.open_station_map()
            return
        sid, nb = (int(x) for x in rest.split(":"))
        ask_split(self, self.ctx, replace=(sid, nb), on_saved=self.reload,
                  station=sid, neighbor=nb, to=self.patches["splits"].get((sid, nb)))

    def delete(self, iids):
        n = 0
        for iid in iids:
            kind, _, sid = iid.partition(":")
            if kind == "m":
                sp.clear_move(self.patches, int(sid))
                n += 1
            elif kind == "j":
                sp.clear_merge(self.patches, int(sid))
                n += 1
            elif kind == "s":
                station, nb = sid.split(":")
                sp.clear_split(self.patches, int(station), int(nb))
                n += 1
        if not n:
            return
        if not sp.save_patches(self.path, self.patches):
            self.save_failed()
            return
        self._changed(f"{n} Stations-Patch(es) gelöscht.")


NAV_GROUPS = (
    ("FAHRZEUGE", (("roster", "Fuhrpark"), ("families", "Baureihenfamilien"))),
    ("LINIEN", (("lines", "Verwaltung"),)),
    ("BETREIBER", (
        ("home", "Heimatregion"), ("replacements", "Namen"), ("line_rules", "Je Linie"),
    )),
    ("KARTE", (("edges", "Kanten"), ("stations", "Stationen"))),
    ("DARSTELLUNG", (("colors", "Linienfarben"), ("boarding", "Einstiege"))),
)

PAGES = {
    "roster": RosterPage,
    "families": FamiliesPage,
    "home": HomeRegionPage,
    "replacements": ReplacementsPage,
    "line_rules": LineRulesPage,
    "lines": LinesPage,
    "edges": EdgesPage,
    "stations": StationsPage,
    "colors": ColorsPage,
    "boarding": BoardingPage,
}
