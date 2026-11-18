# cpe-resolver

PoC: bestimmt CPE-2.3-Namen für das Blatt `Assets` eines Assetverzeichnisses (xlsx) anhand des offiziellen NVD-CPE-Dictionary (API 2.0) und schreibt sie in die Spalte `CPE`. Grundlage für den automatischen Abgleich mit Schwachstellenmeldungen (heise-Feed / CVEs).

## Nutzung

```bash
pip install -r requirements.txt
python3 cpe_resolver.py Assetverzeichnis.xlsx            # Probelauf -> cpe_report.csv
python3 cpe_resolver.py Assetverzeichnis.xlsx --apply    # schreibt Assetverzeichnis_cpe.xlsx
```

Optionen: `--overrides overrides.csv`, `--vendor-aliases vendor_aliases.csv`, `--on-ambiguous clear|keep` (Standard `clear`), `--out`, `--report`, `--cache-dir`.
`vendor_aliases.csv` enthält bestätigte Zuordnungen für Hersteller, die im Inventar anders heißen als in der NVD (z. B. Igor Pavlov -> `7-zip`). Ohne Eintrag schlägt der Resolver gleichnamige Produkte nur vor ("Manuell prüfen"); das Produkt wird in jedem Fall per NVD-Suche bestimmt.
Optionaler NVD-API-Key (ohne Key: ~6,5 s pro Anfrage, Antworten werden in `.nvd_cache/` gecacht):
`.env.example` nach `.env` kopieren und `NVD_API_KEY=...` eintragen. `.env` ist in `.gitignore`.
Eine gesetzte Umgebungsvariable `NVD_API_KEY` hat Vorrang; andere Datei per `--credentials pfad`.

## Aufbau

`cpe_resolver.py` ist der Einstiegspunkt (CLI, Ablauf). Die Logik liegt im Paket `resolver/`:

| Modul | Inhalt |
|---|---|
| `credentials.py` | Zugangsdaten aus `.env` lesen |
| `cpe.py` | CPE-Strings zerlegen, maskieren, bauen |
| `text.py` | Wortzerlegung, Hersteller-Vergleich |
| `aliases.py` | `vendor_aliases.csv` laden |
| `nvd.py` | NVD-Client mit Cache und Rate-Limit |
| `model.py` | Status-Werte und Ergebnistypen |
| `vendor_product.py` | vendor:product per Stichwortsuche bestimmen |
| `version.py` | Version prüfen und CPE bauen |
| `overrides.py` | `overrides.csv` laden und anwenden |
| `resolve.py` | eine Asset-Zeile auflösen |
| `excel.py` | Blatt lesen, CPE-Zelle + Kommentar schreiben |
| `report.py` | CSV-Report |

## Status pro Asset (Zellfarbe + Kommentar an der CPE-Zelle + Report)

Zellfarben: NVD exakt grün, Override dunkleres Grün, Abgeleitet türkis, Build unbekannt gelb, Version nicht gelistet orange, Manuell prüfen rot.

| Status | Bedeutung |
|---|---|
| NVD exakt | exakter, nicht veralteter Eintrag im Dictionary (auch nach Normalisierung, z. B. `7.2p2` -> `7.2:p2`, `v2.4.49` -> `2.4.49`) |
| Version nicht gelistet | vendor:product bestätigt, Versionsschema passt zur NVD, kein verwandtes Produkt führt die Version; CPE mit Inventarversion gebaut |
| Build unbekannt | vendor:product bestätigt, Version `*` (oder Release aus dem Produktnamen, z. B. `r2`) |
| Override | aus `overrides.csv`, gegen NVD geprüft |
| Abgeleitet | Version nicht unter dem gefundenen Produkt, aber exakt unter genau einem verwandten Produkt (z. B. Zabbix Server -> `zabbix:zabbix`); dessen Eintrag übernommen |
| Manuell prüfen | kein/kein eindeutiger Treffer, Version nicht NVD-konform, Versionsschema passt nicht oder Version als Zahl/Datum in Excel; Zelle wird geleert |

## Test

```bash
python3 tests/test_offline.py Assetverzeichnis.xlsx
```

Läuft gegen einen synthetischen Fake-Katalog (nicht die echte NVD) und prüft Auswahl-Logik, Excel-Ausgabe und dass Formeln erhalten bleiben, dazu Grenzfälle (Hersteller-Tippfehler, Sonderzeichen, Excel-Zahlen/-Datum, Versionsnormalisierung, Cisco-Klammern, NVD-Timeout).
