# -*- coding: utf-8 -*-
"""MG install-rate — daily refresh of the AUGUST columns (L:O) in tab
'MG Rate Summary | Aug' (gid 1675005689) of the MG install-rate sheet.

August is anchored the same way as July: a lead counts in August if it reached
TECHNICIAN_ASSIGNED in August, AND is MATURED
(>=48h since assignment, so it had a fair window to install). install = OTP /
completed / step>=7. PAIR GRAIN (matches the July + May-Jun baselines): a lead that was
technician-assigned to two CSPs counts once for EACH of them; the install is credited to a
CSP only if it completed under that CSP's own rows, so a CSP that was handed a lead and lost it
carries it as a miss. (Verified against the frozen July baseline: denom 3127 vs 3145, installs
1668 vs 1670. Crediting the final holder instead gives 1662 and does NOT reconcile.) Only the LIVE August columns L:O are rewritten each run —
May-Jun / July / cohorts / counts (cols A:K) are frozen baselines and untouched.

Five tables share the same L:O columns and are refreshed together:
  ALL (710) · MG ENROLLED (477) · MG ENROLLED excl violation CSPs (461) ·
  CONTROL not-enrolled (233) · CONTROL not-enrolled excl no-work CSPs (190) ·
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
TAB      = "MG Rate Summary | Aug"          # current title (renamed 17-Aug-2026)
TAB_GID  = int(os.environ.get("MGRATE_TAB_GID", "1675005689"))  # gid survives renames
TAB_ALT  = ["MG rate (MayJun ASSIGNED, Jul IEC)"]  # historical titles
IST      = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
HERE     = os.path.dirname(os.path.abspath(__file__))

AUG_SQL = """
with src as (select * from PROD_DB.DBT_CSP.TAS_INSTALL_EXECUTION_CANDIDATES),
pair as (   -- PAIR GRAIN: one row per (lead, CSP) that ever had it technician-assigned
  select CONNECTION_ID, CSP_ID, min(UPDATED_AT) f_ta from src
  where CURRENT_STATE='TECHNICIAN_ASSIGNED' group by 1,2),
instp as (  -- install credited to a CSP only if it completed under THAT CSP's own rows
  select CONNECTION_ID, CSP_ID,
    max(iff(OTP_VERIFIED=TRUE OR INSTALLATION_COMPLETED_AT is not null OR COMPLETED_STEP>=7,1,0)) i
  from src group by 1,2),
csp as (select CSP_ID, PARTNER_ID::string pid from PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_ACCOUNT
  where _fivetran_active=TRUE qualify row_number() over(partition by CSP_ID order by 1)=1)
select c.pid, count(*) tech, sum(coalesce(ip.i,0)) inst
from pair p join csp c on c.CSP_ID=p.CSP_ID
  left join instp ip on ip.CONNECTION_ID=p.CONNECTION_ID and ip.CSP_ID=p.CSP_ID
where dateadd(minute,330,p.f_ta)>='2026-08-01' and dateadd(minute,330,p.f_ta)<'2026-09-01'
  and p.f_ta <= dateadd(hour,-48,current_timestamp())
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
    if l.startswith("MG ENROLLED") and "excl" in l.lower(): return "ENR_EXVIOL"
    if l.startswith("MG ENROLLED"):  return "ENROLLED"
    if l.startswith("CONTROL") and "excl" in l.lower(): return "CTL_ANYLEAD"
    if "Sehat" in l:                 return "CTL_SEHAT"
    if "no MG" in l:                 return "CTL_NOMG"
    if l.startswith("CONTROL"):      return "CONTROL"
    return None


def belongs(g, sub, table, viol=0, nowork=0, has_aug=False):
    if table == "ALL":       return True
    if table == "ENROLLED":  return g == "ENROLLED"
    if table == "ENR_EXVIOL": return g == "ENROLLED" and not viol
    # "excl no-work CSPs" is LIVE, not a frozen list: control CSPs with >=1 matured August lead
    if table == "CTL_ANYLEAD": return g == "CONTROL" and has_aug
    if table == "CONTROL":   return g == "CONTROL"
    if table == "CTL_SEHAT": return g == "CONTROL" and sub == "SEHAT"
    if table == "CTL_NOMG":  return g == "CONTROL" and sub == "NOMG"
    return False


def colletter(i):  # 0-based -> A1 letter
    s = ""; i += 1
    while i:
        i, r = divmod(i - 1, 26); s = chr(65 + r) + s
    return s


def open_tab(sh):
    """Find the summary tab by gid first (survives renames), then by title."""
    try:
        return sh.get_worksheet_by_id(TAB_GID)
    except Exception:
        pass
    titles = {w.title: w for w in sh.worksheets()}
    for t in [TAB] + TAB_ALT:
        if t in titles:
            return titles[t]
    for t, w in titles.items():
        if "MG rate" in t or "MG Rate" in t:
            return w
    raise SystemExit("MG rate summary tab not found in sheet " + SHEET_ID)


