"""Offline-Test mit einem Fake-NVD (synthetischer Mini-Katalog, NICHT die echte NVD)."""
import sys, shutil, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cpe_resolver as cr
from resolver import model as m
from resolver.cpe import CPE_FIELDS, split_cpe

CATALOG = [  # (cpeName, title, deprecated)
    ("cpe:2.3:a:gitlab:gitlab:17.5.0:*:*:*:community:*:*:*", "GitLab 17.5.0 Community Edition", False),
    ("cpe:2.3:a:gitlab:gitlab:17.5.0:*:*:*:enterprise:*:*:*", "GitLab 17.5.0 Enterprise Edition", False),
    ("cpe:2.3:a:gitlab:runner:17.4.0:*:*:*:*:*:*:*", "GitLab Runner 17.4.0", False),
    ("cpe:2.3:a:gitlab:gitaly:1.0:*:*:*:*:*:*:*", "GitLab Gitaly 1.0", False),
    ("cpe:2.3:a:microsoft:exchange_server:2019:cumulative_update_14:*:*:*:*:*:*", "Microsoft Exchange Server 2019 Cumulative Update 14", False),
    ("cpe:2.3:a:veeam:veeam_backup_\\&_replication:12.0.0.1420:*:*:*:*:*:*:*", "Veeam Backup & Replication 12.0.0.1420", False),
    ("cpe:2.3:a:veeam:backup_and_replication_active_directory_restore:1.0:*:*:*:*:*:*:*", "Veeam Backup and Replication Active Directory Restore 1.0", False),
    ("cpe:2.3:o:microsoft:windows_11_23h2:10.0.22631.2861:*:*:*:*:*:x64:*", "Microsoft Windows 11 23H2 10.0.22631.2861 on x64", False),
    ("cpe:2.3:o:microsoft:windows_server_2019:-:*:*:*:*:*:*:*", "Microsoft Windows Server 2019", False),
    ("cpe:2.3:a:wordpress:wordpress:6.4.2:*:*:*:*:*:*:*", "WordPress 6.4.2", False),
    ("cpe:2.3:a:wordpress:wordpress:6.4.3:*:*:*:*:*:*:*", "WordPress 6.4.3 old", True),
]

def match(pattern, name):
    p = pattern.split(":")[2:]
    n = split_cpe(name)
    return all(pv in ("*", "") or pv == n[f] for pv, f in zip(p, CPE_FIELDS))

class FakeNvd:
    calls = 0
    catalog = CATALOG
    def products(self, **params):
        FakeNvd.calls += 1
        out = []
        for name, title, dep in self.catalog:
            if "keywordSearch" in params:
                hay = (title + " " + name).lower()
                ok = all(w in hay for w in params["keywordSearch"].lower().split())
            else:
                ok = match(params["cpeMatchString"], name)
            if ok:
                out.append({"cpeName": name, "deprecated": dep, "title": title})
        return out

tmp = Path(tempfile.mkdtemp())
xlsx = tmp / "assets.xlsx"
if len(sys.argv) < 2:
    sys.exit("Aufruf: python3 tests/test_offline.py <Assetverzeichnis.xlsx>")
shutil.copy(sys.argv[1], xlsx)
res = cr.main([str(xlsx), "--apply", "--report", str(tmp / "r.csv"),
               "--overrides", str(tmp / "keine_overrides.csv")], nvd=FakeNvd())
by = {r.asset_id: r for r in res}

def check(aid, status, cpe):
    r = by[aid]
    assert r.status == status and r.new == cpe, (aid, r)

