# -*- coding: utf-8 -*-
"""MG Payout (formula-integrated) - August.

Same layout as the July tab, but August numerator/denominator come LIVE from the execution
service (IEC), month-to-date (auto-rolls). Per-install rate = Rs300 for everyone, so
installation money = 300 x installs. Derived columns are live sheet formulas; G (denom) and
H (installs) are refreshed each run from IEC.

August MG metric (user-provided):
  NUM (installs) = OTP_VERIFIED OR INSTALLATION_COMPLETED_AT OR COMPLETED_STEP>=7
  DENOM (leads reached tech-assigned) = EXECUTOR_ID IS NOT NULL
  per (connection, CSP), last_date (IST) in current calendar month.

Cohort + names + enrollment dates carried from the July tab (same enrolled CSPs).
Env: METABASE_KEY, GOOGLE_SA_JSON (CI) or local SA. Idempotent (clear + rewrite).
"""
import os, sys, json, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

MB_KEY = os.environ.get("METABASE_KEY", "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
SHEET_ID = "1XHqjybQYKyfCpgdraPiL32GBm2R2wf-CguxlHalmu8M"
JULY_TAB = "MG Payout (formula-integrated) - July"
AUG_TAB = "MG Payout (formula-integrated) - August"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


def mb(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://metabase.wiom.in/api/dataset", data=body,
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=180).read().decode())
    if isinstance(d, dict) and d.get("error"): raise SystemExit("MB ERROR: " + str(d["error"])[:400])
    return d["data"]["rows"]


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
    gc = gspread.authorize(creds())
    ss = gc.open_by_key(SHEET_ID)

    # 1) cohort from the July tab: Name, Partner ID, CSP ID, Flow, Enrollment date
    jv = ss.worksheet(JULY_TAB).get_all_values()
    cohort = []
    for r in jv[2:]:                                   # skip banner + header
        if not r or not r[2].strip() or r[0].strip().upper() == "TOTAL": continue
        cohort.append({"name": r[0], "pid": r[1], "cspid": r[2].strip(), "flow": r[3], "enroll": r[4]})
    cspids = [c["cspid"] for c in cohort]
    print(f"cohort from July tab: {len(cspids)} CSPs", flush=True)

    # 2) August installs + denom (tech-assigned) + total leads, month-to-date, from IEC
    inlist = ",".join("'%s'" % c.replace("'", "") for c in cspids)
    rows = mb(f"""
WITH agg AS (
  SELECT iec.CONNECTION_ID, iec.CSP_ID,
    MAX(IFF(iec.OTP_VERIFIED=TRUE OR iec.INSTALLATION_COMPLETED_AT IS NOT NULL OR iec.COMPLETED_STEP>=7,1,0)) AS has_installed,
    MAX(IFF(iec.EXECUTOR_ID IS NOT NULL,1,0)) AS tech_assigned,
    TO_DATE(DATEADD(minute,330,MAX(iec.UPDATED_AT))) AS last_date
  FROM PROD_DB.CSP_TAS_SERVICE_CSP_TAS_SERVICE.INSTALL_EXECUTION_CANDIDATES iec
  WHERE iec._FIVETRAN_ACTIVE AND iec.CSP_ID IN ({inlist})
  GROUP BY 1,2
)
SELECT CSP_ID,
  SUM(IFF(has_installed=1 AND last_date >= DATE_TRUNC('month', CURRENT_DATE),1,0)) AS installs,
  SUM(IFF(tech_assigned=1 AND last_date >= DATE_TRUNC('month', CURRENT_DATE),1,0)) AS denom,
  SUM(IFF(last_date >= DATE_TRUNC('month', CURRENT_DATE),1,0)) AS total_leads
FROM agg GROUP BY CSP_ID""")
    aug = {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in rows}
    print(f"IEC returned August data for {len(aug)} CSPs", flush=True)

    # 3) build rows (data starts at sheet row 3)
    banner = ["MG Payout — FORMULA-INTEGRATED — AUGUST (month-to-date, live from IEC) · per-install rate ₹300 for all · "
              "≥60% gate · ≤2 leads = min guarantee · MG floor ₹10,000 prorated by August eligible days · "
              f"updated {datetime.datetime.now(IST):%d-%b %H:%M IST}"] + [""] * 17
    header = ["Name", "Partner ID", "CSP ID", "Flow", "Enrollment date", "Eligible days /31",
              "CSP ko kitne leads mile (denom)", "Installs", "Install rate", "Higher-rate installs count",
              "Base-rate installs count", "Per-install higher rate (₹)", "Bucket", "MG guarantee (prorata ₹)",
              "Installation money (₹)", "Top-up money (₹)", "Total payout (₹)", "Total leads offered (Aug)"]
    data = []
    for i, c in enumerate(cohort):
        R = i + 3
        inst, den, tot = aug.get(c["cspid"], (0, 0, 0))
        data.append([
            c["name"], c["pid"], c["cspid"], c["flow"], c["enroll"],
            f"=MAX(0,MIN(31,DATE(2026,8,31)-MAX(DATEVALUE(E{R}),DATE(2026,8,1))+1))",   # F eligible Aug days
            den,                                                                          # G denom (input)
            inst,                                                                         # H installs (input)
            f"=IF(G{R}>0,H{R}/G{R},\"\")",                                                # I install rate
            0,                                                                            # J higher-rate installs
            inst,                                                                         # K base-rate installs = installs
            300,                                                                          # L per-install rate ₹300 all
            f"=IF(G{R}<=2,\"≤2 leads (min guarantee)\",IF(H{R}>=0.6*G{R},\"Secured (≥60%)\",\"Piece-rate only\"))",  # M
            f"=ROUND(10000*F{R}/31)",                                                     # N MG guarantee prorata
            f"=300*K{R}+L{R}*J{R}",                                                        # O installation money
            f"=IF(OR(G{R}<=2,H{R}>=0.6*G{R}),MAX(0,N{R}-O{R}),0)",                        # P top-up
            f"=O{R}+P{R}",                                                                 # Q total payout
            tot,                                                                          # R total leads offered Aug
        ])
    last = len(cohort) + 3                                                                # sheet row of TOTAL
    total = ["TOTAL", "", "", "", "", ""] + [
        f"=SUM(G3:G{last-1})", f"=SUM(H3:H{last-1})", "", f"=SUM(J3:J{last-1})", f"=SUM(K3:K{last-1})", "",
        "", f"=SUM(N3:N{last-1})", f"=SUM(O3:O{last-1})", f"=SUM(P3:P{last-1})", f"=SUM(Q3:Q{last-1})", f"=SUM(R3:R{last-1})"]
    out = [banner, header] + data + [total]

    # 4) write
    try: ws = ss.worksheet(AUG_TAB); ws.clear()
    except gspread.WorksheetNotFound: ws = ss.add_worksheet(AUG_TAB, rows=len(out) + 20, cols=18)
    ws.update(values=out, range_name="A1", value_input_option="USER_ENTERED")
    ws.format("A1:R1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    ws.format("A2:R2", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}})
    ws.format(f"A{last}:R{last}", {"textFormat": {"bold": True}})
    ws.format(f"I3:I{last}", {"numberFormat": {"type": "PERCENT", "pattern": "0%"}})
    tot_installs = sum(aug.get(c["cspid"], (0, 0, 0))[0] for c in cohort)
    tot_denom = sum(aug.get(c["cspid"], (0, 0, 0))[1] for c in cohort)
    print(f"wrote '{AUG_TAB}': {len(cohort)} CSPs, Aug installs={tot_installs}, denom={tot_denom} | {datetime.datetime.now(IST):%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
