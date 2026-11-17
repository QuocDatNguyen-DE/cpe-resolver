#!/usr/bin/env python3
"""
cpe_resolver.py - PoC: bestimmt CPE-2.3-Namen für das Blatt "Assets" anhand des
offiziellen NVD-CPE-Dictionary (API 2.0) und schreibt sie in die Spalte "CPE".

Grundsatz: Es wird nichts geraten. Ein Asset bekommt nur dann eine CPE, wenn
vendor:product eindeutig aus der NVD bestimmt werden konnte (oder per Override
festgelegt ist). Alles andere wird als "Manuell prüfen" markiert und - je nach
--on-ambiguous - geleert oder unverändert gelassen.

Status (steht als Kommentar an der CPE-Zelle und im Report):
  NVD exakt               exakter, nicht veralteter Eintrag im NVD-Dictionary
  Version nicht gelistet  vendor:product bestätigt, Version nicht im Dictionary
                          -> CPE aus bestätigtem vendor:product + Inventarversion
  Build unbekannt         vendor:product bestätigt, Version unbekannt -> "*"
  Override                Zuordnung aus overrides.csv (gegen NVD geprüft)
  Manuell prüfen          kein / kein eindeutiger Treffer, Version nicht NVD-konform

Nutzung:
  python3 cpe_resolver.py Assetverzeichnis.xlsx                 # Probelauf, nur Report
  python3 cpe_resolver.py Assetverzeichnis.xlsx --apply         # schreibt <name>_cpe.xlsx
  Optionen: --overrides overrides.csv  --on-ambiguous clear|keep  --out datei.xlsx
  API-Key (optional): Umgebungsvariable NVD_API_KEY

Abhängigkeit: openpyxl (pip install openpyxl). HTTP läuft über die Standardbibliothek.

Begriffe im Code:
  inventory_*  Werte aus dem Assetverzeichnis (Hersteller, Produkt, Version)
  vendor/product/part  Felder eines CPE-Namens aus der NVD
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import NamedTuple

import openpyxl
from openpyxl.comments import Comment

# --------------------------------------------------------------------------- Konfiguration

NVD_URL = "https://services.nvd.nist.gov/rest/json/cpes/2.0"
# NVD-Rate-Limit: ohne Key 5 Requests / 30 s, mit Key 50 / 30 s
NVD_INTERVAL_WITH_KEY = 0.7
NVD_INTERVAL_WITHOUT_KEY = 6.5
NVD_RETRY_HTTP_CODES = (403, 429, 500, 502, 503)
NVD_MAX_ATTEMPTS = 4
NVD_RETRY_WAIT = 30

SHEET = "Assets"
# interner Schlüssel -> Spaltenüberschrift im Blatt
HEADERS = {"id": "ID", "vendor": "Hersteller", "product": "Produkt",
           "version": "Version", "cpe": "CPE"}

CPE_FIELDS = ["part", "vendor", "product", "version", "update", "edition",
              "language", "sw_edition", "target_sw", "target_hw", "other"]
EXTRA_FIELDS = CPE_FIELDS[4:]  # alles nach version
WILDCARDS = {"*", "-"}

# Inventar-Werte, die "Version unbekannt" bedeuten
UNKNOWN_VERSION = re.compile(r"^\s*(build\s+offen|offen|unbekannt|n/?a|-|–|)\s*$", re.I)
# Grobe Plausibilität: NVD-Versionen beginnen mit einer Ziffer (17.5.0, 2022.1, 6.4.3 ...)
NVD_LIKE_VERSION = re.compile(r"^\d+([._\-+][0-9a-z]+)*$", re.I)
# Kürzel im Produktnamen -> Feldwerte im NVD-Dictionary
TOKEN_ALIASES = {"ce": "community", "ee": "enterprise"}

S_EXACT = "NVD exakt"
S_VERSION = "Version nicht gelistet"
S_BUILD = "Build unbekannt"
S_OVERRIDE = "Override"
S_MANUAL = "Manuell prüfen"

MAX_CANDIDATES_IN_COMMENT = 15


# --------------------------------------------------------------------------- CPE-Strings

def split_cpe(cpe: str) -> dict:
    """Zerlegt einen CPE-2.3-Formatted-String an nicht maskierten Doppelpunkten."""
    parts, current, escaped = [], [], False
    for ch in cpe:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            current.append(ch)
            escaped = True
        elif ch == ":":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))

    if len(parts) != 13 or parts[0] != "cpe" or parts[1] != "2.3":
        raise ValueError(f"kein gültiger CPE-2.3-Name: {cpe}")
    return dict(zip(CPE_FIELDS, parts[2:]))


def unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def escape(value: str) -> str:
    """Maskiert Sonderzeichen für den Formatted String (z. B. & -> \\&)."""
    return "".join(c if re.match(r"[a-z0-9._\-]", c) else "\\" + c for c in value.lower())


def build_cpe(part: str, vendor: str, product: str, version: str = "*") -> str:
    return ":".join(["cpe", "2.3", part, vendor, product, version] + ["*"] * 7)


# --------------------------------------------------------------------------- Text

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


# --------------------------------------------------------------------------- NVD

class NvdClient:
    """Minimaler Client für die CPE-API 2.0 mit Datei-Cache und Rate-Limit."""

    def __init__(self, cache_dir: Path, api_key: str | None = None):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.min_interval = NVD_INTERVAL_WITH_KEY if api_key else NVD_INTERVAL_WITHOUT_KEY
        self._last_request = 0.0

    def products(self, **params) -> list[dict]:
        """Alle Treffer (paginiert) als Liste von {cpeName, deprecated, title}."""
        cache_file = self._cache_file(params)
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))

        products, start = [], 0
        while True:
            page = self._request({**params, "resultsPerPage": 10000, "startIndex": start})
            products += [self._simplify(p["cpe"]) for p in page.get("products", [])]
            start += page.get("resultsPerPage", 0)
            if start >= page.get("totalResults", 0) or not page.get("resultsPerPage"):
                break

        cache_file.write_text(json.dumps(products), encoding="utf-8")
        return products

    def _cache_file(self, params: dict) -> Path:
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    @staticmethod
    def _simplify(cpe: dict) -> dict:
        title = next((t["title"] for t in cpe.get("titles", []) if t.get("lang") == "en"), "")
        return {"cpeName": cpe["cpeName"], "deprecated": cpe.get("deprecated", False),
                "title": title}

    def _request(self, params: dict) -> dict:
        url = f"{NVD_URL}?{urllib.parse.urlencode(params, safe=':*')}"
        headers = {"User-Agent": "cpe-resolver-poc/0.1"}
        if self.api_key:
            headers["apiKey"] = self.api_key

        for attempt in range(NVD_MAX_ATTEMPTS):
            self._wait_for_rate_limit()
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                is_last_attempt = attempt == NVD_MAX_ATTEMPTS - 1
                if e.code in NVD_RETRY_HTTP_CODES and not is_last_attempt:
                    print(f"  NVD HTTP {e.code}, warte {NVD_RETRY_WAIT} s ...", file=sys.stderr)
                    time.sleep(NVD_RETRY_WAIT)
                    continue
                raise
        raise RuntimeError("NVD nicht erreichbar")

    def _wait_for_rate_limit(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()


def active(products: list[dict]) -> list[dict]:
    """Nur nicht veraltete (deprecated) Einträge."""
    return [p for p in products if not p["deprecated"]]


# --------------------------------------------------------------------------- Ergebnistypen

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


# --------------------------------------------------------------------------- vendor:product bestimmen

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


# --------------------------------------------------------------------------- Version bestimmen

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


# --------------------------------------------------------------------------- Asset auflösen

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
                                 row["product"], "(Override)")
    if resolution.status != S_MANUAL:
        resolution.status = S_OVERRIDE
    return resolution


def resolve_from_nvd(nvd: NvdClient, row: dict) -> Resolution:
    search = find_vendor_product(nvd, row["vendor"] or "", row["product"] or "")
    if search.match is None:
        return manual(search.reason, search.candidates)
    return resolve_version(nvd, search.match, row["version"], row["product"],
                           f"({search.reason})")


def resolve_row(nvd: NvdClient, row: dict, overrides: dict) -> Result:
    if row["id"] in overrides:
        resolution = resolve_override(nvd, overrides[row["id"]], row)
    else:
        resolution = resolve_from_nvd(nvd, row)
    return Result(row["id"], row["cpe"], resolution.cpe, resolution.status,
                  resolution.detail, resolution.candidates)


# --------------------------------------------------------------------------- Ein-/Ausgabe

def load_overrides(path: Path | None) -> dict:
    """overrides.csv -> {asset_id: Zeile}; Zeilen mit # werden ignoriert."""
    if not path or not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f, delimiter=";")
                if r.get("asset_id") and not r["asset_id"].startswith("#")]
    return {r["asset_id"].strip(): r for r in rows}


