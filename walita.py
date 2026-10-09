#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Einstiegspunkt: Träwelling-Statuses laden und HTML-Dashboard bauen.

Führt nacheinander `download_statuses` und `build_dashboard` aus (Defaults unter
`data/`). Typische Nutzung:

    python3 walita.py                 # Export + Dashboard, öffnet im Browser
    python3 walita.py --login         # OAuth-Login, dann Export + Dashboard
    python3 walita.py --since 2026-01-01  # nur Fahrten nach diesem Datum
    python3 walita.py --full          # alle Statuses laden (Default: neue + letzte Tage)
    python3 walita.py --no-open       # ohne Browser
    python3 walita.py --demo          # Demo aus examples/ (kein Token nötig)
    python3 walita.py --dashboard-only  # nur Dashboard neu erzeugen aus vorhandener data/
    python3 walita.py --edit          # Tag-Editor (live Tags + Status-Text)
"""

import argparse
import os
import sys

import build_dashboard
import download_statuses
from version import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Details zu einzelnen Schritten: download_statuses.py / "
               "build_dashboard.py / status_editor.py --help",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"walita {__version__}",
        help="Version ausgeben und beenden.",
    )

    auth = parser.add_argument_group("Anmeldung")
    auth.add_argument(
        "--token",
        default=os.environ.get("TRWL_TOKEN"),
        help="Personal Access Token (sonst Env TRWL_TOKEN).",
    )
    auth.add_argument(
        "--login", action="store_true",
        help="OAuth-Login im Browser erzwingen (Export: read-statuses; "
             "mit --edit zusätzlich write-statuses).",
    )
    auth.add_argument(
        "--logout", action="store_true",
        help="Gespeichertes OAuth-Token löschen und beenden.",
    )
    auth.add_argument(
        "--client-id", default=os.environ.get("TRWL_CLIENT_ID"),
        help="Optional eigene OAuth-Client-ID (Default steht in auth.py).",
    )
    auth.add_argument(
        "--redirect-uri", default=os.environ.get("TRWL_REDIRECT_URI"),
        help="Optional eigener OAuth-Redirect (Default: Loopback in auth.py).",
    )
    auth.add_argument(
        "--manual", action="store_true",
        help="OAuth-Code manuell einfügen (z.B. unter WSL).",
    )
    auth.add_argument(
        "--oauth-token-file", default="data/oauth_token.json",
        help="Ablageort des OAuth-Tokens (Default: data/oauth_token.json).",
    )

    export = parser.add_argument_group("Export")
    export.add_argument(
        "--limit", type=int, default=None,
        help="Max. Anzahl Statuses (zum Testen).",
    )
    export.add_argument(
        "--full", action="store_true",
        help="Alle Statuses laden; ohne Flag nur neue und die letzten Tage "
             "(für Änderungen an älteren Fahrten).",
    )
    export.add_argument(
        "--since", metavar="YYYY-MM-DD", default="",
        help="Nur Statuses mit Abfahrt strikt nach diesem Tag "
             "(z.B. --since 2026-01-01: alles ab dem 02.01.2026).",
    )
    export.add_argument(
        "--skip-trips", action="store_true",
        help="Keine Zwischenhalte nachladen.",
    )
    export.add_argument(
        "--refresh-trips", action="store_true",
        help="Trip-Cache ignorieren, alle Trips neu laden.",
    )
    export.add_argument(
        "--refresh-stations", action="store_true",
        help="Stations-Cache ignorieren, Koordinaten und Identifier neu auflösen.",
    )
    export.add_argument(
        "--no-stations", action="store_true",
        help="Keine stations.json schreiben.",
    )
    export.add_argument(
        "--operator-replacements", default="data/operator_replacements.json",
        help="JSON mit Operator-Namen-Ersetzungen (Default: data/operator_replacements.json).",
    )

    dash = parser.add_argument_group("Dashboard")
    dash.add_argument(
        "--open", action="store_true", default=True,
        help="Dashboard nach dem Bau im Browser öffnen (Default).",
    )
    dash.add_argument(
        "--no-open", action="store_false", dest="open",
        help="Dashboard nicht im Browser öffnen.",
    )
    dash.add_argument(
        "--ignore-plus", action="store_true",
        help="Wagennummern-Tags nicht am '+' trennen.",
    )
    dash.add_argument(
        "--loc-class-families", default="data/loc_class_families.txt",
        help="Baureihe→Familie für den Kartenfilter "
             "(Default: data/loc_class_families.txt; JSON-Objekt, "
             "gleiche Baureihe darf mehrfach vorkommen).",
    )
    dash.add_argument(
        "--edge-patches", default="data/edge_patches.json",
        help="Lokale Via-Patches für grobe Kanten "
             "(Default: data/edge_patches.json; fehlende Datei = keine Expansion).",
    )
    dash.add_argument(
        "--station-patches", default="data/station_patches.json",
        help="Lokale Stations-Patches (Koordinaten/Merges) "
             "(Default: data/station_patches.json; fehlende Datei = keine Änderung).",
    )
    dash.add_argument(
        "--line-color-patches", default="data/line_color_patches.json",
        help="Lokale Linienfarben-Patches je Status "
             "(Default: data/line_color_patches.json; fehlende Datei = keine Änderung).",
    )
    dash.add_argument(
        "--home-region", default="data/home_region.json",
        help="Lokale Operator-Liste der Heimatregion "
             "(Default: data/home_region.json; fehlende oder leere Datei = kein Filter).",
    )
    dash.add_argument(
        "--boarding-patches", default="data/boarding_patches.json",
        help="Lokale Einstiegs-Patches je Status "
             "(Default: data/boarding_patches.json; fehlende Datei = keine Änderung).",
    )
    dash.add_argument(
        "--vehicle-roster", default="data/vehicle_roster.json",
        help="Lokaler Fuhrpark je Baureihe "
             "(Default: data/vehicle_roster.json; fehlende Datei = keine Nummernliste).",
    )
    dash.add_argument(
        "--line-patches", default="data/line_patches.json",
        help="Liniennamen je Fahrt und zusammengeführte Linien "
             "(Default: data/line_patches.json).",
    )
    dash.add_argument(
        "--operator-line-patches", default="data/operator_line_patches.json",
        help="Operator einer Linie überschreiben "
             "(Default: data/operator_line_patches.json; fehlende Datei = keine Änderung).",
    )

    mode = parser.add_argument_group("Modus")
    # Beide Modi überspringen den Export – gemeinsam angegeben wäre unklar, welcher gilt.
    mode_excl = mode.add_mutually_exclusive_group()
    mode_excl.add_argument(
        "--demo", action="store_true",
        help="Nur Dashboard aus examples/ bauen (kein API-Download, kein Token).",
    )
    mode_excl.add_argument(
        "--dashboard-only", action="store_true",
        help="Export überspringen, Dashboard aus vorhandener data/ bauen.",
    )
    mode_excl.add_argument(
        "--edit", action="store_true",
        help="Tag-Editor öffnen (Tags und Status-Text live auf Träwelling).",
    )

    args = parser.parse_args(argv)

    # --demo und --dashboard-only laden nichts von der API; Anmelde- und Export-Flags
    # hätten dort keine Wirkung. Lieber abbrechen als stillschweigend ignorieren.
    # Verglichen wird gegen den Default, damit gesetzte Env-Vars (TRWL_TOKEN etc.)
    # nicht als bewusste Angabe zählen.
    skip_export = args.demo or args.dashboard_only
    if skip_export:
        ignored = [
            flag for flag, dest in (
                ("--token", "token"), ("--login", "login"),
                ("--client-id", "client_id"), ("--redirect-uri", "redirect_uri"),
                ("--manual", "manual"), ("--limit", "limit"), ("--full", "full"),
                ("--since", "since"),
                ("--skip-trips", "skip_trips"), ("--refresh-trips", "refresh_trips"),
                ("--refresh-stations", "refresh_stations"),
                ("--no-stations", "no_stations"),
                ("--operator-replacements", "operator_replacements"),
                ("--oauth-token-file", "oauth_token_file"),
            )
            if getattr(args, dest) != parser.get_default(dest)
        ]
        if ignored:
            active = "--demo" if args.demo else "--dashboard-only"
            parser.error(
                f"{active} lädt nichts von der API – {', '.join(ignored)} "
                f"hätte keine Wirkung. Flag(s) weglassen oder {active} entfernen."
            )

    if args.logout:
        return download_statuses.main(
            ["--logout", "--oauth-token-file", args.oauth_token_file]
        )

    if args.edit:
        import status_editor
        edit_argv = []
        if args.token:
            edit_argv.extend(["--token", args.token])
        if args.login:
            edit_argv.append("--login")
        if args.client_id:
            edit_argv.extend(["--client-id", args.client_id])
        if args.redirect_uri:
            edit_argv.extend(["--redirect-uri", args.redirect_uri])
        if args.manual:
            edit_argv.append("--manual")
        edit_argv.extend(["--oauth-token-file", args.oauth_token_file])
        if args.limit is not None:
            edit_argv.extend(["--limit", str(args.limit)])
        if args.since:
            edit_argv.extend(["--since", args.since])
        if args.ignore_plus:
            edit_argv.append("--ignore-plus")
        edit_argv.extend(["--loc-class-families", args.loc_class_families])
        edit_argv.extend(["--edge-patches", args.edge_patches])
        edit_argv.extend(["--station-patches", args.station_patches])
        edit_argv.extend(["--line-color-patches", args.line_color_patches])
        edit_argv.extend(["--home-region", args.home_region])
        edit_argv.extend(["--boarding-patches", args.boarding_patches])
        edit_argv.extend(["--vehicle-roster", args.vehicle_roster])
        edit_argv.extend(["--operator-line-patches", args.operator_line_patches])
        edit_argv.extend(["--line-patches", args.line_patches])
        edit_argv.extend(["--operator-replacements", args.operator_replacements])
        return status_editor.main(edit_argv)

    if args.demo:
        dash_argv = [
            "--statuses", "examples/statuses.json",
            "--stations", "examples/stations.json",
            "-o", "data/dashboard.html",
        ]
        if args.open:
            dash_argv.append("--open")
        if args.ignore_plus:
            dash_argv.append("--ignore-plus")
        dash_argv.extend(["--loc-class-families", args.loc_class_families])
        dash_argv.extend(["--edge-patches", args.edge_patches])
        dash_argv.extend(["--station-patches", args.station_patches])
        dash_argv.extend(["--line-color-patches", args.line_color_patches])
        dash_argv.extend(["--home-region", args.home_region])
        dash_argv.extend(["--boarding-patches", args.boarding_patches])
        dash_argv.extend(["--vehicle-roster", args.vehicle_roster])
        dash_argv.extend(["--operator-line-patches", args.operator_line_patches])
        dash_argv.extend(["--line-patches", args.line_patches])
        return build_dashboard.main(dash_argv)

    if not args.dashboard_only:
        dl_argv = []
        if args.token:
            dl_argv.extend(["--token", args.token])
        if args.login:
            dl_argv.append("--login")
        if args.client_id:
            dl_argv.extend(["--client-id", args.client_id])
        if args.redirect_uri:
            dl_argv.extend(["--redirect-uri", args.redirect_uri])
        if args.manual:
            dl_argv.append("--manual")
        dl_argv.extend(["--oauth-token-file", args.oauth_token_file])
        if args.limit is not None:
            dl_argv.extend(["--limit", str(args.limit)])
        if args.full:
            dl_argv.append("--full")
        if args.since:
            dl_argv.extend(["--since", args.since])
        if args.skip_trips:
            dl_argv.append("--skip-trips")
        if args.refresh_trips:
            dl_argv.append("--refresh-trips")
        if args.refresh_stations:
            dl_argv.append("--refresh-stations")
        if args.no_stations:
            dl_argv.append("--no-stations")
        dl_argv.extend(["--operator-replacements", args.operator_replacements])

        rc = download_statuses.main(dl_argv)
        if rc:
            return rc

    dash_argv = []
    if args.open:
        dash_argv.append("--open")
    if args.ignore_plus:
        dash_argv.append("--ignore-plus")
    dash_argv.extend(["--loc-class-families", args.loc_class_families])
    dash_argv.extend(["--edge-patches", args.edge_patches])
    dash_argv.extend(["--station-patches", args.station_patches])
    dash_argv.extend(["--line-color-patches", args.line_color_patches])
    dash_argv.extend(["--home-region", args.home_region])
    dash_argv.extend(["--boarding-patches", args.boarding_patches])
    dash_argv.extend(["--vehicle-roster", args.vehicle_roster])
    dash_argv.extend(["--operator-line-patches", args.operator_line_patches])
    dash_argv.extend(["--line-patches", args.line_patches])
    return build_dashboard.main(dash_argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Abgebrochen.", file=sys.stderr, flush=True)
        sys.exit(130)
