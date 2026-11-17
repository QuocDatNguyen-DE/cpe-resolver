# cpe-resolver

PoC: bestimmt CPE-2.3-Namen für das Blatt `Assets` eines Assetverzeichnisses (xlsx) anhand des offiziellen NVD-CPE-Dictionary (API 2.0) und schreibt sie in die Spalte `CPE`. Grundlage für den automatischen Abgleich mit Schwachstellenmeldungen (heise-Feed / CVEs).

## Nutzung

```bash
pip install -r requirements.txt
python3 cpe_resolver.py Assetverzeichnis.xlsx            # Probelauf -> cpe_report.csv
python3 cpe_resolver.py Assetverzeichnis.xlsx --apply    # schreibt Assetverzeichnis_cpe.xlsx
```

Optionen: `--overrides overrides.csv`, `--on-ambiguous clear|keep` (Standard `clear`), `--out`, `--report`, `--cache-dir`.
Optionaler NVD-API-Key über `NVD_API_KEY` (ohne Key: ~6,5 s pro Anfrage, Antworten werden in `.nvd_cache/` gecacht).

## Aufbau

`cpe_resolver.py` ist der Einstiegspunkt (CLI, Ablauf). Die Logik liegt im Paket `resolver/`:

| Modul | Inhalt |
|---|---|
| `cpe.py` | CPE-Strings zerlegen, maskieren, bauen |
| `text.py` | Wortzerlegung, Hersteller-Vergleich |
| `nvd.py` | NVD-Client mit Cache und Rate-Limit |
| `model.py` | Status-Werte und Ergebnistypen |
| `vendor_product.py` | vendor:product per Stichwortsuche bestimmen |
| `version.py` | Version prüfen und CPE bauen |
| `overrides.py` | `overrides.csv` laden und anwenden |
| `resolve.py` | eine Asset-Zeile auflösen |
| `excel.py` | Blatt lesen, CPE-Zelle + Kommentar schreiben |
| `report.py` | CSV-Report |

## Status pro Asset (Kommentar an der CPE-Zelle + Report)

| Status | Bedeutung |
|---|---|
| NVD exakt | exakter, nicht veralteter Eintrag im Dictionary |
| Version nicht gelistet | vendor:product bestätigt, CPE mit Inventarversion gebaut |
| Build unbekannt | vendor:product bestätigt, Version `*` |
| Override | aus `overrides.csv`, gegen NVD geprüft |
| Manuell prüfen | kein/kein eindeutiger Treffer oder Version nicht NVD-konform; Zelle wird geleert |

## Test

```bash
python3 tests/test_offline.py Assetverzeichnis.xlsx
```

Läuft gegen einen synthetischen Fake-Katalog (nicht die echte NVD) und prüft Auswahl-Logik, Excel-Ausgabe und dass Formeln erhalten bleiben.