def header_map(ws) -> dict:
    """{interner Schlüssel: Spaltennummer}; bricht ab, wenn Spalten fehlen."""
    columns = {str(c.value).strip(): c.column for c in ws[1] if c.value is not None}
    missing = [h for h in HEADERS.values() if h not in columns]
    if missing:
        sys.exit(f"Spalten fehlen im Blatt {SHEET}: {missing}")
    return {key: columns[header] for key, header in HEADERS.items()}


def read_assets(ws, columns: dict):
    """Liefert (Zeilennummer, Zeile) für jede Zeile mit ID; Werte als getrimmte Strings."""
    for row_no in range(2, ws.max_row + 1):
        raw = {key: ws.cell(row_no, col).value for key, col in columns.items()}
        if not raw["id"]:
            continue
        yield row_no, {k: (str(v).strip() if v is not None else None) for k, v in raw.items()}


def write_cpe_cell(cell, result: Result, clear_if_manual: bool) -> None:
    """Schreibt die CPE (oder leert die Zelle) und hängt den Status als Kommentar an."""
    if result.new:
        cell.value = result.new
    elif clear_if_manual:
        cell.value = None

    text = f"CPE-Status: {result.status}\n{result.detail}"
    if result.candidates:
        text += "\nKandidaten:\n" + "\n".join(result.candidates[:MAX_CANDIDATES_IN_COMMENT])
    text += f"\nQuelle: NVD CPE API 2.0, {date.today():%d.%m.%Y}"
    cell.comment = Comment(text, "cpe_resolver")
    cell.comment.width, cell.comment.height = 420, 160


