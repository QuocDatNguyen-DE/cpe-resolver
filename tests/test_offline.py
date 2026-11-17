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
    def products(self, **params):
        FakeNvd.calls += 1
        out = []
        for name, title, dep in CATALOG:
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
               "--overrides", str(ROOT / "overrides.csv")], nvd=FakeNvd())
by = {r.asset_id: r for r in res}

def check(aid, status, cpe):
    r = by[aid]
    assert r.status == status and r.new == cpe, (aid, r)

check("SRV-01", m.S_EXACT, "cpe:2.3:a:gitlab:gitlab:17.5.0:*:*:*:community:*:*:*")
check("SRV-06", m.S_VERSION, "cpe:2.3:a:gitlab:runner:17.5.0:*:*:*:*:*:*:*")
check("SRV-03", m.S_MANUAL, None)                      # "CU14" nicht NVD-konform
check("SRV-04", m.S_EXACT, "cpe:2.3:a:veeam:veeam_backup_\\&_replication:12.0.0.1420:*:*:*:*:*:*:*")
check("NB-01", m.S_BUILD, "cpe:2.3:o:microsoft:windows_11_23h2:*:*:*:*:*:*:*:*")
check("FS-01", m.S_BUILD, "cpe:2.3:o:microsoft:windows_server_2019:*:*:*:*:*:*:*:*")
check("SRV-11", m.S_VERSION, "cpe:2.3:a:wordpress:wordpress:6.4.3:*:*:*:*:*:*:*")  # deprecated ignoriert
check("SW-01", m.S_MANUAL, None)                       # nicht im Katalog
check("SRV-12", m.S_MANUAL, None)                      # 2025 darf nicht auf 2019 fallen
check("NB-02", m.S_MANUAL, None)                       # Win10 darf nicht auf Win11 fallen
check("DC-01", m.S_BUILD, "cpe:2.3:o:microsoft:windows_server_2019:*:*:*:*:*:*:*:*")

import openpyxl
wb = openpyxl.load_workbook(tmp / "assets_cpe.xlsx")
ws = wb["Assets"]
assert ws["H2"].value == by["SRV-01"].new
assert ws["H4"].value is None and "Manuell" in ws["H4"].comment.text      # SRV-03 geleert
assert ws["D2"].value == "=MID(H2,9,1)"                                   # Formeln intakt
assert ws["N2"].value.startswith("=MAX(")
assert wb["Risiken"]["C2"].value.startswith("=IFERROR(")
print("\nOK - alle Offline-Checks bestanden;", FakeNvd.calls, "Fake-Abfragen")
print(open(tmp / "r.csv", encoding="utf-8-sig").read())
