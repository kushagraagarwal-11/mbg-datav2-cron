# -*- coding: utf-8 -*-
"""MG removals — refresh from the Enforcement Hub mirror sheet (fully headless, cron-able).

The Enforcement Hub mirrors its "Remove from MG" list to a Google Sheet
(1nr3QGLaKnt...), now shared with the automation SA. This reads that mirror and rebuilds:
  1) "Remove from MG (<date>)"  in the MG pilot sheet  — the removal source tab
  2) "MG Removals + Updated Lists" (6 lists)           — removals + (enrolled minus removals)

No hub login needed. Sehat split: mg_type "Sehat MG (Optical Power)" -> Optical, else Service.

Env: GOOGLE_SA_JSON (CI) or local SA. Idempotent.
"""
import os, sys, tempfile, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

MIRROR_ID = "1nr3QGLaKnt_vY_VoMp5_wWyzkhNSRfEqWtjyIsyW4fo"
MIRROR_TAB = "Remove from MG"
PILOT_ID = "1XHqjybQYKyfCpgdraPiL32GBm2R2wf-CguxlHalmu8M"
LIST_TAB = "MG Removals + Updated Lists"
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


def num(x):
    try: return float(str(x).replace(",", "")) if x not in (None, "") else 0.0
    except Exception: return 0.0


def viol_label(fpv, cases, contested):
    plural = "s" if str(cases) != "1" else ""
    c = ", contested - turned down" if str(contested).lower() == "yes" else ""
    return f"{fpv} ({cases} case{plural}{c})"


