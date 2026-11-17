"""Aus bestätigtem vendor:product und Inventarversion die CPE bestimmen."""
from __future__ import annotations

import re

from .cpe import EXTRA_FIELDS, WILDCARDS, build_cpe, escape, split_cpe, unescape
from .model import S_BUILD, S_EXACT, S_VERSION, Resolution, VendorProduct, manual
from .nvd import NvdClient, active
from .text import tokens

# Inventar-Werte, die "Version unbekannt" bedeuten
UNKNOWN_VERSION = re.compile(r"^\s*(build\s+offen|offen|unbekannt|n/?a|-|–|)\s*$", re.I)
# Grobe Plausibilität: NVD-Versionen beginnen mit einer Ziffer (17.5.0, 2022.1, 6.4.3 ...)
NVD_LIKE_VERSION = re.compile(r"^\d+([._\-+][0-9a-z]+)*$", re.I)
# Kürzel im Produktnamen -> Feldwerte im NVD-Dictionary
TOKEN_ALIASES = {"ce": "community", "ee": "enterprise"}


def pick_version_entry(entries: list[dict], inventory_product: str):
    """Wählt bei mehreren exakten Versionseinträgen den passenden (Edition o. ä.).

    Ein Eintrag passt, wenn alle seine gesetzten Zusatzfelder (update, edition,
    sw_edition ...) im Produktnamen vorkommen. Unter den passenden gewinnt der
    mit den meisten Zusatzfeldern - aber nur, wenn er der einzige ist.
    Rückgabe: (Eintrag oder None, Hinweis).
    """
    if len(entries) == 1:
        return entries[0], ""

    words = set(tokens(inventory_product, drop_parens=False))
    words |= {TOKEN_ALIASES[w] for w in words if w in TOKEN_ALIASES}

    matching = []  # (Anzahl Zusatzfelder, Eintrag)
    for entry in entries:
        fields = split_cpe(entry["cpeName"])
        extras = [unescape(fields[k]) for k in EXTRA_FIELDS if fields[k] not in WILDCARDS]
        if all(v in words for v in extras):
            matching.append((len(extras), entry))

    if matching:
        most_extras = max(n for n, _ in matching)
        best = [e for n, e in matching if n == most_extras]
        if len(best) == 1:
            return best[0], "aus mehreren Einträgen per Produktname gewählt"
    return None, f"{len(entries)} Einträge mit dieser Version, keiner eindeutig"


def resolve_version(nvd: NvdClient, vp: VendorProduct, inventory_version: str | None,
                    inventory_product: str | None, origin: str) -> Resolution:
    """Baut aus bestätigtem vendor:product und Inventarversion die CPE."""
    version_raw = "" if inventory_version is None else str(inventory_version).strip()
    base = f"{vp.vendor}:{vp.product} {origin}"

    if UNKNOWN_VERSION.match(version_raw):
        return Resolution(S_BUILD, build_cpe(*vp),
                          f'{base}; Inventarversion "{version_raw}" -> "*"')
    if not NVD_LIKE_VERSION.match(version_raw):
        return manual(f'{base}; Version "{version_raw}" nicht NVD-konform -> Override nötig')

    version = version_raw.lower()
    pattern = f"cpe:2.3:{vp.part}:{vp.vendor}:{vp.product}:{escape(version)}"
    hits = active(nvd.products(cpeMatchString=pattern))
    exact = [h for h in hits if unescape(split_cpe(h["cpeName"])["version"]) == version]
    if not exact:
        return Resolution(S_VERSION, build_cpe(*vp, escape(version)),
                          f"{base}; Version {version} nicht im Dictionary")

    entry, note = pick_version_entry(exact, inventory_product)
    if entry is None:
        return manual(f"{base}; {note}", [e["cpeName"] for e in exact])
    return Resolution(S_EXACT, entry["cpeName"], f"{base}; {note}" if note else base)