check("SRV-01", m.S_EXACT, "cpe:2.3:a:gitlab:gitlab:17.5.0:*:*:*:community:*:*:*")
# 17.5.0 fehlt bei runner, steht aber unter gitlab:gitlab (CE und EE, keine passt zu
# "GitLab Runner") -> keine Ableitung, Produktwahl prüfen (real per Override)
check("SRV-06", m.S_MANUAL, None)
assert len(by["SRV-06"].candidates) == 2 and "gitlab:gitlab" in by["SRV-06"].detail, by["SRV-06"]
check("SRV-03", m.S_EXACT, "cpe:2.3:a:microsoft:exchange_server:2019:cumulative_update_14:*:*:*:*:*:*")  # CU14 -> Update-Feld
check("SRV-04", m.S_EXACT, "cpe:2.3:a:veeam:veeam_backup_\\&_replication:12.0.0.1420:*:*:*:*:*:*:*")
check("NB-01", m.S_BUILD, "cpe:2.3:o:microsoft:windows_11_23h2:*:*:*:*:*:*:*:*")
check("FS-01", m.S_BUILD, "cpe:2.3:o:microsoft:windows_server_2019:*:*:*:*:*:*:*:*")
check("SRV-11", m.S_VERSION, "cpe:2.3:a:wordpress:wordpress:6.4.3:*:*:*:*:*:*:*")  # deprecated ignoriert
check("SW-01", m.S_MANUAL, None)                       # nicht im Katalog
check("SRV-12", m.S_MANUAL, None)                      # 2025 darf nicht auf 2019 fallen
check("NB-02", m.S_MANUAL, None)                       # Win10 darf nicht auf Win11 fallen
check("DC-01", m.S_BUILD, "cpe:2.3:o:microsoft:windows_server_2019:*:*:*:*:*:*:*:*")

# Die echte NVD lehnt zu breite Suchen ab (HTTP 404 "Keyword search too broad.")
from resolver.nvd import SearchTooBroad
from resolver.vendor_product import find_vendor_product

class TooBroadNvd(FakeNvd):
    def products(self, **params):
        if params.get("keywordSearch", "").startswith("wordpress"):
            raise SearchTooBroad("Keyword search too broad.")
        return super().products(**params)

search = find_vendor_product(TooBroadNvd(), "WordPress", "WordPress")
assert search.match is None and "zu breit" in search.reason, search

import openpyxl
from openpyxl.worksheet.formula import ArrayFormula
from resolver.excel import find_asset_sheet, header_map

def formulas(wb):
    """{(Blatt, Zelle): Formeltext} über alle Blätter."""
    out = {}
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for c in row:
                v = c.value.text if isinstance(c.value, ArrayFormula) else c.value
                if isinstance(v, str) and v.startswith("="):
                    out[(sheet.title, c.coordinate)] = v
    return out

wb_in = openpyxl.load_workbook(sys.argv[1])
wb = openpyxl.load_workbook(tmp / "assets_cpe.xlsx")
ws = find_asset_sheet(wb)
cols = header_map(ws)
row_of = {ws.cell(r, cols["id"]).value: r for r in range(2, ws.max_row + 1)}
cpe_cell = lambda aid: ws.cell(row_of[aid], cols["cpe"])
assert cpe_cell("SRV-01").value == by["SRV-01"].new
sw01 = cpe_cell("SW-01")
assert sw01.value is None and "Manuell" in sw01.comment.text             # SW-01 geleert
# Zellfarbe je Status: grün = bestätigt, gelb = Version offen, rot = nachhaken
from resolver.excel import STATUS_FILLS
for aid in ("SRV-01", "NB-01", "SW-01"):
    assert cpe_cell(aid).fill.start_color.rgb.endswith(STATUS_FILLS[by[aid].status]), aid
assert STATUS_FILLS[by["SRV-01"].status] != STATUS_FILLS[by["SW-01"].status]
before, after = formulas(wb_in), formulas(wb)
assert before and before == after, "Formeln verändert"                    # Formeln intakt
print(f"\nBlatt {ws.title}: {len(after)} Formeln unverändert")

# --- Grenzfälle aus dem Test mit Altsoftware und Sonderzeichen (Mini-Katalog) ---
from datetime import datetime
from resolver.model import VendorProduct
from resolver.text import tokens, vendor_matches
from resolver.vendor_product import build_search_queries
from resolver.version import resolve_version

