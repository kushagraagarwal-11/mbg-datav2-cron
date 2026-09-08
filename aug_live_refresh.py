# -*- coding: utf-8 -*-
"""MG install-rate — daily refresh of the AUGUST columns (L:N) and the
SEPTEMBER columns (R:T) in tab 'MG Rate Summary | Aug' (gid 1675005689) of the
MG install-rate sheet.

Each live month is anchored the same way as the frozen July baseline: a lead counts in
month M if it reached TECHNICIAN_ASSIGNED in M, AND is MATURED (>=48h since assignment,
so it had a fair window to install). install = OTP / completed / step>=7. PAIR GRAIN
(matches the July + May-Jun baselines): a lead that was technician-assigned to two CSPs
counts once for EACH of them; the install is credited to a CSP only if it completed under
that CSP's own rows, so a CSP that was handed a lead and lost it carries it as a miss.
(Verified against the frozen July baseline: denom 3127 vs 3145, installs 1668 vs 1670.
Crediting the final holder instead gives 1662 and does NOT reconcile.)

The install flag has NO month cutoff — it asks "did this (lead, CSP) pair ever install".
That is deliberate and matches the frozen columns: 106 of July's installs actually landed
in August, and 25 of May-Jun's landed in July. Cutting a live month at its own month-end
would make it the only column measured on a within-month rule.

Only the LIVE columns are rewritten each run — May-Jun / July / cohorts / counts (A:K),
the Aug delta formulas (O, Q) and Base/CSP (P) are untouched.

Seven tables share the same live columns and are refreshed together:
  ALL (710) · MG ENROLLED (477) · MG ENROLLED excl violation CSPs (461) ·
  CONTROL not-enrolled (233) · CONTROL not-enrolled excl no-work CSPs ·
  CONTROL Sehat-MG enrolled (72) · CONTROL no-MG (161).
Partition (group / Sehat-subgroup / re-derived cohort / May-Jun baseline) is
frozen in mgrate_aug_config.json so the split always matches the pasted baseline.
The "excl no-work CSPs" cohort stays defined on AUGUST activity (as its title says), so
adding September does not silently move its membership or its A:K baselines.

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

# Live months: (key, header token that locates the block, IST window [start, end))
MONTHS = [
    ("AUG", "Aug tech", "2026-08-01", "2026-09-01"),
    ("SEP", "Sep tech", "2026-09-01", "2026-10-01"),
]

MONTH_SQL = """
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
where dateadd(minute,330,p.f_ta)>='{start}' and dateadd(minute,330,p.f_ta)<'{end}'
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
    # "excl no-work CSPs" is LIVE, not a frozen list: control CSPs with >=1 matured August
    # lead. Deliberately still AUGUST-based now that September columns exist too — the
    # table title and its A:K baselines both describe that membership.
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

    month = {}
    for key, _tok, start, end in MONTHS:
        rows = mb(MONTH_SQL.format(start=start, end=end))
        month[key] = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows if r[0]}
        print(f"{key} matured pids: {len(month[key])}",
              "tech", sum(v[0] for v in month[key].values()),
              "inst", sum(v[1] for v in month[key].values()), flush=True)

    tables = ["ALL", "ENROLLED", "ENR_EXVIOL", "CONTROL", "CTL_ANYLEAD", "CTL_SEHAT", "CTL_NOMG"]
    LIVE_COHORT = "CTL_ANYLEAD"   # membership recomputed each run -> its A:K are rewritten too
    # cohort -> aug_tech, aug_inst, mjt, mji, n_csps, base, jul_tech, jul_inst, sep_tech, sep_inst
    agg = {t: defaultdict(lambda: [0] * 10) for t in tables}
    members = {t: 0 for t in tables}
    for pid, rec in cfg.items():
        g, sub, coh, mjt, mji = rec[:5]
        viol   = rec[5] if len(rec) > 5 else 0
        nowork = rec[6] if len(rec) > 6 else 0
        base, jt, ji = (rec[7:10] + [0, 0, 0])[:3] if len(rec) > 7 else (0, 0, 0)
        at, ai = month["AUG"].get(pid, (0, 0))
        st, si = month["SEP"].get(pid, (0, 0))
        for t in tables:
            if belongs(g, sub, t, viol, nowork, at > 0):
                x = agg[t][coh]
                x[0] += at; x[1] += ai; x[2] += mjt; x[3] += mji
                x[4] += 1; x[5] += base; x[6] += jt; x[7] += ji
                x[8] += st; x[9] += si
                members[t] += 1

    def cells(tech, inst):
        """[tech, installs, install %] — the Delta cells are live sheet formulas."""
        p = round(100 * inst / tech) if tech else None
        return [tech, inst, (str(p) + "%") if p is not None else "-"]

    gc = gspread.authorize(creds())
    ws = open_tab(gc.open_by_key(SHEET_ID))
    grid = ws.get_all_values()

    updates = []; cur_tbl = None; starts = {}; wrote = 0
    wrote_by_table = {k: defaultdict(int) for k, _t, _s, _e in MONTHS}
    for i, row in enumerate(grid):
        a = (row[0] if row else "").strip()
        t = table_of(a)
        if t:
            cur_tbl = t
        if a == "cohort":
            # locate each live month block by its own header token, per table
            starts = {}
            for key, tok, _s, _e in MONTHS:
                j = next((j for j, c in enumerate(row) if tok in c), None)
                if j is not None:
                    starts[key] = j
            continue
        if cur_tbl is None or not starts:
            continue
        if a in ("0", "0.5-4", "4.5-8", "8.5-12", "12.5-24", ">24"):
            rec = agg[cur_tbl].get(a, [0] * 10)
        elif a == "Grand Total":
            rec = [0] * 10
            for v in agg[cur_tbl].values():
                for k in range(10): rec[k] += v[k]
        else:
            continue
        atech, ainst, mjt, mji, ncsp, base, jt, ji, stech, sinst = rec
        r1 = i + 1
        if cur_tbl == LIVE_COHORT:
            pct = lambda n, d: (str(round(100 * n / d)) + "%") if d else "-"
            updates.append({"range": f"B{r1}:J{r1}", "values": [[
                ncsp, base, mjt, mji, jt, ji,
                round(base / ncsp) if ncsp else "", pct(mji, mjt), pct(ji, jt)]]})
        # write only tech/inst/% per month. The Delta columns (O = Aug vs MayJun,
        # Q = Aug vs Jul) and P (Base/CSP) are live sheet formulas — do NOT overwrite them.
        vals = {"AUG": cells(atech, ainst), "SEP": cells(stech, sinst)}
        for key, _tok, _s, _e in MONTHS:
            j = starts.get(key)
            if j is None:
                continue
            updates.append({"range": f"{colletter(j)}{r1}:{colletter(j + 2)}{r1}",
                            "values": [vals[key]]})
            wrote_by_table[key][cur_tbl] += 1
        wrote += 1

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
        base + "  [LIVE: Aug cols L:N + Sep cols R:T auto-refresh 24h "
        "(O=Aug \u0394% & P=Base/CSP are live formulas); matured>=48h; "
        f"last run {now:%Y-%m-%d %H:%M IST}]"]]})

    # Guard: a renamed/removed table label (or a missing month header) would otherwise be
    # skipped silently and the run would still report success, leaving that block frozen at
    # stale numbers. Fail loudly instead.
    problems = []
    for key, tok, _s, _e in MONTHS:
        missing = [t for t in tables if wrote_by_table[key].get(t, 0) == 0]
        thin = {t: n for t, n in wrote_by_table[key].items() if 0 < n < 6}
        if missing or thin:
            problems.append(f"{key} (header '{tok}') -> tables not found: {missing or 'none'}; "
                            f"tables with too few rows: {thin or 'none'}")
    if problems:
        raise SystemExit(" | ".join([
            "ABORTED - refusing to write a partial refresh"] + problems + [
            "a table label in column A, or a month header, was probably renamed, deleted or "
            "reordered; fix the label (or table_of()) and re-run - nothing was written"]))

    ws.batch_update(updates, value_input_option="RAW")
    print(f"wrote {wrote} rows x {len(MONTHS)} months across {len(tables)} tables; "
          f"{len(updates)} ranges. OK {now:%Y-%m-%d %H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
