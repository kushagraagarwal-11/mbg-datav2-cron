# -*- coding: utf-8 -*-
"""Refresh the 'Carry Fee CSPs' tab of the 'For Return' sheet (1VNhP2...), alongside
the col J / col L sync in netbox_status_sync.py (same 15-min cron).

Four stacked day-by-day summaries — Overall, Delhi, Mumbai, Bharat — from 16-Aug-2026
(carry fee go-live) through today, each with:
    # CSPs with Carry fees charged on | Active Base | # CSPs with No carry fee charged on | Active base

Scope is read LIVE from the Delhi / Mumbai / Bharat tabs of the same workbook (727 CSPs
today), so editing those tabs re-scopes this one. 'Bharat' = every CSP not in the Delhi
or Mumbai tab.
  charged     = a distinct CARRY_FEE entry in WALLET_LEDGER_ENTRIES on that date
                (launch day 16-Aug wrote ~2 entries per CSP, hence DISTINCT not COUNT(*))
  Active Base = ACTIVE_R15_CUSTOMERS from PROD_DB.PUBLIC.CUSTOMER_BASE that day
Creds from env (GitHub Actions secrets); falls back to local files for laptop runs."""
import os, json, datetime, collections, urllib.request as U

SHEET    = "1VNhP2DNwnF73UiRoO_E-HtkNwCtETr8ZVzHcpKINBfs"
TAB_GID  = 1171963187                 # "Carry Fee CSPs"
START    = datetime.date(2026, 8, 16)  # carry fee go-live
# (gid, 0-based column holding CSP ID) — Delhi tab has no Cohort column, the others do
CITY_TABS = [(232007919, "Delhi", 0), (1220846429, "Mumbai", 1), (124782724, "Bharat", 1)]

MB_KEY = os.environ.get("MB_KEY")  # PUBLIC repo — never hardcode; provided via MB_KEY secret
if not MB_KEY:
    raise SystemExit("MB_KEY not set — provide it via the MB_KEY env var / GitHub secret.")

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
MIN_SCOPE = 100   # sanity floor — a mangled city tab must not silently shrink the report


def log(m):
    print("[%s] %s" % (datetime.datetime.now(IST).strftime("%H:%M:%S"), m), flush=True)