class EdgeNvd(FakeNvd):
    catalog = [
        ("cpe:2.3:h:mi:m365:2019:*:*:*:*:*:*:*", "Mi M365 2019", False),
        ("cpe:2.3:o:centos:centos:6.10:*:*:*:*:*:*:*", "CentOS 6.10", False),
        ("cpe:2.3:a:apache:http_server:2.4.49:*:*:*:*:*:*:*", "Apache HTTP Server 2.4.49", False),
        ("cpe:2.3:a:openbsd:openssh:7.2:p2:*:*:*:*:*:*", "OpenBSD OpenSSH 7.2p2", False),
        ("cpe:2.3:o:cisco:ios:12.2\\(55\\)se:*:*:*:*:*:*:*", "Cisco IOS 12.2(55)SE", False),
        ("cpe:2.3:a:mediawiki:mediawiki:1.31.0:*:*:*:*:*:*:*", "MediaWiki 1.31.0", False),
        ("cpe:2.3:a:microsoft:internet_explorer:8:*:*:*:*:*:*:*", "Microsoft Internet Explorer 8", False),
        ("cpe:2.3:a:microsoft:internet_explorer:8.0.6001:*:*:*:*:*:*:*", "Microsoft Internet Explorer 8.0.6001", False),
        ("cpe:2.3:a:atlassian:jira_software_data_center:8.14.0:*:*:*:*:*:*:*", "Jira Software Data Center 8.14.0", False),
        ("cpe:2.3:o:microsoft:windows_server_2008:r2:sp1:*:*:*:*:*:*", "Microsoft Windows Server 2008 R2 SP1", False),
        ("cpe:2.3:o:microsoft:windows_xp:-:sp3:*:*:professional:*:*:*", "Microsoft Windows XP Professional SP3", False),
        ("cpe:2.3:o:microsoft:windows_xp:-:sp3:*:*:embedded:*:*:*", "Microsoft Windows XP Embedded SP3", False),
        ("cpe:2.3:a:7-zip:7-zip:9.20:*:*:*:*:*:*:*", "7-Zip 9.20", False),
        # allgemeine Regeln, bewusst mit anderen Produkten als im Grenzfall-Test
        ("cpe:2.3:a:vmware:vcenter_server:7.0:update3c:*:*:*:*:*:*", "VMware vCenter Server 7.0 Update 3c", False),
        ("cpe:2.3:a:vmware:vcenter_server:7.0:update3:*:*:*:*:*:*", "VMware vCenter Server 7.0 Update 3", False),
        ("cpe:2.3:o:vmware:esxi:6.7:2:*:*:*:*:*:*", "VMware ESXi 6.7 Update 2", False),
        ("cpe:2.3:a:microsoft:sharepoint_server:2013:sp1:*:*:enterprise:*:*:*", "Microsoft SharePoint Server 2013 SP1", False),
        ("cpe:2.3:a:python:python:3.8.10:*:*:*:*:*:*:*", "Python 3.8.10", False),
        ("cpe:2.3:a:sendmail:sendmail:8.14:*:*:*:*:*:*:*", "Sendmail 8.14", False),
        ("cpe:2.3:a:zabbix:zabbix:5.0.12:*:*:*:*:*:*:*", "Zabbix 5.0.12", False),
        ("cpe:2.3:a:zabbix:zabbix_server:6.0.22:*:*:*:*:*:*:*", "Zabbix Server 6.0.22", False),
        ("cpe:2.3:a:citrix:application_delivery_controller:13.0:*:*:*:*:*:*:*", "Citrix ADC 13.0", False),
        ("cpe:2.3:a:citrix:netscaler:12.0:*:*:*:*:*:*:*", "Citrix NetScaler 12.0", False),
        ("cpe:2.3:a:pulsesecure:connect_secure:9.1:*:*:*:*:*:*:*", "Pulse Secure Pulse Connect Secure 9.1", False),
        ("cpe:2.3:a:pulsesecure:pulse_policy_secure:9.1:*:*:*:*:*:*:*", "Pulse Secure Pulse Policy Secure 9.1", False),
        ("cpe:2.3:a:apache:struts:2.5.10:*:*:*:*:*:*:*", "Apache Struts 2.5.10", False),
        ("cpe:2.3:a:apache:struts_extras:2.0:*:*:*:*:*:*:*", "Apache Struts 2 Extras", False),
        ("cpe:2.3:a:keepass:keepass:2.40:*:*:*:*:*:*:*", "KeePass 2.40", False),
        ("cpe:2.3:a:google:chrome:119.0.6045.200:*:*:*:*:*:*:*", "Google Chrome 119.0.6045.200", False),
        ("cpe:2.3:o:google:chrome:100.0:*:*:*:*:*:*:*", "Google Chrome OS 100.0", False),
        ("cpe:2.3:a:microsoft:.net:6.0.25:*:*:*:*:*:*:*", "Microsoft .NET 6.0.25", False),
        ("cpe:2.3:o:redhat:enterprise_linux:8.0:*:*:*:*:*:*:*", "Red Hat Enterprise Linux 8.0", False),
        ("cpe:2.3:a:redhat:enterprise_linux_server:8:*:*:*:*:*:*:*", "Red Hat Enterprise Linux Server 8", False),
        ("cpe:2.3:a:sonarsource:sonarqube:9.9.3.79811:*:*:*:*:*:*:*", "SonarSource SonarQube 9.9.3.79811", False),
        ("cpe:2.3:o:microsoft:windows_10_22h2:10.0.19045.3693:*:*:*:*:*:x64:*", "Windows 10 22H2 x64", False),
        ("cpe:2.3:o:microsoft:windows_10_22h2:10.0.19045.3693:*:*:*:*:*:arm64:*", "Windows 10 22H2 arm64", False),
        ("cpe:2.3:a:fortinet:forticlient:6.4.2:*:*:*:*:linux:*:*", "FortiClient 6.4.2 Linux", False),
        ("cpe:2.3:a:fortinet:forticlient:6.4.2:*:*:*:*:windows:*:*", "FortiClient 6.4.2 Windows", False),
    ]

