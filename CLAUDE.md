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
- PoC in Python; später Portierung nach n8n geplant.
- NVD-API-Key in `.env` (nicht im Repo, Vorlage `.env.example`); Umgebungsvariable `NVD_API_KEY` hat Vorrang.
- Asset-Blatt wird über die Spalten ID/Hersteller/Produkt/Version/CPE gefunden (heißt je Datei "Assets" oder "IT-Assets"); Spalten nie per Buchstabe adressieren.
- NVD-Antwort "Keyword search too broad" (HTTP 404) = kein Treffer für diese Suche, weiter mit der nächsten; keine Namen raten -> Override.
- "&" zählt beim Namensvergleich mit (`veeam_backup_\&_replication`).
- Inventarversion "CU<n>": Release aus dem Produktnamen + `update=cumulative_update_<n>`, nur wenn exakt im Dictionary.
- Echte Mehrdeutigkeit in der NVD (z. B. `gitlab:runner` vs. `gitlab:gitlab_runner`) wird nicht per Heuristik aufgelöst, sondern per Override.

## Regeln aus dem Grenzfall-Test (18.11.2026, von Dat beauftragt)
Allgemeine Regeln, keine produktspezifischen Sonderfälle im Code (Produktnamen stehen nur als Beispiele in Kommentaren).
- Hersteller-Abgleich ohne Teilstring: NVD-Vendor muss aufeinanderfolgenden Wörtern des Herstellers entsprechen (Trennzeichen egal, `+` = "plus"). Passt kein Vendor, werden gleichnamige Produkte anderer Vendors nur als Vorschlag gelistet ("Manuell prüfen"); übernommen erst nach Bestätigung in `vendor_aliases.csv`.
- Keine Suche nur aus Release-Kennungen (z. B. nur "2019"). Mehrere Kandidaten ohne Namensgleichheit -> nächste, allgemeinere Suche.
- Namensvergleich in Stufen: voller Name (auch gekürzte Suche), dann voller Produktname ohne Wörter aus dem NVD-Vendor, dann ohne Release-Kennungen. Die lockeren Stufen nie mit der gekürzten Suche.
- Wortzerlegung: Umlaute transliteriert (für -> fur); Punkte/Bindestriche/`++` bleiben im Wort; Namensvergleich ohne Trennzeichen.
- Version als Zahl (float) oder Datum in der Zelle -> "Manuell prüfen". Ganzzahlen sind erlaubt.
- Inventarversion wird allgemein zerlegt: Bezeichnung (Version/v/Rel./Build), Kern, Update-Teil (Buchstaben+Ziffer, auch angehängt wie 7.2p2), Zusätze in Klammern/Plattform/Wörter (ignoriert, im Kommentar vermerkt).
- Übernahme nur bei exaktem Dictionary-Eintrag; Update-Felder über normierte Form verglichen (update03 = U3, cumulative_update_14 = CU14, Service Pack 1 = sp1); gleichwertige Versionen numerisch (1.31 = 1.31.0). Nur Update ohne Version (SP3, CU14) -> Release aus dem Produktnamen, das nicht im NVD-Namen steckt, sonst `-`.
- Ein Update-Teil, der nicht exakt passt, fällt nie still weg -> "Manuell prüfen".
- "Version nicht gelistet" nur, wenn (a) kein gleichnamiges Produkt eines anderen Vendors und kein passendes Produkt desselben Vendors die Version exakt führt (sonst "Manuell prüfen" mit Kandidaten; bei Overrides nicht geprüft) und (b) das Versionsschema passt.
- "Build offen": Release aus dem Produktnamen, das nicht im NVD-Produktnamen steckt, wird Version, wenn es im Dictionary existiert.
- NVD-Timeouts/Verbindungsfehler werden wie HTTP 5xx wiederholt.

## Stand 18.11.2026
- Erster echter Lauf (SoftCom_Assetverzeichnis_erweitert (1).xlsx): 19/19 aufgelöst (12 exakt, 4 Build unbekannt, 1 Version nicht gelistet, 2 Override). Nach den allgemeinen Regeln ist SRV-02 (Jira Software Data Center 9.12.0) "Manuell prüfen": 9.12.0 steht exakt unter `atlassian:jira_data_center` -> per Override entscheiden.
- Testdateien in Downloads (nicht im Repo): Grenzfall-Test (79 Assets) und Gegenprobe mit anderen Produkten (64 Assets); keine falsche CPE aus Tippfehlern, Excel-Zahlen oder falscher Produktwahl mehr gefunden.

## Offen
- "Build unbekannt" setzt alle Felder nach product auf `*`; Edition/Plattform aus dem Sheet (z. B. `standard`, `x64`) gehen dabei verloren.
- Die xlsx liegt NICHT im Repo (Firmen-Inventar).
- Bleiben "Manuell prüfen" (Override/Alias): Oracle Database 11g, Java "8u202", echte NVD-Mehrdeutigkeiten (Server 2003 R2, Debian 8, Notepad++ mit zwei Vendors), Hersteller ohne NVD-Vendor (Vorschlag wird angezeigt).
