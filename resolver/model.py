"""Status-Werte und Ergebnistypen."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

S_EXACT = "NVD exakt"
S_VERSION = "Version nicht gelistet"
S_BUILD = "Build unbekannt"
S_OVERRIDE = "Override"
S_DERIVED = "Abgeleitet"
S_MANUAL = "Manuell prüfen"


class VendorProduct(NamedTuple):
    part: str
    vendor: str
    product: str

    def __str__(self) -> str:
        return ":".join(self)


@dataclass
class ProductSearch:
    """Ergebnis von find_vendor_product: match ist None, wenn nicht eindeutig."""
    match: VendorProduct | None
    reason: str
    candidates: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    """Ergebnis der Auflösung eines Assets, noch ohne Bezug zur Zeile."""
    status: str
    cpe: str | None
    detail: str
    candidates: list[str] = field(default_factory=list)


@dataclass
class Result:
    """Ergebnis pro Asset-Zeile, wie es in Excel und Report landet."""
    asset_id: str
    old: str | None
    new: str | None
    status: str
    detail: str = ""
    candidates: list[str] = field(default_factory=list)


def manual(detail: str, candidates: list[str] | None = None) -> Resolution:
    return Resolution(S_MANUAL, None, detail, candidates or [])
