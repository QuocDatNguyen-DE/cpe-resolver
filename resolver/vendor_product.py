"""vendor:product eines Assets per Stichwortsuche im NVD-Dictionary bestimmen."""
from __future__ import annotations

import re

from .cpe import split_cpe, unescape
from .model import ProductSearch, VendorProduct
from .nvd import NvdClient, active
from .text import has_digit, slug, tokens, vendor_matches


def build_search_queries(vendor_words: list[str], product_words: list[str]) -> list[list[str]]:
    """Suchanfragen von spezifisch nach allgemein.

    1. Hersteller + Produkt, 2. nur Produkt, danach das Produkt schrittweise von
    hinten um beschreibende Wörter (Standard, Software, Self-Managed ...) gekürzt.
    Wörter mit Ziffern (Release-Kennungen) werden nie weggekürzt.
    """
    queries = [vendor_words + product_words, product_words]
    shortened = list(product_words)
    while len(shortened) > 1:
        last_plain = next((i for i in reversed(range(len(shortened)))
                           if not has_digit(shortened[i])), None)
        if last_plain is None:
            break
        del shortened[last_plain]
        queries.append(list(shortened))
    return queries


def collect_candidates(hits: list[dict], inventory_vendor: str,
                       required: list[str]) -> list[VendorProduct]:
    """vendor:product-Kandidaten aus den Suchtreffern.

    Ein Kandidat zählt nur, wenn sein Vendor zum Inventar-Hersteller passt und
    jede Release-Kennung im Produktnamen oder in seinen Versionen vorkommt
    (verhindert z. B. "Windows 10" -> windows_11_23h2).
    """
    evidence: dict[VendorProduct, set[str]] = {}
    for hit in hits:
        fields = split_cpe(hit["cpeName"])
        if not vendor_matches(fields["vendor"], inventory_vendor):
            continue
        key = VendorProduct(fields["part"], fields["vendor"], fields["product"])
        words = evidence.setdefault(key, set(fields["product"].split("_")))
        version = unescape(fields["version"]).lower()
        words.update(t for t in re.split(r"[._\-]", version) if t)
    return sorted(key for key, words in evidence.items() if all(r in words for r in required))


def find_vendor_product(nvd: NvdClient, inventory_vendor: str,
                        inventory_product: str) -> ProductSearch:
    vendor_words = tokens(inventory_vendor, drop_parens=True)
    product_words = tokens(inventory_product, drop_parens=True)
    # Release-Kennungen (2019, 2025, 11, 23h2 ...) bestimmen das Produkt
    required = [w for w in product_words if has_digit(w)]

    seen = set()
    for words in build_search_queries(vendor_words, product_words):
        query = " ".join(words)
        if not query or query in seen:
            continue
        seen.add(query)

        hits = active(nvd.products(keywordSearch=query))
        candidates = collect_candidates(hits, inventory_vendor, required)
        if not candidates:
            continue  # nächste, allgemeinere Suche
        if len(candidates) == 1:
            return ProductSearch(candidates[0], f'eindeutig per Suche "{query}"')

        # Mehrere Kandidaten: eindeutig, wenn genau einer wie das Produkt bzw. die Suche heißt
        expected_names = {slug(product_words), slug(words),
                          slug([w for w in product_words if w not in vendor_words])}
        same_name = [c for c in candidates if c.product in expected_names]
        if len(same_name) == 1:
            return ProductSearch(same_name[0], f'Namensgleichheit bei Suche "{query}" '
                                               f'({len(candidates)} Kandidaten)')
        return ProductSearch(None, f'{len(candidates)} Kandidaten bei Suche "{query}"',
                             [str(c) for c in candidates])

    return ProductSearch(None, "kein Treffer im NVD-Dictionary")