def resolve(part_vendor_product, version, product):
    return resolve_version(EdgeNvd(), VendorProduct(*part_vendor_product.split(":")),
                           version, product, "(Test)")

# Hersteller: kein Teilstring-Treffer, Trennzeichen egal, Aliase
assert not vendor_matches("mi", "Mircosoft")
assert vendor_matches("apache", "Apache Software Foundation")
assert vendor_matches("7-zip", "7-Zip") and vendor_matches("notepad-plus-plus", "Notepad++")
assert not vendor_matches("rarlab", "win.rar GmbH")
assert vendor_matches("rarlab", "win.rar GmbH", {"winrargmbh": {"rarlab"}})
search = find_vendor_product(EdgeNvd(), "Mircosoft", "Windwos Server 2019")
assert search.match is None, search                                       # nicht raten
assert ["2019"] not in build_search_queries(["mircosoft"], ["windwos", "server", "2019"])
search = find_vendor_product(EdgeNvd(), "Igor Pavlov", "7-Zip", {"igorpavlov": {"7zip"}})
assert str(search.match) == "a:7-zip:7-zip", search

# Wortzerlegung: Umlaute, Punkte in Release-Nummern, .NET
assert tokens("SAP GUI für Windows", True) == ["sap", "gui", "fur", "windows"]
assert tokens("Ubuntu Linux 14.04 LTS", True) == ["ubuntu", "linux", "14.04", "lts"]
assert tokens(".NET Framework 3.5", True) == [".net", "framework", "3.5"]

# Excel-Zelltypen
r = resolve("o:centos:centos", 6.1, "CentOS")
assert r.status == m.S_MANUAL and "als Zahl" in r.detail, r
r = resolve("a:apache:http_server", datetime(2026, 10, 1), "HTTP Server")
assert r.status == m.S_MANUAL and "als Datum" in r.detail, r

