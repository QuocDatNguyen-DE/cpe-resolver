"""vendor:product eines Assets per Stichwortsuche im NVD-Dictionary bestimmen."""
from __future__ import annotations

import re

from .cpe import split_cpe, unescape
from .model import ProductSearch, VendorProduct
from .nvd import NvdClient, SearchTooBroad, active
from .text import has_digit, name_key, slug, tokens, vendor_key, vendor_matches


def build_search_queries(vendor_words: list[str], product_words: list[str]) -> list[list[str]]:
    """Suchanfragen von spezifisch nach allgemein.

    1. Hersteller + Produkt, 2. nur Produkt, danach das Produkt schrittweise von
    hinten um beschreibende Wörter (Standard, Software, Self-Managed ...) gekürzt.
    Wörter mit Ziffern (Release-Kennungen) werden nie weggekürzt, und das letzte
    beschreibende Wort bleibt stehen (eine Suche nur nach "2019" findet Beliebiges).
    """
    queries = [vendor_words + product_words, product_words]
    shortened = list(product_words)
    while sum(not has_digit(w) for w in shortened) > 1:
        last_plain = next(i for i in reversed(range(len(shortened)))
                          if not has_digit(shortened[i]))
        del shortened[last_plain]
        queries.append(list(shortened))
    return queries


def collect_candidates(hits: list[dict], inventory_vendor: str, required: list[str],
                       aliases: dict[str, set[str]] | None = None,
                       any_vendor: bool = False) -> list[VendorProduct]:
    """vendor:product-Kandidaten aus den Suchtreffern.

    Ein Kandidat zählt nur, wenn sein Vendor zum Inventar-Hersteller passt (außer bei
    any_vendor) und jede Release-Kennung im Produktnamen, in seinen Versionen oder
    Update-Feldern vorkommt (verhindert z. B. "Windows 10" -> windows_11_23h2;
    "Windows 7 SP1" findet windows_7 über update=sp1).
    """
    evidence: dict[VendorProduct, set[str]] = {}
    for hit in hits:
        fields = split_cpe(hit["cpeName"])
        if not any_vendor and not vendor_matches(fields["vendor"], inventory_vendor, aliases):
            continue
        key = VendorProduct(fields["part"], fields["vendor"], fields["product"])
        words = evidence.setdefault(key, set(unescape(fields["product"]).split("_")))
        for field in ("version", "update"):
            value = unescape(fields[field]).lower()
            words.add(value)
            words.update(t for t in re.split(r"[._\-]", value) if t)
    return sorted(key for key, words in evidence.items() if all(r in words for r in required))


def name_tiers(vendor_words: list[str], product_words: list[str], query_words: list[str],
               inventory_vendor: str, inventory_product: str):
    """Namensvergleiche von streng nach locker; jede Stufe ist eine Funktion
    Kandidat -> passt der NVD-Produktname zum Inventar? (Vergleich ohne Trennzeichen)"""
    vendor_amp = tokens(inventory_vendor, drop_parens=True, keep_ampersand=True)
    product_amp = tokens(inventory_product, drop_parens=True, keep_ampersand=True)
    full = {name_key(slug(names)) for names in (
        product_words, query_words, [w for w in product_words if w not in vendor_words],
        vendor_amp + product_amp, product_amp, [w for w in product_amp if w not in vendor_amp])}

    # Die lockeren Stufen vergleichen nur mit dem vollständigen Produktnamen, nicht mit
    # der gekürzten Suche (sonst würde "Office 2010 Professional Plus" zu office)
    def without_candidate_vendor(c: VendorProduct) -> str:
        # Wörter, die im NVD-Vendor stecken, gehören nicht zum Produktnamen
        # (Pulse Connect Secure bei pulsesecure -> connect_secure)
        v = vendor_key(c.vendor)
        return name_key(slug([w for w in product_words
                              if not (len(name_key(w)) >= 3 and name_key(w) in v)]))

    # ohne Release-Kennungen (Struts 2 -> struts, Exchange Server 2010 -> exchange_server)
    no_release = name_key(slug([w for w in product_words if not has_digit(w)]))

    return [
        ("Namensgleichheit", lambda c: name_key(c.product) in full),
        ("Namensgleichheit ohne Herstellerwörter",
         lambda c: name_key(c.product) == without_candidate_vendor(c) != ""),
        ("Namensgleichheit ohne Release-Kennung", lambda c: name_key(c.product) == no_release != ""),
    ]


def find_vendor_product(nvd: NvdClient, inventory_vendor: str, inventory_product: str,
                        aliases: dict[str, set[str]] | None = None) -> ProductSearch:
    vendor_words = tokens(inventory_vendor, drop_parens=True)
    product_words = tokens(inventory_product, drop_parens=True)
    # Release-Kennungen (2019, 2025, 11, 23h2, 14.04 ...) bestimmen das Produkt
    required = [w for w in product_words if has_digit(w)]

    seen, too_broad, suggestions, unnamed = set(), [], set(), None
    for words in build_search_queries(vendor_words, product_words):
        query = " ".join(words)
        if not query or query in seen:
            continue
        seen.add(query)

        try:
            hits = active(nvd.products(keywordSearch=query))
        except SearchTooBroad:
            too_broad.append(query)
            continue  # zählt wie kein Treffer
        tiers = name_tiers(vendor_words, product_words, words, inventory_vendor,
                           inventory_product)
        candidates = collect_candidates(hits, inventory_vendor, required, aliases)
        if not candidates:
            # Hersteller passt nicht: gleichnamige Produkte nur als Vorschlag merken
            suggestions |= {str(c) for c in collect_candidates(hits, "", required, any_vendor=True)
                            if tiers[0][1](c)}
            continue  # nächste, allgemeinere Suche
        if len(candidates) == 1:
            return ProductSearch(candidates[0], f'eindeutig per Suche "{query}"')

        # Mehrere Kandidaten: eindeutig, wenn in der strengsten greifenden Stufe genau
        # einer wie das Produkt heißt
        for label, matches in tiers:
            same_name = [c for c in candidates if matches(c)]
            if len(same_name) == 1:
                return ProductSearch(same_name[0], f'{label} bei Suche "{query}" '
                                                   f'({len(candidates)} Kandidaten)')
            if same_name:
                return ProductSearch(None, f'{len(same_name)} gleichnamige Kandidaten bei '
                                           f'Suche "{query}" ({label})',
                                     [str(c) for c in same_name])
        # keiner heißt wie das Produkt (z. B. nur Plugins gefunden): allgemeinere Suche
        # versuchen, die Kandidaten aber für die Meldung merken
        unnamed = unnamed or ProductSearch(None, f'{len(candidates)} Kandidaten bei Suche '
                                                 f'"{query}"', [str(c) for c in candidates])

    if unnamed:
        return unnamed
    if suggestions:
        return ProductSearch(None, "Hersteller passt zu keinem NVD-Vendor; gleichnamige "
                                   "Produkte anderer Vendors als Vorschlag -> nach Prüfung in "
                                   "vendor_aliases.csv eintragen", sorted(suggestions))
    if too_broad:
        searches = ", ".join(f'"{q}"' for q in too_broad)
        return ProductSearch(None, f"Suche zu breit für die NVD ({searches}) -> Override nötig")
    return ProductSearch(None, "kein Treffer im NVD-Dictionary")
