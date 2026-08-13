# -*- coding: utf-8 -*-
"""MG install-rate — daily refresh of the AUGUST columns (L:O) in tab
'MG rate (MayJun ASSIGNED, Jul IEC)' of the MG install-rate sheet.

August is anchored the same way as July: a lead counts in August if it reached
TECHNICIAN_ASSIGNED in August (clean distinct-connection count), AND is MATURED
(>=48h since assignment, so it had a fair window to install). install = OTP /
completed / step>=7. Only the LIVE August columns L:O are rewritten each run —
May-Jun / July / cohorts / counts (cols A:K) are frozen baselines and untouched.

Five tables share the same L:O columns and are refreshed together:
  ALL (710) · MG ENROLLED (477) · CONTROL not-enrolled (233) ·
  CONTROL Sehat-MG enrolled (72) · CONTROL no-MG (161).
Partition (group / Sehat-subgroup / re-derived cohort / May-Jun baseline) is
frozen in mgrate_aug_config.json so the split always matches the pasted baseline.

Env: MB_KEY, GOOGLE_SA_JSON (JSON string; else local SA file). Optional MGRATE_SHEET_ID.
"""
import os, sys, json, time, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account
from collections import defaultdict

MB_KEY   = os.environ.get("MB_KEY") or os.environ.get("METABASE_KEY")
SHEET_ID = os.environ.get("MGRATE_SHEET_ID", "1_Zg7VkQ7RTZ-9pDJjrI1OvfGe3-1NhTqJyu71OT-Z7c")
TAB      = "MG rate (MayJun ASSIGNED, Jul IEC)"
IST      = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
HERE     = os.path.dirname(os.path.abspath(__file__))

AUG_SQL = """
with ta as (select CONNECTION_ID, min(UPDATED_AT) ta_at
   from PROD_DB.DBT_CSP.TAS_INSTALL_EXECUTION_CANDIDATES
   where CURRENT_STATE='TECHNICIAN_ASSIGNED' group by 1),
tas as (select CONNECTION_ID,
    max(iff(OTP_VERIFIED=TRUE OR INSTALLATION_COMPLETED_AT is not null OR COMPLETED_STEP>=7,1,0)) inst
  from PROD_DB.DBT_CSP.TAS_INSTALL_EXECUTION_CANDIDATES where ETL_CURRENT=TRUE group by 1),
tl as (select CONNECTION_ID,CSP_ID from PROD_DB.DBT_CSP.TAS_INSTALL_EXECUTION_CANDIDATES where ETL_CURRENT=TRUE
  qualify row_number() over(partition by CONNECTION_ID order by UPDATED_AT desc)=1),
csp as (select CSP_ID,PARTNER_ID::string pid from PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_ACCOUNT
  where _fivetran_active=TRUE qualify row_number() over(partition by CSP_ID order by 1)=1)
select csp.pid, count(distinct ta.CONNECTION_ID) tech, sum(coalesce(tas.inst,0)) inst
from ta join tl on tl.CONNECTION_ID=ta.CONNECTION_ID
  left join tas on tas.CONNECTION_ID=ta.CONNECTION_ID
  left join csp on csp.CSP_ID=tl.CSP_ID
where dateadd(minute,330,ta.ta_at)>='2026-08-01' and dateadd(minute,330,ta.ta_at)<'2026-09-01'
  and ta.ta_at <= dateadd(hour,-48,current_timestamp())
group by 1
"""


def mb(q, tries=4):
    for a in range(tries):
        try:
            r = urllib.request.Request("https://metabase.wiom.in/api/dataset",
                data=json.dumps({"database": 113, "type": "native", "native": {"query": q}}).encode(),
                headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
            j = json.loads(urllib.request.urlopen(r, timeout=300).read().decode())
            if j.get("data") and j["data"].get("rows") is not None:
                return j["data"]["rows"]
        except Exception as e:
            print("  mb retry", a + 1, e, flush=True); time.sleep(6)
    raise SystemExit("Metabase failed")


def creds():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if raw:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name, scopes=scopes)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p):
            return service_account.Credentials.from_service_account_file(p, scopes=scopes)
    raise SystemExit("no SA creds")