# Versionen normalisieren, nur bei exaktem Eintrag
cases = [
    ("a:apache:http_server", "v2.4.49", "HTTP Server", m.S_EXACT, "cpe:2.3:a:apache:http_server:2.4.49:*:*:*:*:*:*:*"),
    ("a:openbsd:openssh", "7.2p2", "OpenSSH", m.S_EXACT, "cpe:2.3:a:openbsd:openssh:7.2:p2:*:*:*:*:*:*"),
    ("o:cisco:ios", "12.2(55)SE", "IOS", m.S_EXACT, "cpe:2.3:o:cisco:ios:12.2\\(55\\)se:*:*:*:*:*:*:*"),
    ("a:mediawiki:mediawiki", "1.31", "MediaWiki", m.S_EXACT, "cpe:2.3:a:mediawiki:mediawiki:1.31.0:*:*:*:*:*:*:*"),
    ("o:microsoft:windows_xp", "SP3", "Windows XP Professional", m.S_EXACT,
     "cpe:2.3:o:microsoft:windows_xp:-:sp3:*:*:professional:*:*:*"),
    # Versionsschema passt nicht (IE führt 8 / 8.0.6001) -> keine gebaute CPE
    ("a:microsoft:internet_explorer", "8.0.6001.18702", "Internet Explorer 8", m.S_MANUAL, None),
    # Schema passt (8.14.0) -> CPE mit Inventarversion
    ("a:atlassian:jira_software_data_center", "9.12.0", "Jira Software Data Center", m.S_VERSION,
     "cpe:2.3:a:atlassian:jira_software_data_center:9.12.0:*:*:*:*:*:*:*"),
    # Release aus dem Produktnamen bei unbekanntem Build
    ("o:microsoft:windows_server_2008", "Build offen", "Windows Server 2008 R2 Standard", m.S_BUILD,
     "cpe:2.3:o:microsoft:windows_server_2008:r2:*:*:*:*:*:*:*"),
    ("o:cisco:ios", "6.5 U3", "IOS", m.S_MANUAL, None),
]
for vp, version, product, status, cpe in cases:
    r = resolve(vp, version, product)
    assert (r.status, r.cpe) == (status, cpe), (vp, version, r)

# Allgemeine Regeln mit anderen Produkten (keine Sonderfälle für die Testdatei)
general = [
    # Update-Langform/Kurzform über normierten Vergleich (update3c = U3c, Update 2 = 2)
    ("a:vmware:vcenter_server", "7.0 U3c", "vCenter Server", m.S_EXACT,
     "cpe:2.3:a:vmware:vcenter_server:7.0:update3c:*:*:*:*:*:*"),
    ("a:vmware:vcenter_server", "7.0 Update 3", "vCenter Server", m.S_EXACT,
     "cpe:2.3:a:vmware:vcenter_server:7.0:update3:*:*:*:*:*:*"),
    ("o:vmware:esxi", "6.7 Update 2", "ESXi", m.S_EXACT, "cpe:2.3:o:vmware:esxi:6.7:2:*:*:*:*:*:*"),
    # Update-Teil ohne passenden Eintrag fällt nicht still weg
    ("a:vmware:vcenter_server", "7.0 U9", "vCenter Server", m.S_MANUAL, None),
    # nur Service Pack: Release aus dem Produktnamen, Edition per Name
    ("a:microsoft:sharepoint_server", "Service Pack 1", "SharePoint Server 2013 Enterprise", m.S_EXACT,
     "cpe:2.3:a:microsoft:sharepoint_server:2013:sp1:*:*:enterprise:*:*:*"),
    ("o:microsoft:windows_server_2008", "SP1", "Windows Server 2008 R2", m.S_EXACT,
     "cpe:2.3:o:microsoft:windows_server_2008:r2:sp1:*:*:*:*:*:*"),
    # Bezeichnung, Klammerzusatz und Plattform werden abgetrennt
    ("a:python:python", "Version 3.8.10 (Windows) x64", "Python", m.S_EXACT,
     "cpe:2.3:a:python:python:3.8.10:*:*:*:*:*:*:*"),
    # gleichwertige Schreibweise in beide Richtungen
    ("a:sendmail:sendmail", "8.14.0", "Sendmail", m.S_EXACT, "cpe:2.3:a:sendmail:sendmail:8.14:*:*:*:*:*:*:*"),
    # nicht gelistete Version, aber exakt unter genau einem verwandten Produkt -> abgeleitet
    ("a:zabbix:zabbix_server", "5.0.12", "Zabbix Server", m.S_DERIVED,
     "cpe:2.3:a:zabbix:zabbix:5.0.12:*:*:*:*:*:*:*"),
    ("a:citrix:netscaler", "13.0", "NetScaler ADC", m.S_DERIVED,
     "cpe:2.3:a:citrix:application_delivery_controller:13.0:*:*:*:*:*:*:*"),
]
for vp, version, product, status, cpe in general:
    r = resolve(vp, version, product)
    assert (r.status, r.cpe) == (status, cpe), (vp, version, r)
assert "abgeleitet" in resolve("a:zabbix:zabbix_server", "5.0.12", "Zabbix Server").detail

