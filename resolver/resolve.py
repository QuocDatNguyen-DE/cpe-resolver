"""Eine Asset-Zeile auflösen: Override oder Suche im NVD-Dictionary."""
from __future__ import annotations

from .model import Resolution, Result, manual
from .nvd import NvdClient
from .overrides import resolve_override
from .vendor_product import find_vendor_product
from .version import resolve_version


def resolve_from_nvd(nvd: NvdClient, row: dict, aliases: dict | None = None) -> Resolution:
    search = find_vendor_product(nvd, row["vendor"] or "", row["product"] or "", aliases,
                                 row["version"])
    if search.match is None:
        return manual(search.reason, search.candidates)
    return resolve_version(nvd, search.match, row["version"], row["product"],
                           f"({search.reason})")


def resolve_row(nvd: NvdClient, row: dict, overrides: dict, aliases: dict | None = None) -> Result:
    if row["id"] in overrides:
        resolution = resolve_override(nvd, overrides[row["id"]], row)
    else:
        resolution = resolve_from_nvd(nvd, row, aliases)
    return Result(row["id"], row["cpe"], resolution.cpe, resolution.status,
                  resolution.detail, resolution.candidates)
