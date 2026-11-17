"""CSV-Report der Ergebnisse."""
from __future__ import annotations

import csv
from pathlib import Path

from .model import Result


def write_report(path: Path, results: list[Result]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["asset_id", "status", "cpe_alt", "cpe_neu", "geaendert", "detail",
                         "kandidaten"])
        for r in results:
            changed = (r.new or None) != (r.old or None)
            writer.writerow([r.asset_id, r.status, r.old or "", r.new or "",
                             "ja" if changed else "nein", r.detail, " | ".join(r.candidates)])
