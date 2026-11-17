"""Blatt "Assets" lesen und die CPE-Spalte samt Statuskommentar schreiben."""
from __future__ import annotations

import sys
from datetime import date

from openpyxl.comments import Comment

from .model import Result

SHEET = "Assets"
# interner Schlüssel -> Spaltenüberschrift im Blatt
HEADERS = {"id": "ID", "vendor": "Hersteller", "product": "Produkt",
           "version": "Version", "cpe": "CPE"}
MAX_CANDIDATES_IN_COMMENT = 15


def header_map(ws) -> dict:
    """{interner Schlüssel: Spaltennummer}; bricht ab, wenn Spalten fehlen."""
    columns = {str(c.value).strip(): c.column for c in ws[1] if c.value is not None}
    missing = [h for h in HEADERS.values() if h not in columns]
    if missing:
        sys.exit(f"Spalten fehlen im Blatt {SHEET}: {missing}")
    return {key: columns[header] for key, header in HEADERS.items()}


def read_assets(ws, columns: dict):
    """Liefert (Zeilennummer, Zeile) für jede Zeile mit ID; Werte als getrimmte Strings."""
    for row_no in range(2, ws.max_row + 1):
        raw = {key: ws.cell(row_no, col).value for key, col in columns.items()}
        if not raw["id"]:
            continue
        yield row_no, {k: (str(v).strip() if v is not None else None) for k, v in raw.items()}


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
