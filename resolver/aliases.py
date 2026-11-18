"""Hersteller-Aliase aus vendor_aliases.csv laden (Inventar-Hersteller -> NVD-Vendor)."""
from __future__ import annotations

import csv
from pathlib import Path

from .text import vendor_key


def load_vendor_aliases(path: Path | None) -> dict[str, set[str]]:
    """vendor_aliases.csv -> {Hersteller-Schlüssel: {NVD-Vendor-Schlüssel}}; # = Kommentar."""
    if not path or not path.exists():
        return {}
    aliases: dict[str, set[str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            vendor, nvd_vendor = (r.get("hersteller") or "").strip(), (r.get("nvd_vendor") or "").strip()
            if vendor and nvd_vendor and not vendor.startswith("#"):
                aliases.setdefault(vendor_key(vendor), set()).add(vendor_key(nvd_vendor))
    return aliases
