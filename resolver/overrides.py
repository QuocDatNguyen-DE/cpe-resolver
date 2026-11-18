"""Manuelle Zuordnungen aus overrides.csv laden und gegen die NVD prüfen."""
from __future__ import annotations

import csv
from pathlib import Path

from .cpe import split_cpe
from .model import S_MANUAL, S_OVERRIDE, Resolution, VendorProduct, manual
from .nvd import NvdClient, active
from .version import resolve_version


def load_overrides(path: Path | None) -> dict:
    """overrides.csv -> {asset_id: Zeile}; Zeilen mit # werden ignoriert."""
    if not path or not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f, delimiter=";")
                if r.get("asset_id") and not r["asset_id"].startswith("#")]
    return {r["asset_id"].strip(): r for r in rows}


def resolve_override(nvd: NvdClient, override: dict, row: dict) -> Resolution:
    """Override mit voller CPE oder mit vendor+product (+ optional version)."""
    if override.get("cpe"):
        cpe = override["cpe"].strip()
        split_cpe(cpe)  # Formatprüfung
        found = any(h["cpeName"] == cpe for h in active(nvd.products(cpeMatchString=cpe)))
        where = "exakt im NVD-Dictionary" if found else "NICHT im NVD-Dictionary"
        return Resolution(S_OVERRIDE, cpe, f"volle CPE aus overrides.csv; {where}")

    vendor, product = override["vendor"].strip(), override["product"].strip()
    hits = active(nvd.products(cpeMatchString=f"cpe:2.3:*:{vendor}:{product}"))
    parts = sorted({split_cpe(h["cpeName"])["part"] for h in hits})
    if len(parts) != 1:
        return manual(f"Override {vendor}:{product} nicht (eindeutig) im NVD-Dictionary")

    version = override.get("version") or row["version"]
    resolution = resolve_version(nvd, VendorProduct(parts[0], vendor, product), version,
                                 row["product"], "(Override)", check_related=False)
    if resolution.status != S_MANUAL:
        resolution.status = S_OVERRIDE
    return resolution
