# -*- coding: utf-8 -*-
"""Rebuild the "MG Removals + Updated Lists" tab (6 lists) — headless, cron-able.

Reads:
  - "Remove from MG" tab (the removal source; refreshed from the Enforcement Hub — manually
    until its mirror sheet is shared with the SA). MG column -> Install / Sehat-Optical / Sehat-Service.
  - Enrolled cohorts: Install from "MG Tracker CSP", Sehat Optical/Service from "Sehat MG".
Writes the 6 lists (3 removal + 3 updated = enrolled minus removals).

NOTE: this cron keeps the tab consistent with the removal tab + current enrollment. It does NOT
fetch NEW hub removals (the hub is OAuth-gated, 12h sessions). To make removals auto-fresh too,
share the hub mirror 1nr3QGLaKnt... with wiom-sheets-writer@wiom-return.iam.gserviceaccount.com.

Env: GOOGLE_SA_JSON (CI) or local SA. Idempotent.
"""
import os, sys, tempfile, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account
SHEET_ID = "1XHqjybQYKyfCpgdraPiL32GBm2R2wf-CguxlHalmu8M"
TAB = "MG Removals + Updated Lists"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


def creds():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    if raw:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name, scopes=SCOPES)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p): return service_account.Credentials.from_service_account_file(p, scopes=SCOPES)
    raise SystemExit("no SA creds")


def main():
    gc = gspread.authorize(creds()); ss = gc.open_by_key(SHEET_ID)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

    # 1) removals from the "Remove from MG" tab
    rt = next((w for w in ss.worksheets() if w.title.startswith("Remove from MG")), None)
    if rt is None: raise SystemExit("no 'Remove from MG' tab")
    rv = rt.get_all_values()
    hi = next(i for i, r in enumerate(rv) if "CSP ID" in r)
    RH = rv[hi]; idc = RH.index("CSP ID"); nmc = RH.index("CSP Name"); mgc = RH.index("MG")
    vic = next(j for j, c in enumerate(RH) if "Violation" in (c or ""))
    r_inst, r_opt, r_svc = {}, {}, {}
    for r in rv[hi + 1:]:
        if len(r) <= vic or not r[idc].strip().startswith("a0"): continue
        c = r[idc].strip(); rec = (r[nmc].strip(), r[vic].strip()); mg = r[mgc].upper()
        if "SEHAT" in mg and "OPTICAL" in mg: r_opt[c] = rec
        elif "SEHAT" in mg and ("SERVICE" in mg or "SLA" in mg): r_svc[c] = rec
        elif "SEHAT" in mg: r_opt[c] = rec          # default sehat -> optical
        else: r_inst[c] = rec

    # 2) enrolled cohorts
    tv = ss.worksheet("MG Tracker CSP").get_all_values(); TH = tv[1]
    ix = {n: TH.index(n) for n in ("CSP ID", "Name", "Audit done / Enrolled")}
    install = {r[ix["CSP ID"]].strip(): r[ix["Name"]].strip() for r in tv[2:]
               if len(r) > ix["Audit done / Enrolled"] and r[ix["Audit done / Enrolled"]].strip().upper() == "ENROLLED" and r[ix["CSP ID"]].strip()}
    sv = ss.worksheet("Sehat MG").get_all_values(); SH = sv[0]
    si = {n: SH.index(n) for n in ("CSP ID", "Name", "Sehat Intervention", "Audit done / Enrolled")}
    opt, svc = {}, {}
    for r in sv[1:]:
        if len(r) <= si["Audit done / Enrolled"] or r[si["Audit done / Enrolled"]].strip().lower() not in ("yes", "enrolled"): continue
        c = r[si["CSP ID"]].strip()
        if c: (opt if "optical" in r[si["Sehat Intervention"]].lower() else svc)[c] = r[si["Name"]].strip()

    def upd(base, rm): return sorted(((c, n) for c, n in base.items() if c not in rm), key=lambda x: x[1].lower())
    u_inst, u_opt, u_svc = upd(install, r_inst), upd(opt, r_opt), upd(svc, r_svc)
    rmrows = lambda d: sorted(((c, v[0], v[1]) for c, v in d.items()), key=lambda x: x[1].lower())

    groups = [
        (0, 3, f"REMOVE FROM INSTALL MG ({len(r_inst)})", ["CSP ID", "Name", "Violation"], rmrows(r_inst)),
        (4, 3, f"REMOVE FROM SEHAT MG — OPTICAL ({len(r_opt)})", ["CSP ID", "Name", "Violation"], rmrows(r_opt)),
        (8, 3, f"REMOVE FROM SEHAT MG — SERVICE ({len(r_svc)})", ["CSP ID", "Name", "Violation"], rmrows(r_svc)),
        (12, 2, f"UPDATED INSTALL MG — after removing violations ({len(u_inst)})", ["CSP ID", "Name"], u_inst),
        (15, 2, f"UPDATED SEHAT MG — OPTICAL ({len(u_opt)})", ["CSP ID", "Name"], u_opt),
        (18, 2, f"UPDATED SEHAT MG — SERVICE ({len(u_svc)})", ["CSP ID", "Name"], u_svc),
    ]
    NC = 20; maxlen = max(len(g[4]) for g in groups)
    grid = [["" for _ in range(NC)] for _ in range(maxlen + 3)]
    grid[0][0] = (f"MG violation removals + updated program lists · removals from '{rt.title}' "
                  f"({len(r_inst)} Install + {len(r_opt)} Sehat-Optical + {len(r_svc)} Sehat-Service) · "
                  f"updated = ENROLLED minus removals · auto-refreshed {now:%d-%b %H:%M IST}")
    for sc, nc, head, sub, rws in groups:
        grid[1][sc] = head
        for j, hh in enumerate(sub): grid[2][sc + j] = hh
        for k, row in enumerate(rws):
            for j in range(nc): grid[3 + k][sc + j] = row[j] if j < len(row) else ""

    ws = next((w for w in ss.worksheets() if w.title == TAB), None)
    if ws: ws.clear()
    else: ws = ss.add_worksheet(TAB, rows=len(grid) + 20, cols=NC)
    ws.update(values=grid, range_name="A1", value_input_option="RAW")
    pink = {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}}
    subf = {"textFormat": {"bold": True}, "backgroundColor": {"red": .98, "green": .9, "blue": .95}}
    ws.format("A1:T1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    for sc, nc, _, _, _ in groups:
        a = gspread.utils.rowcol_to_a1(2, sc + 1); b = gspread.utils.rowcol_to_a1(2, sc + nc); ws.format(f"{a}:{b}", pink)
        a = gspread.utils.rowcol_to_a1(3, sc + 1); b = gspread.utils.rowcol_to_a1(3, sc + nc); ws.format(f"{a}:{b}", subf)
    print(f"rebuilt '{TAB}' from '{rt.title}': REMOVE I={len(r_inst)} O={len(r_opt)} S={len(r_svc)} | "
          f"UPDATED I={len(u_inst)} O={len(u_opt)} S={len(u_svc)} | {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
