"""Asset-Blatt finden, lesen und die CPE-Spalte samt Statuskommentar schreiben."""
from __future__ import annotations

import sys
from datetime import date

from openpyxl.comments import Comment

from .model import Result

# interner Schlüssel -> Spaltenüberschrift im Blatt
HEADERS = {"id": "ID", "vendor": "Hersteller", "product": "Produkt",
           "version": "Version", "cpe": "CPE"}
MAX_CANDIDATES_IN_COMMENT = 15


def header_columns(ws) -> dict:
    """{Überschrift: Spaltennummer} aus der ersten Zeile."""
    return {str(c.value).strip(): c.column for c in ws[1] if c.value is not None}


def find_asset_sheet(wb, name: str | None = None):
    """Das Blatt mit allen HEADERS in Zeile 1 (Name ist je Datei verschieden,
    z. B. "Assets" oder "IT-Assets"); mit name wird genau dieses Blatt genommen."""
    if name:
        if name not in wb.sheetnames:
            sys.exit(f"Blatt {name!r} nicht gefunden; vorhanden: {wb.sheetnames}")
        return wb[name]
    matching = [ws for ws in wb.worksheets
                if set(HEADERS.values()) <= set(header_columns(ws))]
    if len(matching) != 1:
        found = [ws.title for ws in matching] or "keins"
        sys.exit(f"Asset-Blatt nicht eindeutig (Blätter mit {list(HEADERS.values())}: "
                 f"{found}); mit --sheet angeben")
    return matching[0]


def header_map(ws) -> dict:
    """{interner Schlüssel: Spaltennummer}; bricht ab, wenn Spalten fehlen."""
    columns = header_columns(ws)
    missing = [h for h in HEADERS.values() if h not in columns]
    if missing:
        sys.exit(f"Spalten fehlen im Blatt {ws.title}: {missing}")
    return {key: columns[header] for key, header in HEADERS.items()}


def read_assets(ws, columns: dict):
    """Liefert (Zeilennummer, Zeile) für jede Zeile mit ID; Werte als getrimmte Strings.

    Die Version bleibt im Zelltyp erhalten (nur Text wird getrimmt), damit Zahlen
    (6.10 -> 6.1) und Datumswerte (1.10 -> 01.10.) erkannt werden können.
    """
    for row_no in range(2, ws.max_row + 1):
        raw = {key: ws.cell(row_no, col).value for key, col in columns.items()}
        if not raw["id"]:
            continue
        row = {k: (str(v).strip() if v is not None else None) for k, v in raw.items()}
        version = raw["version"]
        row["version"] = version.strip() if isinstance(version, str) else version
        yield row_no, row


def write_cpe_cell(cell, result: Result, clear_if_manual: bool) -> None:
    """Schreibt die CPE (oder leert die Zelle) und hängt den Status als Kommentar an."""
    if result.new:
        cell.value = result.new
    elif clear_if_manual:
        cell.value = None

    text = f"CPE-Status: {result.status}\n{result.detail}"
    if result.candidates:
        text += "\nKandidaten:\n" + "\n".join(result.candidates[:MAX_CANDIDATES_IN_COMMENT])
    text += f"\nQuelle: NVD CPE API 2.0, {date.today():%d.%m.%Y}"
    cell.comment = Comment(text, "cpe_resolver")
    cell.comment.width, cell.comment.height = 420, 160
