#!/usr/bin/env python3
"""
cpe_resolver.py - PoC: bestimmt CPE-2.3-Namen für das Blatt "Assets" anhand des
offiziellen NVD-CPE-Dictionary (API 2.0) und schreibt sie in die Spalte "CPE".

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
  API-Key (optional): Umgebungsvariable NVD_API_KEY

Abhängigkeit: openpyxl (pip install openpyxl). HTTP läuft über die Standardbibliothek.

Aufbau (Paket resolver/):
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
import os
from collections import Counter
from pathlib import Path

import openpyxl

from resolver.excel import SHEET, header_map, read_assets, write_cpe_cell
from resolver.model import Result
from resolver.nvd import NvdClient
from resolver.overrides import load_overrides
from resolver.report import write_report
from resolver.resolve import resolve_row


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--apply", action="store_true", help="Ergebnis schreiben (sonst Probelauf)")
    ap.add_argument("--out", type=Path, help="Zieldatei (Standard: <name>_cpe.xlsx)")
    ap.add_argument("--overrides", type=Path, default=Path("overrides.csv"))
    ap.add_argument("--on-ambiguous", choices=["clear", "keep"], default="clear",
                    help="CPE-Zelle bei 'Manuell prüfen' leeren oder alten Wert behalten")
    ap.add_argument("--report", type=Path, default=Path("cpe_report.csv"))
    ap.add_argument("--cache-dir", type=Path, default=Path(".nvd_cache"))
    return ap.parse_args(argv)


def main(argv=None, nvd: NvdClient | None = None) -> list[Result]:
    args = parse_args(argv)
    nvd = nvd or NvdClient(args.cache_dir, os.environ.get("NVD_API_KEY"))
    overrides = load_overrides(args.overrides)

    wb = openpyxl.load_workbook(args.xlsx)  # Formeln bleiben als Formeln erhalten
    ws = wb[SHEET]
    columns = header_map(ws)

    results = []
    for row_no, row in read_assets(ws, columns):
        print(f"{row['id']}: {row['vendor']} / {row['product']} / {row['version']}")
        result = resolve_row(nvd, row, overrides)
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
