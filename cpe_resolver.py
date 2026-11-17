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
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

NVD_URL = "https://services.nvd.nist.gov/rest/json/cpes/2.0"
SHEET = "Assets"
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


# --------------------------------------------------------------------------- CPE

def split_cpe(cpe: str) -> dict:
    """Zerlegt einen CPE-2.3-Formatted-String an nicht maskierten Doppelpunkten."""
    parts, cur, esc = [], [], False
    for ch in cpe:
        if esc:
            cur.append(ch); esc = False
        elif ch == "\\":
            cur.append(ch); esc = True
        elif ch == ":":
            parts.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
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

def tokens(text: str, drop_parens: bool) -> list[str]:
    text = (text or "").lower()
    if drop_parens:
        text = re.sub(r"\([^)]*\)", " ", text)
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def slug(words: list[str]) -> str:
    return "_".join(words)


def vendor_matches(nvd_vendor: str, hersteller: str) -> bool:
    h_tokens = set(tokens(hersteller, drop_parens=False))
    h_compact = "".join(tokens(hersteller, drop_parens=False))
    v_tokens = [t for t in nvd_vendor.split("_") if t]
    return nvd_vendor.replace("_", "") in h_compact or set(v_tokens) <= h_tokens


# --------------------------------------------------------------------------- NVD

class NvdClient:
    """Minimaler Client für die CPE-API 2.0 mit Datei-Cache und Rate-Limit."""

    def __init__(self, cache_dir: Path, api_key: str | None = None):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        # NVD: ohne Key 5 Requests / 30 s, mit Key 50 / 30 s
        self.min_interval = 0.7 if api_key else 6.5
        self._last = 0.0

    def products(self, **params) -> list[dict]:
        """Alle Treffer (paginiert) als Liste von {cpeName, deprecated, title}."""
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))

        out, start = [], 0
        while True:
            data = self._request({**params, "resultsPerPage": 10000, "startIndex": start})
            for p in data.get("products", []):
                c = p["cpe"]
                title = next((t["title"] for t in c.get("titles", []) if t.get("lang") == "en"), "")
                out.append({"cpeName": c["cpeName"], "deprecated": c.get("deprecated", False),
                            "title": title})
            start += data.get("resultsPerPage", 0)
            if start >= data.get("totalResults", 0) or not data.get("resultsPerPage"):
                break
        cache_file.write_text(json.dumps(out), encoding="utf-8")
        return out

    def _request(self, params: dict) -> dict:
        url = f"{NVD_URL}?{urllib.parse.urlencode(params, safe=':*')}"
        headers = {"User-Agent": "cpe-resolver-poc/0.1"}
        if self.api_key:
            headers["apiKey"] = self.api_key
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                            timeout=60) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (403, 429, 500, 502, 503) and attempt < 3:
                    print(f"  NVD HTTP {e.code}, warte 30 s ...", file=sys.stderr)
                    time.sleep(30)
                    continue
                raise
        raise RuntimeError("NVD nicht erreichbar")


def active(products: list[dict]) -> list[dict]:
    return [p for p in products if not p["deprecated"]]


# --------------------------------------------------------------------------- Logik

@dataclass
class Result:
    asset_id: str
    old: str | None
    new: str | None
    status: str
    detail: str = ""
    candidates: list[str] = field(default_factory=list)


