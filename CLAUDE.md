# Kontext für Claude Code

Projekt: Cisco-AI / Projektseminar. Ziel: CPEs im Assetverzeichnis (Blatt `Assets`, Spalte H `CPE`) sicher bestimmen, als Grundlage für den automatischen CVE-/heise-Feed-Abgleich.

## Festgelegte Entscheidungen (mit Dat abgestimmt)
- Nicht raten: vendor:product nur übernehmen, wenn eindeutig aus dem NVD-CPE-Dictionary bestimmt (oder per `overrides.csv`). Unklares -> Status "Manuell prüfen".
- Spalte H wird überschrieben; bei "Manuell prüfen" wird H geleert (`--on-ambiguous clear`).
- Status als Zellkommentar an H, KEINE neue Spalte einfügen (andere Blätter referenzieren feste Spaltenbereiche wie `Assets!$N:$Q`, `$S`, `$C`; Spalte D = `=MID(H,9,1)`).
- Version unbekannt ("Build offen") -> `*`.
- Version nicht im Dictionary -> CPE aus bestätigtem vendor:product + Inventarversion, Status "Version nicht gelistet".
- Part (a/o/h) immer aus der NVD übernehmen (z. B. führt die NVD Azure DevOps Server als `o`).
- Release-Kennungen mit Ziffern (2019, 2025, 23h2) dürfen bei der Suche nicht weggekürzt werden und müssen im Treffer vorkommen.
- PoC in Python; später Portierung nach n8n geplant. Kein NVD-API-Key vorhanden.

## Offen
- Noch nie gegen die echte NVD gelaufen; Verhalten von `keywordSearch` (Wort- vs. Teilstring-Match) unverifiziert -> erst Probelauf, Report prüfen.
- Exchange (SRV-03, Version "CU14") braucht einen Override.
- Die xlsx liegt NICHT im Repo (Firmen-Inventar).
