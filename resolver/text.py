"""Wortzerlegung und Namensvergleich zwischen Inventar und NVD."""
from __future__ import annotations

import re
import unicodedata

# Wort aus a-z/0-9; Punkte und Bindestriche im Wort bleiben erhalten (14.04, 7-zip,
# node.js), ebenso ein führender Punkt (.net) und angehängte Pluszeichen (notepad++).
WORD = r"\.?[a-z0-9]+(?:[.\-][a-z0-9]+)*\+*"


def ascii_lower(text: str | None) -> str:
    """Kleinschreibung, Umlaute/Akzente transliteriert (für -> fur), Zeichen wie ® entfernt."""
    text = (text or "").lower().replace("ß", "ss")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def tokens(text: str | None, drop_parens: bool, keep_ampersand: bool = False) -> list[str]:
    """Kleingeschriebene Wörter; optional ohne Klammerinhalte.

    keep_ampersand: "&" bleibt als eigenes Wort erhalten, weil es in
    NVD-Produktnamen vorkommt (veeam_backup_\\&_replication).
    """
    text = ascii_lower(text)
    if drop_parens:
        text = re.sub(r"\([^)]*\)", " ", text)
    return re.findall(WORD + ("|&" if keep_ampersand else ""), text)


def has_digit(word: str) -> bool:
    return bool(re.search(r"\d", word))


def slug(words: list[str]) -> str:
    return "_".join(words)


def name_key(name: str) -> str:
    """Vergleichsschlüssel für Produktnamen ohne Trenn- und Maskierungszeichen
    (node_js = node.js, joomla = joomla\\!, backup_&_replication = backup_\\&_replication)."""
    return re.sub(r"[^a-z0-9&+]", "", ascii_lower(name))


def vendor_key(name: str) -> str:
    """Vergleichsschlüssel für Hersteller: nur a-z/0-9, "+" als "plus"
    (Notepad++ = notepad-plus-plus, 7-Zip = 7-zip)."""
    return re.sub(r"[^a-z0-9]", "", ascii_lower(name).replace("+", "plus"))


def vendor_matches(nvd_vendor: str, inventory_vendor: str,
                   aliases: dict[str, set[str]] | None = None) -> bool:
    """Passt der NVD-Vendor (z. B. "microsoft") zum Hersteller aus dem Inventar?

    Der Vendor muss einem oder mehreren aufeinanderfolgenden Wörtern des Herstellers
    entsprechen ("Apache Software Foundation" -> apache); ein Teilstring reicht nicht
    ("mi" passt nicht auf "Mircosoft"). Zusätzlich gelten die Aliase aus
    vendor_aliases.csv (Igor Pavlov -> 7-zip).
    """
    nvd = vendor_key(nvd_vendor)
    if nvd in (aliases or {}).get(vendor_key(inventory_vendor), set()):
        return True
    words = [vendor_key(w) for w in tokens(inventory_vendor, drop_parens=False)]
    runs = {"".join(words[i:j]) for i in range(len(words)) for j in range(i + 1, len(words) + 1)}
    return nvd in runs