def find_vendor_product(nvd: NvdClient, hersteller: str, produkt: str):
    """Liefert (part, vendor, product) oder (None, Kandidatenliste, Begründung)."""
    v_words = tokens(hersteller, drop_parens=True)
    p_words = tokens(produkt, drop_parens=True)
    # Release-Kennungen (2019, 2025, 11, 23h2 ...) bestimmen das Produkt und dürfen
    # weder weggekürzt werden noch im Treffer fehlen.
    required = [w for w in p_words if re.search(r"\d", w)]
    queries = [v_words + p_words, p_words]
    shortened = list(p_words)
    while True:  # hinten beschreibende Wörter kürzen (Standard, Software, Self-Managed ...)
        idx = next((i for i in range(len(shortened) - 1, -1, -1)
                    if not re.search(r"\d", shortened[i])), None)
        if idx is None or len(shortened) <= 1:
            break
        shortened = shortened[:idx] + shortened[idx + 1:]
        queries.append(list(shortened))

    seen = set()
    for words in queries:
        q = " ".join(words)
        if not q or q in seen:
            continue
        seen.add(q)
        hits = active(nvd.products(keywordSearch=q))
        evidence: dict[tuple, set] = {}
        for h in hits:
            f = split_cpe(h["cpeName"])
            if not vendor_matches(f["vendor"], hersteller):
                continue
            key = (f["part"], f["vendor"], f["product"])
            ev = evidence.setdefault(key, set(f["product"].split("_")))
            ev.update(t for t in re.split(r"[._\-]", unescape(f["version"]).lower()) if t)
        # Kandidat nur, wenn jede Release-Kennung im Produktnamen oder in seinen
        # Versionsfeldern steckt (verhindert z. B. "Windows 10" -> windows_11_23h2)
        cands = sorted(k for k, ev in evidence.items() if all(r in ev for r in required))
        if not cands:
            continue
        if len(cands) == 1:
            return cands[0], f'eindeutig per Suche "{q}"'
        # Namensgleichheit: genau ein Kandidat heißt wie das Produkt bzw. die Suche
        names = {slug(p_words), slug(words),
                 slug([w for w in p_words if w not in v_words])}
        exact = [c for c in cands if c[2] in names]
        if len(exact) == 1:
            return exact[0], f'Namensgleichheit bei Suche "{q}" ({len(cands)} Kandidaten)'
        return None, [":".join(c) for c in cands], f'{len(cands)} Kandidaten bei Suche "{q}"'
    return None, [], "kein Treffer im NVD-Dictionary"


def pick_version_entry(entries: list[dict], produkt: str):
    """Wählt bei mehreren exakten Versionseinträgen den passenden (Edition o. ä.)."""
    if len(entries) == 1:
        return entries[0], ""
    words = set(tokens(produkt, drop_parens=False))
    words |= {TOKEN_ALIASES[w] for w in words if w in TOKEN_ALIASES}

    def explained(fields):
        vals = [unescape(fields[k]) for k in EXTRA_FIELDS if fields[k] not in WILDCARDS]
        return all(v in words for v in vals), len(vals)

    scored = []
    for e in entries:
        ok, n = explained(split_cpe(e["cpeName"]))
        if ok:
            scored.append((n, e))
    if scored:
        best = max(n for n, _ in scored)
        top = [e for n, e in scored if n == best]
        if len(top) == 1:
            return top[0], "aus mehreren Einträgen per Produktname gewählt"
    return None, f"{len(entries)} Einträge mit dieser Version, keiner eindeutig"


def resolve_version(nvd, part, vendor, product, version_raw, produkt, origin) -> Result:
    version_raw = "" if version_raw is None else str(version_raw).strip()
    base = f"{vendor}:{product} {origin}"

    if UNKNOWN_VERSION.match(version_raw):
        return Result("", None, build_cpe(part, vendor, product), S_BUILD,
                      f'{base}; Inventarversion "{version_raw}" -> "*"')
    if not NVD_LIKE_VERSION.match(version_raw):
        return Result("", None, None, S_MANUAL,
                      f'{base}; Version "{version_raw}" nicht NVD-konform -> Override nötig')

    v = version_raw.lower()
    hits = active(nvd.products(cpeMatchString=f"cpe:2.3:{part}:{vendor}:{product}:{escape(v)}"))
    exact = [h for h in hits if unescape(split_cpe(h["cpeName"])["version"]) == v]
    if not exact:
        return Result("", None, build_cpe(part, vendor, product, escape(v)), S_VERSION,
                      f"{base}; Version {v} nicht im Dictionary")
    entry, note = pick_version_entry(exact, produkt)
    if entry is None:
        return Result("", None, None, S_MANUAL, f"{base}; {note}",
                      [e["cpeName"] for e in exact])
    return Result("", None, entry["cpeName"], S_EXACT, f"{base}; {note}".rstrip("; "))


def resolve_override(nvd, ov: dict, row: dict) -> Result:
    if ov.get("cpe"):
        cpe = ov["cpe"].strip()
        split_cpe(cpe)  # Formatprüfung
        hits = active(nvd.products(cpeMatchString=cpe))
        found = any(h["cpeName"] == cpe for h in hits)
        return Result("", None, cpe, S_OVERRIDE,
                      "volle CPE aus overrides.csv; " +
                      ("exakt im NVD-Dictionary" if found else "NICHT im NVD-Dictionary"))
    vendor, product = ov["vendor"].strip(), ov["product"].strip()
    hits = active(nvd.products(cpeMatchString=f"cpe:2.3:*:{vendor}:{product}"))
    parts = sorted({split_cpe(h["cpeName"])["part"] for h in hits})
    if len(parts) != 1:
        return Result("", None, None, S_MANUAL,
                      f"Override {vendor}:{product} nicht (eindeutig) im NVD-Dictionary")
    version = ov.get("version") or row["version"]
    r = resolve_version(nvd, parts[0], vendor, product, version, row["product"],
                        "(Override)")
    r.status = S_OVERRIDE if r.status != S_MANUAL else r.status
    return r


