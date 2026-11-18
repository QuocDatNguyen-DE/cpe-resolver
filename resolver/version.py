"""Aus bestätigtem vendor:product und Inventarversion die CPE bestimmen.

Die Inventarversion wird allgemein zerlegt (Bezeichnung, Kern, Update-Teil,
Zusatzangaben) und nur übernommen, wenn sie - ggf. in gleichwertiger Schreibweise -
exakt im NVD-Dictionary steht. Eine CPE mit nicht gelisteter Version wird nur gebaut,
wenn nichts dagegen spricht: kein Update-Teil geht verloren, kein verwandtes Produkt
desselben Herstellers führt die Version exakt, und das Versionsschema passt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .cpe import EXTRA_FIELDS, WILDCARDS, build_cpe, escape, split_cpe, unescape
from .model import S_BUILD, S_DERIVED, S_EXACT, S_VERSION, Resolution, VendorProduct, manual
from .nvd import NvdClient, active
from .text import has_digit, name_key, tokens

# Inventar-Werte, die "Version unbekannt" bedeuten
UNKNOWN_VERSION = re.compile(r"^\s*(build\s+offen|offen|unbekannt|n/?a|-|–|)\s*$", re.I)
# Grobe Plausibilität: NVD-Versionen beginnen mit einer Ziffer (17.5.0, 2022.1, 6.4.3 ...);
# Cisco-Notation mit Klammern ist erlaubt (12.2(55)se, 15.0(2)se11)
NVD_LIKE_VERSION = re.compile(r"^\d+([._\-+][0-9a-z]+|\(\d+[a-z]*\)[0-9a-z]*)*$", re.I)
# Vorangestellte Bezeichnung: "v2.4", "Version 3.8", "Rel. 5", "Build 1234"
VERSION_PREFIX = re.compile(r"^(?:version|ver\.?|v\.?|release|rel\.?|build)\s*(?=\d)")
# Langformen von Update-Bezeichnungen -> Kurzform, gleich für Inventar und NVD-Update-Feld
UPDATE_WORDS = [("cumulative update", "cu"), ("cumulative_update", "cu"),
                ("service pack", "sp"), ("service_pack", "sp"), ("servicepack", "sp"),
                ("update", "u"), ("patch", "p")]
# Eigenständiger Update-Teil: Buchstaben + Ziffer (sp1, u3, cu14, r11, p2, u3c)
UPDATE_TOKEN = re.compile(r"^[a-z]+\d+[a-z0-9]*$")
# Plattformangaben gehören nicht zur Version
PLATFORM_TOKEN = re.compile(r"^(x86|x64|x86_64|amd64|arm64|i386|\d+-?bit)$")
# An die Zahl angehängter Teil: 7.2p2, 9.1r11, 1.1.1k
ATTACHED_SUFFIX = re.compile(r"^(\d+(?:\.\d+)*)([a-z][a-z0-9_]*)$")
# Kürzel im Produktnamen -> Feldwerte im NVD-Dictionary
TOKEN_ALIASES = {"ce": "community", "ee": "enterprise"}


@dataclass
class InventoryVersion:
    """Zerlegte Inventarversion: "Version 6.5 U3 (x64)" -> core 6.5, update u3."""
    raw: str
    core: str | None
    update: str | None
    notes: list[str] = field(default_factory=list)


def parse_inventory_version(raw: str) -> InventoryVersion | None:
    text = raw.lower().strip()
    notes = []
    without_prefix = VERSION_PREFIX.sub("", text)
    if without_prefix != text:
        notes.append(f'Bezeichnung "{text[:len(text) - len(without_prefix)].strip()}" entfernt')
        text = without_prefix

    ignored = re.findall(r"\s\(([^)]*)\)", text)       # "1.18.0 (Ubuntu)", nicht "12.2(55)se"
    text = re.sub(r"\s\([^)]*\)", " ", text)
    for long, short in UPDATE_WORDS:
        text = text.replace(long, short)
    text = re.sub(r"\b(sp|cu|u|p|r)\s+(\d)", r"\1\2", text)   # "sp 1" -> "sp1"

    parts = [p for p in re.split(r"[\s,;/]+", text) if p]
    core = parts[0] if parts and parts[0][0].isdigit() else None
    update = None
    for part in parts[1:] if core else parts:
        if update is None and UPDATE_TOKEN.match(part) and not PLATFORM_TOKEN.match(part):
            update = part
        else:
            ignored.append(part)
    if core is None and update is None:
        return None
    if ignored:
        notes.append(f'Zusatz "{" ".join(ignored)}" ignoriert')
    return InventoryVersion(raw, core, update, notes)


def update_key(update: str) -> str:
    """Vergleichsform für Update-Angaben: update03 = U3, cumulative_update_14 = CU14."""
    value = unescape(update).lower()
    for long, short in UPDATE_WORDS:
        value = value.replace(long, short)
    value = re.sub(r"[^a-z0-9]", "", value)
    return re.sub(r"\d+", lambda m: str(int(m.group())), value)


def updates_match(nvd_update: str, inventory_update: str) -> bool:
    nvd, inventory = update_key(nvd_update), update_key(inventory_update)
    # die NVD führt "Update 2" teils nur als Zahl (vmware:esxi:6.5:2)
    return nvd == inventory or (nvd.isdigit() and inventory == f"u{nvd}")


def pick_version_entry(entries: list[dict], inventory_product: str, known: tuple[str, ...] = ()):
    """Wählt bei mehreren exakten Versionseinträgen den passenden (Edition o. ä.).

    Ein Eintrag passt, wenn alle seine gesetzten Zusatzfelder (update, edition,
    sw_edition ...) im Produktnamen oder in known (z. B. das bereits geprüfte Update)
    vorkommen. Unter den passenden gewinnt der mit den meisten Zusatzfeldern - aber
    nur, wenn er der einzige ist. Sonst gilt die gemeinsame CPE aller Einträge: Felder,
    in denen sie sich unterscheiden (Plattform, Edition ...), werden "*" - vendor,
    product und version sind ja bestätigt (Windows 10 22H2 x64/arm64/x86 -> "*").
    Rückgabe: (Eintrag, Hinweis).
    """
    if len(entries) == 1:
        return entries[0], ""

    words = set(tokens(inventory_product, drop_parens=False))
    words |= {TOKEN_ALIASES[w] for w in words if w in TOKEN_ALIASES}
    words |= set(known)

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
    return common_entry(entries)


def common_entry(entries: list[dict]) -> tuple[dict, str]:
    """Gemeinsame CPE mehrerer Einträge derselben Version: gleiche Felder bleiben,
    unterschiedliche werden "*"."""
    split = [split_cpe(e["cpeName"]) for e in entries]
    fields = dict(split[0])
    differing = [k for k in EXTRA_FIELDS if len({s[k] for s in split}) > 1]
    for k in differing:
        fields[k] = "*"
    cpe = ":".join(["cpe", "2.3"] + list(fields.values()))
    values = ", ".join(f"{k}=" + "/".join(sorted({unescape(s[k]) for s in split}))
                       for k in differing)
    return {"cpeName": cpe}, f"{len(entries)} Einträge mit dieser Version ({values}) -> \"*\""


def cell_type_problem(value) -> str | None:
    """Excel-Zelltypen, bei denen die Version nicht mehr sicher lesbar ist."""
    if isinstance(value, (datetime, date)):
        return (f"Version als Datum gespeichert ({value:%d.%m.%Y}, Excel-Autoformat) "
                f"-> als Text eintragen")
    if isinstance(value, float):
        return (f"Version als Zahl gespeichert ({value!r}; z. B. 6.10 wird zu 6.1) "
                f"-> als Text eintragen")
    return None


def leftover_releases(vp: VendorProduct, inventory_product: str | None) -> list[str]:
    """Release-Kennungen aus dem Produktnamen, die nicht schon im NVD-Produktnamen
    stecken (Windows Server 2008 R2 -> ["r2"], Exchange Server 2019 -> ["2019"])."""
    product_parts = {name_key(p) for p in unescape(vp.product).split("_")}
    return [w for w in tokens(inventory_product, drop_parens=True)
            if has_digit(w) and name_key(w) not in product_parts]


def exact_entries(nvd: NvdClient, vp: VendorProduct, version: str) -> list[dict]:
    """Aktive NVD-Einträge mit genau dieser Version."""
    pattern = f"cpe:2.3:{vp.part}:{vp.vendor}:{vp.product}:{escape(version)}"
    return [h for h in active(nvd.products(cpeMatchString=pattern))
            if unescape(split_cpe(h["cpeName"])["version"]) == version]


def product_versions(nvd: NvdClient, vp: VendorProduct) -> set[str]:
    hits = active(nvd.products(cpeMatchString=f"cpe:2.3:{vp.part}:{vp.vendor}:{vp.product}"))
    return {unescape(split_cpe(h["cpeName"])["version"]) for h in hits} - WILDCARDS


def version_key(version: str) -> tuple[int, ...] | None:
    """Numerischer Vergleichsschlüssel: 1.31 = 1.31.0, 7.00 = 7.0 = 7."""
    parts = version.split(".")
    if not all(p.isdigit() for p in parts):
        return None
    numbers = [int(p) for p in parts]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def version_segments(version: str) -> list[str]:
    return [s for s in re.split(r"[._\-()]+", version) if s]


def fits_nvd_scheme(nvd: NvdClient, vp: VendorProduct, version: str) -> tuple[bool, str]:
    """Passt eine nicht gelistete Version zum Versionsschema der NVD für dieses Produkt?

    Verglichen wird die Anzahl der Versionsteile mit den NVD-Versionen derselben
    Hauptversion (gibt es keine, mit allen): 9.12.0 passt zu 8.14.0, aber
    8.0.6001.18702 nicht zu Internet Explorer "8"/"8.0.6001".
    """
    versions = product_versions(nvd, vp)
    segments = version_segments(version)
    same_major = [v for v in versions if version_segments(v)[:1] == segments[:1]]
    pool = same_major or sorted(versions)
    examples = ", ".join(sorted(pool, key=len)[:5]) or "keine"
    return any(len(version_segments(v)) == len(segments) for v in pool), examples


def related_products(nvd: NvdClient, vp: VendorProduct, version: str,
                     inventory_product: str | None) -> list[str]:
    """Einträge, die genau diese Version führen und auf eine falsche Produktwahl
    hindeuten: dasselbe Produkt unter anderem Vendor, oder ein passendes anderes
    Produkt desselben Vendors (Zabbix Server -> zabbix:zabbix, NetScaler ADC ->
    application_delivery_controller).

    Passend heißt: alle Wörter des NVD-Produkts stehen im Inventar-Produktnamen, oder
    seine Anfangsbuchstaben ergeben ein Wort daraus (ADC).
    """
    inventory_words = {name_key(w) for w in tokens(inventory_product, drop_parens=False)}
    related = set()
    # gleiches Produkt unter anderem Vendor (don_ho vs. notepad-plus-plus)
    for hit in active(nvd.products(cpeMatchString=f"cpe:2.3:*:*:{vp.product}:{escape(version)}")):
        fields = split_cpe(hit["cpeName"])
        if fields["vendor"] != vp.vendor and unescape(fields["version"]) == version:
            related.add(f"{fields['part']}:{fields['vendor']}:{fields['product']}")
    # anderes Produkt desselben Vendors
    pattern = f"cpe:2.3:*:{vp.vendor}:*:{escape(version)}"
    for hit in active(nvd.products(cpeMatchString=pattern)):
        fields = split_cpe(hit["cpeName"])
        if fields["product"] == vp.product or unescape(fields["version"]) != version:
            continue
        words = [name_key(w) for w in unescape(fields["product"]).split("_") if name_key(w)]
        acronym = "".join(w[0] for w in words)
        if set(words) <= inventory_words or (len(words) > 1 and acronym in inventory_words):
            related.add(f"{fields['part']}:{fields['vendor']}:{fields['product']}")
    return sorted(related)


def try_exact(nvd: NvdClient, vp: VendorProduct, version: str, update: str | None,
              inventory_product: str | None, base: str, notes: list[str]):
    """Exakter Eintrag für Version (+ Update). Rückgabe: (Resolution oder None,
    Update-Werte der NVD zu dieser Version - leer, wenn die Version fehlt)."""
    entries = exact_entries(nvd, vp, version)
    available = sorted({unescape(split_cpe(e["cpeName"])["update"]) for e in entries})
    if update:
        entries = [e for e in entries if updates_match(split_cpe(e["cpeName"])["update"], update)]
    if not entries:
        return None, available

    known = tuple({unescape(split_cpe(e["cpeName"])["update"]) for e in entries}) if update else ()
    entry, note = pick_version_entry(entries, inventory_product, known)
    if entry is None:
        return manual(f"{base}; {note}", [e["cpeName"] for e in entries]), available
    detail = "; ".join([base] + [n for n in notes + [note] if n])
    return Resolution(S_EXACT, entry["cpeName"], detail), available


def resolve_version(nvd: NvdClient, vp: VendorProduct, inventory_version,
                    inventory_product: str | None, origin: str,
                    check_related: bool = True) -> Resolution:
    """Baut aus bestätigtem vendor:product und Inventarversion die CPE.

    check_related=False bei Overrides: dort ist das Produkt bewusst gewählt.
    """
    base = f"{vp.vendor}:{vp.product} {origin}"
    problem = cell_type_problem(inventory_version)
    if problem:
        return manual(f"{base}; {problem}")
    version_raw = "" if inventory_version is None else str(inventory_version).strip()
    if UNKNOWN_VERSION.match(version_raw):
        return resolve_unknown_version(nvd, vp, version_raw, inventory_product, base)

    parsed = parse_inventory_version(version_raw)
    if parsed is None:
        return manual(f'{base}; Version "{version_raw}" nicht NVD-konform -> Override nötig')

    # Versuche: (Version, Update, Hinweis), spezifisch zuerst
    attempts = []
    if parsed.core:
        attempts.append((parsed.core, parsed.update, ""))
        attached = ATTACHED_SUFFIX.match(parsed.core)
        if attached and not parsed.update:
            attempts.append((attached.group(1), attached.group(2),
                             f'"{parsed.core}" -> Version {attached.group(1)}, '
                             f'Update {attached.group(2)}'))
    else:
        releases = leftover_releases(vp, inventory_product)
        if len(releases) > 1:
            return manual(f'{base}; "{version_raw}" ohne Version und mehrere Releases im '
                          f'Produktnamen ({", ".join(releases)}) -> Override nötig')
        release = releases[0] if releases else "-"
        attempts.append((release, parsed.update,
                         f'"{version_raw}" -> Version {release}, Update {parsed.update}'))
    first_version = attempts[0][0]
    updates_found: dict[str, list[str]] = {}

    def run(candidates):
        for version, update, how in candidates:
            resolution, available = try_exact(nvd, vp, version, update, inventory_product,
                                              base, parsed.notes + [how])
            if resolution:
                return resolution
            if available:
                updates_found.setdefault(version, available)
        return None

    resolution = run(attempts)
    if resolution:
        return resolution
    # gleichwertige Schreibweise erst, wenn die direkte nicht im Dictionary steht
    if parsed.core and version_key(parsed.core):
        equivalent = sorted(v for v in product_versions(nvd, vp) if v != parsed.core
                            and version_key(v) == version_key(parsed.core))
        if len(equivalent) > 1:
            return manual(f"{base}; Version {parsed.core} mehrfach gleichwertig im Dictionary",
                          equivalent)
        resolution = run([(v, parsed.update, f"Version {parsed.core} im Dictionary als {v}")
                          for v in equivalent])
        if resolution:
            return resolution
        # die NVD führt die Version mit angehängter Build-Nummer (SonarQube 9.9.3 ->
        # 9.9.3.79811); nur eindeutig übernehmen
        with_build = sorted(v for v in product_versions(nvd, vp)
                            if v.startswith(parsed.core + ".")
                            and v[len(parsed.core) + 1:].isdigit())
        if len(with_build) > 1:
            return manual(f"{base}; Version {parsed.core} im Dictionary nur mit Build-Nummer, "
                          f"mehrere Builds -> Override nötig", with_build[:20])
        resolution = run([(v, parsed.update, f"Version {parsed.core} im Dictionary als {v} "
                                             f"(mit Build-Nummer)") for v in with_build])
        if resolution:
            return resolution

    if parsed.update:
        # Ein Update-Teil, der nicht exakt passt, darf nicht still wegfallen
        version = parsed.core or first_version
        listed = updates_found.get(version) or next(iter(updates_found.values()), [])
        return manual(f'{base}; "{version_raw}": Version {version} mit Update {parsed.update} '
                      f'nicht im Dictionary -> Override nötig',
                      [f"Updates zu {version}: " + ", ".join(listed[:20])] if listed else [])
    version = parsed.core
    if not NVD_LIKE_VERSION.match(version):
        return manual(f'{base}; Version "{version_raw}" nicht NVD-konform -> Override nötig')

    if check_related:
        related = related_products(nvd, vp, version, inventory_product)
        if len(related) == 1:
            return derive_related_product(nvd, vp, related[0], version, inventory_product,
                                          base, parsed.notes)
        if related:
            return manual(f"{base}; Version {version} nicht unter {vp.product}, aber exakt "
                          f"unter verwandtem Produkt desselben Herstellers -> Produktwahl prüfen",
                          related)
    fits, examples = fits_nvd_scheme(nvd, vp, version)
    if not fits:
        return manual(f"{base}; Version {version} nicht im Dictionary und Versionsschema "
                      f"passt nicht zur NVD (z. B. {examples}) -> Override nötig")
    return Resolution(S_VERSION, build_cpe(*vp, escape(version)),
                      "; ".join([base] + parsed.notes + [f"Version {version} nicht im Dictionary"]))


def derive_related_product(nvd: NvdClient, vp: VendorProduct, related: str, version: str,
                           inventory_product: str | None, base: str,
                           notes: list[str]) -> Resolution:
    """Genau ein verwandtes Produkt führt die Version exakt -> dessen Eintrag übernehmen
    (Status "Abgeleitet": die Version ist bestätigt, die Produktwahl hat der Resolver
    getroffen; ein Override hat immer Vorrang). Nur mit einem echten Eintrag: Passt
    keiner eindeutig, wird nicht zusätzlich per "*" verallgemeinert."""
    related_vp = VendorProduct(*related.split(":", 2))
    entries = exact_entries(nvd, related_vp, version)
    entry, note = pick_version_entry(entries, inventory_product)
    if entry is None or entry not in entries:
        return manual(f"{base}; Version {version} nicht unter {vp.product}, unter {related} "
                      f"{note} -> Produktwahl prüfen", [e["cpeName"] for e in entries])
    detail = "; ".join([base] + notes + [f"Version {version} nicht unter {vp.product}, aber "
                                         f"exakt unter {related} -> Produkt abgeleitet"]
                       + ([note] if note else []))
    return Resolution(S_DERIVED, entry["cpeName"], detail)


def resolve_unknown_version(nvd: NvdClient, vp: VendorProduct, version_raw: str,
                            inventory_product: str | None, base: str) -> Resolution:
    """Version unbekannt -> "*"; steht im Produktnamen genau ein Release, das nicht schon
    im NVD-Produktnamen steckt (Windows Server 2008 R2, Solaris 10) und als Version im
    Dictionary existiert, wird es als Version übernommen."""
    releases = leftover_releases(vp, inventory_product)
    if len(releases) == 1 and exact_entries(nvd, vp, releases[0]):
        return Resolution(S_BUILD, build_cpe(*vp, escape(releases[0])),
                          f'{base}; Inventarversion "{version_raw}", Release '
                          f'{releases[0]} aus dem Produktnamen als Version')
    return Resolution(S_BUILD, build_cpe(*vp), f'{base}; Inventarversion "{version_raw}" -> "*"')