def main():
    cfg = json.load(open(os.path.join(HERE, "mgrate_aug_config.json"), encoding="utf-8"))
    rows = mb(AUG_SQL)
    aug = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows if r[0]}
    print("aug matured pids:", len(aug),
          "tech", sum(v[0] for v in aug.values()), "inst", sum(v[1] for v in aug.values()), flush=True)

    tables = ["ALL", "ENROLLED", "ENR_EXVIOL", "CONTROL", "CTL_ANYLEAD", "CTL_SEHAT", "CTL_NOMG"]
    LIVE_COHORT = "CTL_ANYLEAD"   # membership recomputed each run -> its A:K are rewritten too
    # cohort -> aug_tech, aug_inst, mjt, mji, n_csps, base, jul_tech, jul_inst
    agg = {t: defaultdict(lambda: [0] * 8) for t in tables}
    members = {t: 0 for t in tables}
    for pid, rec in cfg.items():
        g, sub, coh, mjt, mji = rec[:5]
        viol   = rec[5] if len(rec) > 5 else 0
        nowork = rec[6] if len(rec) > 6 else 0
        base, jt, ji = (rec[7:10] + [0, 0, 0])[:3] if len(rec) > 7 else (0, 0, 0)
        at, ai = aug.get(pid, (0, 0))
        for t in tables:
            if belongs(g, sub, t, viol, nowork, at > 0):
                x = agg[t][coh]
                x[0] += at; x[1] += ai; x[2] += mjt; x[3] += mji
                x[4] += 1; x[5] += base; x[6] += jt; x[7] += ji
                members[t] += 1

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
    ws = open_tab(gc.open_by_key(SHEET_ID))
    grid = ws.get_all_values()

    updates = []; cur_tbl = None; aug_start = None; wrote = 0
    wrote_by_table = {}
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
            rec8 = agg[cur_tbl].get(a, [0] * 8)
        elif a == "Grand Total":
            rec8 = [0] * 8
            for v in agg[cur_tbl].values():
                for k in range(8): rec8[k] += v[k]
        else:
            continue
        atech, ainst, mjt, mji, ncsp, base, jt, ji = rec8
        vals = cells(atech, ainst, mjt, mji)
        r1 = i + 1
        if cur_tbl == LIVE_COHORT:
            pct = lambda n, d: (str(round(100 * n / d)) + "%") if d else "-"
            updates.append({"range": f"B{r1}:J{r1}", "values": [[
                ncsp, base, mjt, mji, jt, ji,
                round(base / ncsp) if ncsp else "", pct(mji, mjt), pct(ji, jt)]]})
        # write only tech/inst/% (L:N). Col O (Aug Δ vs MayJun %) and P (Base/CSP)
        # are live sheet formulas (=N/I-1, =ROUND(C/B)) — do NOT overwrite them.
        rng = f"{colletter(aug_start)}{r1}:{colletter(aug_start + 2)}{r1}"
        updates.append({"range": rng, "values": [vals[:3]]})
        wrote += 1
        wrote_by_table[cur_tbl] = wrote_by_table.get(cur_tbl, 0) + 1

    # live title for the recomputed-membership table
    for i, row in enumerate(grid):
        a = (row[0] if row else "").strip()
        if table_of(a) == LIVE_COHORT:
            updates.append({"range": f"A{i+1}", "values": [[
                "CONTROL — not enrolled, excl no-work CSPs "
                f"({members[LIVE_COHORT]} with >=1 matured Aug lead)"]]})
            break

    # header timestamp
    now = datetime.datetime.now(IST)
    hdr = grid[0][0] if grid and grid[0] else ""
    base = hdr.split("  [LIVE")[0]
    if not base:
        base = "MG INSTALL RATE"
    updates.append({"range": "A1", "values": [[
        base + f"  [LIVE: Aug cols L:N auto-refresh 24h (O=Δ% & P=Base/CSP are live formulas); matured>=48h; last run {now:%Y-%m-%d %H:%M IST}]"]]})

    # Guard: a renamed/removed table label would otherwise be skipped silently and the run
    # would still report success, leaving that table frozen at stale numbers. Fail loudly instead.
    missing = [t for t in tables if wrote_by_table.get(t, 0) == 0]
    thin = {t: n for t, n in wrote_by_table.items() if 0 < n < 6}
    if missing or thin:
        raise SystemExit(" | ".join([
            "ABORTED - refusing to write a partial refresh",
            "tables not found in the sheet: %s" % (missing or "none"),
            "tables with too few rows: %s" % (thin or "none"),
            "a table label row in column A was probably renamed, deleted or reordered; "
            "fix the label (or table_of()) and re-run - nothing was written"]))

    ws.batch_update(updates, value_input_option="RAW")
    print(f"wrote {wrote} rows across {len(tables)} tables; {len(updates)} ranges. OK {now:%Y-%m-%d %H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