# Namensvergleich in Stufen: ohne Herstellerwörter, ohne Release-Kennung
search = find_vendor_product(EdgeNvd(), "Pulse Secure", "Pulse Connect Secure")
assert str(search.match) == "a:pulsesecure:connect_secure", search
search = find_vendor_product(EdgeNvd(), "Apache", "Struts 2")
assert str(search.match) == "a:apache:struts", search
# Gepflegtes Inventar: Mehrdeutigkeit über die Version, Release mit Punkt als
# Versionsanfang, Name ohne Hersteller- und Release-Wörter, Version ohne Build-Suffix
search = find_vendor_product(EdgeNvd(), "Google", "Chrome", None, "119.0.6045.200")
assert str(search.match) == "a:google:chrome", search
search = find_vendor_product(EdgeNvd(), "Google", "Chrome", None, "1.0")
assert search.match is None, search                                       # keiner führt 1.0
search = find_vendor_product(EdgeNvd(), "Microsoft", ".NET 6.0")
assert str(search.match) == "a:microsoft:.net", search
search = find_vendor_product(EdgeNvd(), "Red Hat", "Red Hat Enterprise Linux 8")
assert str(search.match) == "o:redhat:enterprise_linux", search
# Version bestätigt, nur Plattform offen -> unterschiedliche Felder "*", gleiche bleiben
r = resolve("o:microsoft:windows_10_22h2", "10.0.19045.3693", "Windows 10 Enterprise 22H2")
assert (r.status, r.cpe) == (m.S_EXACT, "cpe:2.3:o:microsoft:windows_10_22h2:10.0.19045.3693:*:*:*:*:*:*:*"), r
assert "arm64/x64" in r.detail, r
r = resolve("a:fortinet:forticlient", "6.4.2", "FortiClient")
assert r.cpe == "cpe:2.3:a:fortinet:forticlient:6.4.2:*:*:*:*:*:*:*", r
r = resolve("a:fortinet:forticlient", "6.4.2", "FortiClient for Windows")   # Plattform im Namen
assert r.cpe == "cpe:2.3:a:fortinet:forticlient:6.4.2:*:*:*:*:windows:*:*", r
r = resolve("a:sonarsource:sonarqube", "9.9.3", "SonarQube")
assert (r.status, r.cpe) == (m.S_EXACT, "cpe:2.3:a:sonarsource:sonarqube:9.9.3.79811:*:*:*:*:*:*:*"), r
# Release mit Punkt passt nur als ganzer Versionsanfang (6.0 -> 6.0.25, nicht 16.0.25)
from resolver.vendor_product import collect_candidates
hits = [{"cpeName": "cpe:2.3:a:microsoft:.net:16.0.25:*:*:*:*:*:*:*", "deprecated": False}]
assert collect_candidates(hits, "Microsoft", ["6.0"]) == []

# Hersteller passt nicht: nur Vorschlag, keine Zuordnung
search = find_vendor_product(EdgeNvd(), "Dominik Reichl", "KeePass")
assert search.match is None and search.candidates == ["a:keepass:keepass"], search

# NVD-Timeout wird wiederholt statt den Lauf abzubrechen
import io, json, urllib.request
from resolver import nvd as nvd_module

attempts = []
def flaky_urlopen(request, timeout):
    attempts.append(1)
    if len(attempts) == 1:
        raise TimeoutError("The read operation timed out")
    return io.BytesIO(json.dumps({"products": [], "resultsPerPage": 0, "totalResults": 0}).encode())

original_urlopen, original_wait = urllib.request.urlopen, nvd_module.NVD_RETRY_WAIT
urllib.request.urlopen, nvd_module.NVD_RETRY_WAIT = flaky_urlopen, 0
try:
    client = nvd_module.NvdClient(tmp / "cache", "test-key")
    client.min_interval = 0
    assert client.products(keywordSearch="timeout-test") == [] and len(attempts) == 2
finally:
    urllib.request.urlopen, nvd_module.NVD_RETRY_WAIT = original_urlopen, original_wait
print("Grenzfälle (Hersteller, Wortzerlegung, Zelltypen, Versionen, Timeout) OK")
print("\nOK - alle Offline-Checks bestanden;", FakeNvd.calls, "Fake-Abfragen")
print(open(tmp / "r.csv", encoding="utf-8-sig").read())
