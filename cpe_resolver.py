#!/usr/bin/env python3
"""
cpe_resolver.py - PoC: bestimmt CPE-2.3-Namen für das Asset-Blatt (z. B. "Assets"
oder "IT-Assets") anhand des offiziellen NVD-CPE-Dictionary (API 2.0) und schreibt
sie in die Spalte "CPE".

Grundsatz: Es wird nichts geraten. Ein Asset bekommt nur dann eine CPE, wenn
vendor:product eindeutig aus der NVD bestimmt werden konnte (oder per Override
festgelegt ist). Alles andere wird als "Manuell prüfen" markiert und - je nach
--on-ambiguous - geleert oder unverändert gelassen.

Status (steht als Kommentar an der CPE-Zelle und im Report):
  NVD exakt               exakter, nicht veralteter Eintrag im NVD-Dictionary
  Version nicht gelistet  vendor:product bestätigt, Version nicht im Dictionary
                          -> CPE aus bestätigtem vendor:product + Inventarversion
  Build unbekannt         vendor:product bestätigt, Version unbekannt -> "*"
  Override                Zuordnung aus overrides.csv (gegen NVD geprüft)
  Manuell prüfen          kein / kein eindeutiger Treffer, Version nicht NVD-konform

Nutzung:
  python3 cpe_resolver.py Assetverzeichnis.xlsx                 # Probelauf, nur Report
  python3 cpe_resolver.py Assetverzeichnis.xlsx --apply         # schreibt <name>_cpe.xlsx
  Optionen: --overrides overrides.csv  --on-ambiguous clear|keep  --out datei.xlsx
  API-Key (optional): NVD_API_KEY in .env (Vorlage .env.example) oder als
  Umgebungsvariable; die Umgebungsvariable hat Vorrang.

Abhängigkeit: openpyxl (pip install openpyxl). HTTP läuft über die Standardbibliothek.

Aufbau (Paket resolver/):
  credentials.py     Zugangsdaten aus .env lesen
  cpe.py             CPE-Strings zerlegen, maskieren, bauen
  text.py            Wortzerlegung, Hersteller-Vergleich
  nvd.py             NVD-Client mit Cache und Rate-Limit
  model.py           Status-Werte und Ergebnistypen
  vendor_product.py  vendor:product per Stichwortsuche bestimmen
  version.py         Version prüfen und CPE bauen
  overrides.py       overrides.csv laden und anwenden
  resolve.py         eine Asset-Zeile auflösen
  excel.py           Blatt lesen, CPE-Zelle + Kommentar schreiben
  report.py          CSV-Report

Begriffe im Code:
  inventory_*  Werte aus dem Assetverzeichnis (Hersteller, Produkt, Version)
  vendor/product/part  Felder eines CPE-Namens aus der NVD
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import openpyxl

from resolver.aliases import load_vendor_aliases
from resolver.credentials import get_secret
from resolver.excel import find_asset_sheet, header_map, read_assets, write_cpe_cell
from resolver.model import Result
from resolver.nvd import NvdClient
from resolver.overrides import load_overrides
from resolver.report import write_report
from resolver.resolve import resolve_row

DEFAULT_CREDENTIALS = Path(__file__).resolve().parent / ".env"


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--sheet", help="Asset-Blatt (Standard: das Blatt mit den Spalten "
                                    "ID, Hersteller, Produkt, Version, CPE)")
    ap.add_argument("--apply", action="store_true", help="Ergebnis schreiben (sonst Probelauf)")
    ap.add_argument("--out", type=Path, help="Zieldatei (Standard: <name>_cpe.xlsx)")
    ap.add_argument("--overrides", type=Path, default=Path("overrides.csv"))
    ap.add_argument("--vendor-aliases", type=Path, default=Path("vendor_aliases.csv"),
                    help="Hersteller -> NVD-Vendor (z. B. Igor Pavlov -> 7-zip)")
    ap.add_argument("--on-ambiguous", choices=["clear", "keep"], default="clear",
                    help="CPE-Zelle bei 'Manuell prüfen' leeren oder alten Wert behalten")
    ap.add_argument("--report", type=Path, default=Path("cpe_report.csv"))
    ap.add_argument("--cache-dir", type=Path, default=Path(".nvd_cache"))
    ap.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS,
                    help="Datei mit NVD_API_KEY=... (Standard: .env neben diesem Skript)")
    return ap.parse_args(argv)


def create_nvd_client(args) -> NvdClient:
    api_key, source = get_secret("NVD_API_KEY", args.credentials)
    print(f"NVD-API-Key: {source}")
    return NvdClient(args.cache_dir, api_key)


def main(argv=None, nvd: NvdClient | None = None) -> list[Result]:
    args = parse_args(argv)
    nvd = nvd or create_nvd_client(args)
    overrides = load_overrides(args.overrides)
    aliases = load_vendor_aliases(args.vendor_aliases)

    wb = openpyxl.load_workbook(args.xlsx)  # Formeln bleiben als Formeln erhalten
    ws = find_asset_sheet(wb, args.sheet)
    print(f"Blatt: {ws.title}")
    columns = header_map(ws)

    results = []
    for row_no, row in read_assets(ws, columns):
        print(f"{row['id']}: {row['vendor']} / {row['product']} / {row['version']}")
        result = resolve_row(nvd, row, overrides, aliases)
        print(f"   -> [{result.status}] {result.new or '-'}")
        results.append(result)
        write_cpe_cell(ws.cell(row_no, columns["cpe"]), result,
                       clear_if_manual=args.on_ambiguous == "clear")

    write_report(args.report, results)

    counts = Counter(r.status for r in results)
    print("\nZusammenfassung:", ", ".join(f"{k}: {v}" for k, v in counts.items()))
    print(f"Report: {args.report}")
    if args.apply:
        out = args.out or args.xlsx.with_name(args.xlsx.stem + "_cpe.xlsx")
        wb.save(out)
        print(f"Geschrieben: {out}")
    else:
        print("Probelauf - nichts geschrieben (mit --apply speichern).")
    return results


if __name__ == "__main__":
    main()