def resolve_row(nvd: NvdClient, row: dict, overrides: dict) -> Result:
    if row["id"] in overrides:
        r = resolve_override(nvd, overrides[row["id"]], row)
    else:
        found = find_vendor_product(nvd, row["vendor"] or "", row["product"] or "")
        if found[0] is None:
            _, cands, why = found
            r = Result("", None, None, S_MANUAL, why, cands)
        else:
            (part, vendor, product), how = found
            r = resolve_version(nvd, part, vendor, product, row["version"], row["product"],
                                f"({how})")
    r.asset_id, r.old = row["id"], row["cpe"]
    return r


# --------------------------------------------------------------------------- Excel

def load_overrides(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f, delimiter=";")
                if r.get("asset_id") and not r["asset_id"].startswith("#")]
    return {r["asset_id"].strip(): r for r in rows}


def header_map(ws) -> dict:
    hdr = {str(c.value).strip(): c.column for c in ws[1] if c.value is not None}
    missing = [h for h in HEADERS.values() if h not in hdr]
    if missing:
        sys.exit(f"Spalten fehlen im Blatt {SHEET}: {missing}")
    return {k: hdr[v] for k, v in HEADERS.items()}


def main(argv=None, nvd: NvdClient | None = None) -> list[Result]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--apply", action="store_true", help="Ergebnis schreiben (sonst Probelauf)")
    ap.add_argument("--out", type=Path, help="Zieldatei (Standard: <name>_cpe.xlsx)")
    ap.add_argument("--overrides", type=Path, default=Path("overrides.csv"))
    ap.add_argument("--on-ambiguous", choices=["clear", "keep"], default="clear",
                    help="CPE-Zelle bei 'Manuell prüfen' leeren oder alten Wert behalten")
    ap.add_argument("--report", type=Path, default=Path("cpe_report.csv"))
    ap.add_argument("--cache-dir", type=Path, default=Path(".nvd_cache"))
    args = ap.parse_args(argv)

    import openpyxl
    from openpyxl.comments import Comment

    nvd = nvd or NvdClient(args.cache_dir, os.environ.get("NVD_API_KEY"))
    overrides = load_overrides(args.overrides)
    wb = openpyxl.load_workbook(args.xlsx)  # Formeln bleiben als Formeln erhalten
    ws = wb[SHEET]
    cols = header_map(ws)

    results = []
    for r in range(2, ws.max_row + 1):
        row = {k: ws.cell(r, c).value for k, c in cols.items()}
        if not row["id"]:
            continue
        row = {k: (str(v).strip() if v is not None else None) for k, v in row.items()}
        print(f"{row['id']}: {row['vendor']} / {row['product']} / {row['version']}")
        res = resolve_row(nvd, row, overrides)
        print(f"   -> [{res.status}] {res.new or '-'}")
        results.append(res)

        cell = ws.cell(r, cols["cpe"])
        if res.new:
            cell.value = res.new
        elif args.on_ambiguous == "clear":
            cell.value = None
        text = f"CPE-Status: {res.status}\n{res.detail}"
        if res.candidates:
            text += "\nKandidaten:\n" + "\n".join(res.candidates[:15])
        text += f"\nQuelle: NVD CPE API 2.0, {date.today():%d.%m.%Y}"
        cell.comment = Comment(text, "cpe_resolver")
        cell.comment.width, cell.comment.height = 420, 160

    with args.report.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["asset_id", "status", "cpe_alt", "cpe_neu", "geaendert", "detail",
                    "kandidaten"])
        for res in results:
            w.writerow([res.asset_id, res.status, res.old or "", res.new or "",
                        "ja" if (res.new or None) != (res.old or None) else "nein",
                        res.detail, " | ".join(res.candidates)])

    counts = {}
    for res in results:
        counts[res.status] = counts.get(res.status, 0) + 1
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
