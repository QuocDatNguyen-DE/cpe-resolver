"""Wortzerlegung und Namensvergleich zwischen Inventar und NVD."""
from __future__ import annotations

import re


def tokens(text: str | None, drop_parens: bool) -> list[str]:
    """Kleingeschriebene Wörter aus a-z/0-9; optional ohne Klammerinhalte."""
    text = (text or "").lower()
    if drop_parens:
        text = re.sub(r"\([^)]*\)", " ", text)
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def has_digit(word: str) -> bool:
    return bool(re.search(r"\d", word))


def slug(words: list[str]) -> str:
    return "_".join(words)


def vendor_matches(nvd_vendor: str, inventory_vendor: str) -> bool:
    """Passt der NVD-Vendor (z. B. "microsoft") zum Hersteller aus dem Inventar?"""
    inventory_words = tokens(inventory_vendor, drop_parens=False)
    nvd_words = {t for t in nvd_vendor.split("_") if t}
    return (nvd_vendor.replace("_", "") in "".join(inventory_words)
            or nvd_words <= set(inventory_words))