def write_report(path: Path, results: list[Result]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["asset_id", "status", "cpe_alt", "cpe_neu", "geaendert", "detail",
                         "kandidaten"])
        for r in results:
            changed = (r.new or None) != (r.old or None)
            writer.writerow([r.asset_id, r.status, r.old or "", r.new or "",
                             "ja" if changed else "nein", r.detail, " | ".join(r.candidates)])


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--apply", action="store_true", help="Ergebnis schreiben (sonst Probelauf)")
    ap.add_argument("--out", type=Path, help="Zieldatei (Standard: <name>_cpe.xlsx)")
    ap.add_argument("--overrides", type=Path, default=Path("overrides.csv"))
    ap.add_argument("--on-ambiguous", choices=["clear", "keep"], default="clear",
                    help="CPE-Zelle bei 'Manuell prüfen' leeren oder alten Wert behalten")
    ap.add_argument("--report", type=Path, default=Path("cpe_report.csv"))
    ap.add_argument("--cache-dir", type=Path, default=Path(".nvd_cache"))
    return ap.parse_args(argv)


# --------------------------------------------------------------------------- main

def main(argv=None, nvd: NvdClient | None = None) -> list[Result]:
    args = parse_args(argv)
    nvd = nvd or NvdClient(args.cache_dir, os.environ.get("NVD_API_KEY"))
    overrides = load_overrides(args.overrides)

    wb = openpyxl.load_workbook(args.xlsx)  # Formeln bleiben als Formeln erhalten
    ws = wb[SHEET]
    columns = header_map(ws)

    results = []
    for row_no, row in read_assets(ws, columns):
        print(f"{row['id']}: {row['vendor']} / {row['product']} / {row['version']}")
        result = resolve_row(nvd, row, overrides)
        print(f"   -> [{result.status}] {result.new or '-'}")
        results.append(result)
        write_cpe_cell(ws.cell(row_no, columns["cpe"]), result,
                       clear_if_manual=args.on_ambiguous == "clear")

    write_report(args.report, results)

    counts = Counter(r.status for r in results)
    print("\nZusammenfassung:", ", ".join(f"{k}: {v}" for k, v in counts.items()))
    print(f"Report: {args.report}")
    if args.apply:
        out = args.out or args.xlsx.with_name(args.xlsx.stem + "_cpe.xlsx")
        wb.save(out)
        print(f"Geschrieben: {out}")
    else:
        print("Probelauf - nichts geschrieben (mit --apply speichern).")
    return results


if __name__ == "__main__":
    main()