def table_of(label):
    l = (label or "").strip()
    if l.startswith("ALL"):          return "ALL"
    if l.startswith("MG ENROLLED"):  return "ENROLLED"
    if "Sehat" in l:                 return "CTL_SEHAT"
    if "no MG" in l:                 return "CTL_NOMG"
    if l.startswith("CONTROL"):      return "CONTROL"
    return None


def belongs(g, sub, table):
    if table == "ALL":       return True
    if table == "ENROLLED":  return g == "ENROLLED"
    if table == "CONTROL":   return g == "CONTROL"
    if table == "CTL_SEHAT": return g == "CONTROL" and sub == "SEHAT"
    if table == "CTL_NOMG":  return g == "CONTROL" and sub == "NOMG"
    return False


def colletter(i):  # 0-based -> A1 letter
    s = ""; i += 1
    while i:
        i, r = divmod(i - 1, 26); s = chr(65 + r) + s
    return s


def main():
    cfg = json.load(open(os.path.join(HERE, "mgrate_aug_config.json"), encoding="utf-8"))
    rows = mb(AUG_SQL)
    aug = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows if r[0]}
    print("aug matured pids:", len(aug),
          "tech", sum(v[0] for v in aug.values()), "inst", sum(v[1] for v in aug.values()), flush=True)

    tables = ["ALL", "ENROLLED", "CONTROL", "CTL_SEHAT", "CTL_NOMG"]
    agg = {t: defaultdict(lambda: [0, 0, 0, 0]) for t in tables}  # cohort -> aug_tech, aug_inst, mjt, mji
    for pid, (g, sub, coh, mjt, mji) in cfg.items():
        at, ai = aug.get(pid, (0, 0))
        for t in tables:
            if belongs(g, sub, t):
                x = agg[t][coh]; x[0] += at; x[1] += ai; x[2] += mjt; x[3] += mji

    def cells(atech, ainst, mjt, mji):
        ap = round(100 * ainst / atech) if atech else None
        mp = round(100 * mji / mjt) if mjt else None
        pct = (str(ap) + "%") if ap is not None else "-"
        if ap is not None and mp is not None:
            d = ap - mp; delta = ("+" + str(d)) if d >= 0 else str(d)
        else:
            delta = "-"
        return [atech, ainst, pct, delta]

    gc = gspread.authorize(creds())
    ws = gc.open_by_key(SHEET_ID).worksheet(TAB)
    grid = ws.get_all_values()

    updates = []; cur_tbl = None; aug_start = None; wrote = 0
    for i, row in enumerate(grid):
        a = (row[0] if row else "").strip()
        t = table_of(a)
        if t:
            cur_tbl = t
        if a == "cohort":
            aug_start = next((j for j, c in enumerate(row) if "Aug tech" in c), 11)
            continue
        if cur_tbl is None or aug_start is None:
            continue
        if a in ("0", "0.5-4", "4.5-8", "8.5-12", "12.5-24", ">24"):
            atech, ainst, mjt, mji = agg[cur_tbl].get(a, [0, 0, 0, 0])
            vals = cells(atech, ainst, mjt, mji)
        elif a == "Grand Total":
            tot = [0, 0, 0, 0]
            for v in agg[cur_tbl].values():
                for k in range(4): tot[k] += v[k]
            vals = cells(*tot)
        else:
            continue
        r1 = i + 1
        # write only tech/inst/% (L:N). Col O (Aug Δ vs MayJun %) and P (Base/CSP)
        # are live sheet formulas (=N/I-1, =ROUND(C/B)) — do NOT overwrite them.
        rng = f"{colletter(aug_start)}{r1}:{colletter(aug_start + 2)}{r1}"
        updates.append({"range": rng, "values": [vals[:3]]})
        wrote += 1

    # header timestamp
    now = datetime.datetime.now(IST)
    hdr = grid[0][0] if grid and grid[0] else ""
    base = hdr.split("  [LIVE")[0]
    if not base:
        base = "MG INSTALL RATE"
    updates.append({"range": "A1", "values": [[
        base + f"  [LIVE: Aug cols L:N auto-refresh 24h (O=Δ% & P=Base/CSP are live formulas); matured>=48h; last run {now:%Y-%m-%d %H:%M IST}]"]]})

    ws.batch_update(updates, value_input_option="RAW")
    print(f"wrote {wrote} rows across {len(tables)} tables; {len(updates)} ranges. OK {now:%Y-%m-%d %H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