def mb(q):
    r = U.Request("https://metabase.wiom.in/api/dataset",
                  data=json.dumps({"database": 113, "type": "native", "native": {"query": q}}).encode(),
                  headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
    j = json.loads(U.urlopen(r, timeout=300).read().decode())
    if j.get("status") == "failed" or not j.get("data"):
        raise RuntimeError("Metabase query failed: %s" % str(j.get("error"))[:400])
    return j["data"]["rows"]


# ---- 1) live scope from the city tabs ----
import gspread
from google.oauth2.service_account import Credentials
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_sa = os.environ.get("GOOGLE_SA_JSON")
creds = (Credentials.from_service_account_info(json.loads(_sa), scopes=_SCOPES) if _sa
         else Credentials.from_service_account_file(r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json", scopes=_SCOPES))
sh = gspread.authorize(creds).open_by_key(SHEET)

city = {}
for gid, name, idx in CITY_TABS:
    vals = sh.get_worksheet_by_id(gid).get_values()
    for r in vals[1:]:                       # row 1 is the header
        if len(r) > idx and r[idx].strip():
            city.setdefault(r[idx].strip(), name)
counts = collections.Counter(city.values())
log("scope: %d CSPs (%s)" % (len(city), dict(counts)))
if len(city) < MIN_SCOPE:
    raise SystemExit("only %d CSPs in scope (< %d floor) — refusing to write" % (len(city), MIN_SCOPE))

today = datetime.datetime.now(IST).date()
ndays = (today - START).days + 1
if ndays < 1:
    raise SystemExit(0)


def idlist(c):
    return "','".join(sorted(k for k, v in city.items() if v == c))


# ---- 2) one query -> per (day, city) counts + active base ----
SQL = """
WITH scope AS (
  SELECT DISTINCT CSP_ID, PARTNER_ID,
         CASE WHEN CSP_ID IN ('%(delhi)s')  THEN 'Delhi'
              WHEN CSP_ID IN ('%(mumbai)s') THEN 'Mumbai'
              ELSE 'Bharat' END AS city
  FROM PROD_DB.DBT_CSP.DIM_CSP
  WHERE ETL_CURRENT = TRUE AND CSP_ID IN ('%(delhi)s','%(mumbai)s','%(bharat)s')
),
days AS (SELECT DATEADD(day, SEQ4(), DATE '%(start)s') AS d
         FROM TABLE(GENERATOR(ROWCOUNT => %(ndays)d))),
grid AS (SELECT s.CSP_ID, s.PARTNER_ID, s.city, d.d FROM scope s CROSS JOIN days d),
charged AS (
  SELECT DISTINCT CSP_ID, DATE(CREATED_AT) AS d
  FROM PROD_DB.CSP_PAYMENT_SETTLEMENT_SERVICE_CSP_PAYMENT_SETTLEMENT_SERVICE.WALLET_LEDGER_ENTRIES
  WHERE ENTRY_TYPE = 'CARRY_FEE' AND _FIVETRAN_ACTIVE = TRUE AND CREATED_AT >= '%(start)s'
),
cb AS (
  SELECT CASE WHEN partner_account_id LIKE '%%.0'
              THEN LEFT(partner_account_id, LENGTH(partner_account_id)-2)
              ELSE partner_account_id END AS pid,
         DATE(date) AS d,
         MAX(TRY_CAST(active_r15_customers AS NUMBER)) AS r15
  FROM PROD_DB.PUBLIC.CUSTOMER_BASE
  WHERE DATE(date) >= DATEADD(day, -30, DATE '%(start)s')
  GROUP BY 1, 2
),
-- CUSTOMER_BASE lands a day behind, so today has no snapshot yet. Take each CSP's
-- most recent base on or before the grid day ("as of" join) instead of dropping it:
-- the charged / not-charged split is still today's, only the per-CSP base value is
-- carried forward, and it self-corrects the moment the real snapshot arrives.
csp_day AS (
  SELECT g.CSP_ID, g.city, g.d, c.CSP_ID AS chg, cb.r15, cb.d AS cb_d
  FROM grid g
  LEFT JOIN charged c ON c.CSP_ID = g.CSP_ID AND c.d = g.d
  LEFT JOIN cb       ON cb.pid = g.PARTNER_ID::string AND cb.d <= g.d
  QUALIFY ROW_NUMBER() OVER (PARTITION BY g.CSP_ID, g.d ORDER BY cb.d DESC NULLS LAST) = 1
)
SELECT d, city,
       COUNT_IF(chg IS NOT NULL)                                  AS csps_charged,
       SUM(CASE WHEN chg IS NOT NULL THEN COALESCE(r15,0) END)    AS base_charged,
       COUNT_IF(chg IS NULL)                                      AS csps_not,
       SUM(CASE WHEN chg IS NULL THEN COALESCE(r15,0) END)        AS base_not,
       COUNT(r15)                                                 AS cb_rows,
       MAX(cb_d)                                                  AS base_asof
FROM csp_day
GROUP BY 1, 2 ORDER BY 1, 2
""" % {"delhi": idlist("Delhi"), "mumbai": idlist("Mumbai"), "bharat": idlist("Bharat"),
       "start": START.isoformat(), "ndays": ndays}

rows = mb(SQL)
per, cbrows, asof = {}, collections.Counter(), {}
for d, c, chg, bchg, notc, bnot, nc, bdate in rows:
    k = str(d)[:10]
    per[(k, c)] = (chg, bchg, notc, bnot)
    cbrows[k] += nc
    if bdate:
        asof[k] = max(asof.get(k, ""), str(bdate)[:10])

days = [(START + datetime.timedelta(days=i)) for i in range(ndays)]
for dt in days:                                    # derive Overall from the three cities
    k = dt.isoformat()
    t = [0, 0, 0, 0]
    for c in ("Delhi", "Mumbai", "Bharat"):
        v = per.get((k, c), (0, 0, 0, 0))
        for i in range(4):
            t[i] += (v[i] or 0)
    per[(k, "Overall")] = tuple(t)

# ---- 3) render the four blocks ----
# Date labels follow the tab's existing convention verbatim: "<day>th <Month>"
# (so 16th..22nd August reproduce exactly as already typed, and new days match).
HDR = ["", "# CSPs with Carry fees charged on", "Active Base",
       "# CSPs with No carry fee charged on", "Active base"]
blocks = [("Overall", len(city)), ("Delhi", counts["Delhi"]),
          ("Mumbai", counts["Mumbai"]), ("Bharat", counts["Bharat"])]

grid, stale = [], []
for bi, (name, n) in enumerate(blocks):
    grid.append(["%s (%d CSPs)" % (name, n), "", "", "", ""])
    grid.append(HDR)
    for dt in days:
        k = dt.isoformat()
        chg, bchg, notc, bnot = per[(k, name)]
        if bi == 0 and asof.get(k) and asof[k] != k:
            stale.append("%s (base as of %s)" % (k, asof[k]))
        grid.append([dt.strftime("%dth %B").lstrip("0"), chg, bchg, notc, bnot])
    if bi < len(blocks) - 1:
        grid.append(["", "", "", "", ""])

ws = sh.get_worksheet_by_id(TAB_GID)
if ws.row_count < len(grid):
    ws.add_rows(len(grid) - ws.row_count + 20)
ws.update(values=grid, range_name="A1:E%d" % len(grid), value_input_option="USER_ENTERED")

ws.update_note("A1",
               "Auto-refreshed ~every 15 min by mbg-datav2-cron / netbox-status.yml\n"
               "Scope read live from the Delhi/Mumbai/Bharat tabs.\n"
               "Last update: %s IST" % datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M"))

ov = per[(days[-1].isoformat(), "Overall")]
log("wrote A1:E%d — %d days x 4 blocks; latest %s: %d charged (base %s) / %d not (base %s)"
    % (len(grid), ndays, days[-1].isoformat(), ov[0], ov[1], ov[2], ov[3]))
if stale:
    log("carried base forward for: %s" % ", ".join(stale))