def main():
    gc = gspread.authorize(creds())
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

    # 1) read the hub mirror
    mv = gc.open_by_key(MIRROR_ID).worksheet(MIRROR_TAB).get_all_values()
    H = mv[0]; gi = lambda n: H.index(n)
    rem = {}
    for r in mv[1:]:
        if not r or not r[gi("csp_id")].strip().startswith("a0"): continue
        c = r[gi("csp_id")].strip(); mg = r[gi("mg_type")]
        code = "O" if ("Sehat" in mg and "Optical" in mg) else ("S" if "Sehat" in mg else "I")
        rem[c] = {"name": r[gi("csp_name")].strip(),
                  "mg": {"I": "INSTALL", "O": "SEHAT (Optical Power)", "S": "SEHAT (Service)"}[code],
                  "code": code,
                  "viol": viol_label(r[gi("fpv_types")], r[gi("recovered_cases")], r[gi("contested")]),
                  "amt": num(r[gi("amount_recovered")]),
                  "disint": "disintermediation" in (r[gi("fpv_types")] or "").lower()}
    total = round(sum(v["amt"] for v in rem.values()), 2)
    ndis = sum(1 for v in rem.values() if v["disint"])
    ni = sum(1 for v in rem.values() if v["code"] == "I"); no = sum(1 for v in rem.values() if v["code"] == "O"); ns = sum(1 for v in rem.values() if v["code"] == "S")
    print(f"mirror: {len(rem)} removals (Install {ni} / Sehat-Optical {no} / Sehat-Service {ns}), {ndis} disint, Rs {total:,.0f}", flush=True)

    ss = gc.open_by_key(PILOT_ID)

    # 2) rewrite the "Remove from MG" source tab (rename to today's date), carry disint dates
    old = next((w for w in ss.worksheets() if w.title.startswith("Remove from MG")), None)
    disint_date = {}
    if old:
        ov = old.get_all_values()
        hi = next((k for k, rr in enumerate(ov) if "CSP ID" in rr), None)
        if hi is not None:
            OH = ov[hi]; oid = OH.index("CSP ID"); odt = next((j for j, c in enumerate(OH) if "Disintermediation" in (c or "")), None)
            if odt is not None:
                for rr in ov[hi + 1:]:
                    if len(rr) > odt and rr[oid].strip().startswith("a0") and rr[odt].strip():
                        disint_date[rr[oid].strip()] = rr[odt].strip()
    note = [f"Remove from MG — {len(rem)} CSPs · recovered Rs {total:,.0f} · source: Enforcement Hub mirror (auto-synced) · "
            f"{ni} Install + {no} Sehat-Optical + {ns} Sehat-Service · {ndis} disintermediation · refreshed {now:%d-%b %H:%M IST}"]
    head = ["CSP ID", "CSP Name", "MG", "Violation (reason)", "Recovered (Rs)", "Date of Disintermediation"]
    body = [[c, rem[c]["name"], rem[c]["mg"], rem[c]["viol"], rem[c]["amt"], disint_date.get(c, "")]
            for c in sorted(rem, key=lambda x: rem[x]["name"].lower())]
    out = [note, [], head] + body + [[], ["", "", "", "TOTAL RECOVERED", total, ""]]
    tab_name = f"Remove from MG ({now:%d-%b})"
    if old:
        old.clear()
        if old.title != tab_name: old.update_title(tab_name)
        rt = old
    else:
        rt = ss.add_worksheet(tab_name, rows=len(out) + 10, cols=6)
    rt.update(values=out, range_name="A1", value_input_option="RAW")
    rt.format("A1:F1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    rt.format("A3:F3", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}})

    # 3) enrolled cohorts
    tv = ss.worksheet("MG Tracker CSP").get_all_values(); TH = tv[1]
    ix = {n: TH.index(n) for n in ("CSP ID", "Name", "Audit done / Enrolled")}
    install = {rr[ix["CSP ID"]].strip(): rr[ix["Name"]].strip() for rr in tv[2:]
               if len(rr) > ix["Audit done / Enrolled"] and rr[ix["Audit done / Enrolled"]].strip().upper() == "ENROLLED" and rr[ix["CSP ID"]].strip()}
    sv = ss.worksheet("Sehat MG").get_all_values(); SH = sv[0]
    si = {n: SH.index(n) for n in ("CSP ID", "Name", "Sehat Intervention", "Audit done / Enrolled")}
    opt, svc = {}, {}
    for rr in sv[1:]:
        if len(rr) <= si["Audit done / Enrolled"] or rr[si["Audit done / Enrolled"]].strip().lower() not in ("yes", "enrolled"): continue
        c = rr[si["CSP ID"]].strip()
        if c: (opt if "optical" in rr[si["Sehat Intervention"]].lower() else svc)[c] = rr[si["Name"]].strip()

    r_inst = {c: rem[c] for c in rem if rem[c]["code"] == "I"}
    r_opt = {c: rem[c] for c in rem if rem[c]["code"] == "O"}
    r_svc = {c: rem[c] for c in rem if rem[c]["code"] == "S"}
    upd = lambda base, rm: sorted(((c, n) for c, n in base.items() if c not in rm), key=lambda x: x[1].lower())
    u_inst, u_opt, u_svc = upd(install, r_inst), upd(opt, r_opt), upd(svc, r_svc)
    rmrows = lambda d: sorted(((c, v["name"], v["viol"]) for c, v in d.items()), key=lambda x: x[1].lower())

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
    grid[0][0] = (f"MG violation removals + updated program lists · removals from Enforcement Hub mirror (auto-synced) "
                  f"({len(r_inst)} Install + {len(r_opt)} Sehat-Optical + {len(r_svc)} Sehat-Service) · "
                  f"updated = ENROLLED minus removals · refreshed {now:%d-%b %H:%M IST}")
    for sc, nc, head_, sub, rws in groups:
        grid[1][sc] = head_
        for j, hh in enumerate(sub): grid[2][sc + j] = hh
        for k, row in enumerate(rws):
            for j in range(nc): grid[3 + k][sc + j] = row[j] if j < len(row) else ""
    ws = next((w for w in ss.worksheets() if w.title == LIST_TAB), None)
    if ws: ws.clear()
    else: ws = ss.add_worksheet(LIST_TAB, rows=len(grid) + 20, cols=NC)
    ws.update(values=grid, range_name="A1", value_input_option="RAW")
    pink = {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}}
    subf = {"textFormat": {"bold": True}, "backgroundColor": {"red": .98, "green": .9, "blue": .95}}
    ws.format("A1:T1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    for sc, nc, _, _, _ in groups:
        a = gspread.utils.rowcol_to_a1(2, sc + 1); b = gspread.utils.rowcol_to_a1(2, sc + nc); ws.format(f"{a}:{b}", pink)
        a = gspread.utils.rowcol_to_a1(3, sc + 1); b = gspread.utils.rowcol_to_a1(3, sc + nc); ws.format(f"{a}:{b}", subf)
    print(f"rebuilt '{tab_name}' + '{LIST_TAB}': REMOVE I={len(r_inst)} O={len(r_opt)} S={len(r_svc)} | UPDATED I={len(u_inst)} O={len(u_opt)} S={len(u_svc)} | {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
