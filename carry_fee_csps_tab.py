# -*- coding: utf-8 -*-
"""Refresh the 'Carry Fee CSPs' tab of the 'For Return' sheet (1VNhP2...), alongside
the col J / col L sync in netbox_status_sync.py (same 15-min cron).

Four stacked day-by-day summaries — Overall, Delhi, Mumbai, Bharat — from 16-Aug-2026
(carry fee go-live) through today, cols A:G:
    A date
    B # CSPs with Carry fees charged on      C Active Base
    D # CSPs with No carry fee charged on    E Active base
    F # CSPs with No pending devices         G Active base
    H # Devices Carry Fee charged on          I  ...+ retrieval pending
       (no pending device = none of that CSP's devices is CARRY_FEE_ACTIVE or RETRIEVAL_PENDING)

Scope is read LIVE from the Delhi / Mumbai / Bharat tabs of the same workbook (727 CSPs
today), so editing those tabs re-scopes this one. 'Bharat' = every CSP not in the Delhi
or Mumbai tab.

  charged      = a distinct CARRY_FEE entry in WALLET_LEDGER_ENTRIES on that date
                 (launch day 16-Aug wrote ~2 entries per CSP, hence DISTINCT not COUNT(*))
  Active Base  = ACTIVE_R15_CUSTOMERS from PROD_DB.PUBLIC.CUSTOMER_BASE that day
  pending      = device state reconstructed from the NETBOX_CUSTODY SCD2 history
                 (_FIVETRAN_START/_FIVETRAN_END) as of end of that day, restricted to the
                 devices listed on the 'Charged & Pending Devices New' tab and attributed
                 to the CSP that tab pairs them with.
Creds from env (GitHub Actions secrets); falls back to local files for laptop runs."""
import os, json, datetime, collections, urllib.request as U

SHEET    = "1VNhP2DNwnF73UiRoO_E-HtkNwCtETr8ZVzHcpKINBfs"
TAB_GID  = 1171963187                  # "Carry Fee CSPs"
BASE_GID = 2056261339                  # "Charged & Pending Devices New"
START    = datetime.date(2026, 8, 16)  # carry fee go-live
# (gid, 0-based column holding CSP ID) — Delhi tab has no Cohort column, the others do
CITY_TABS = [(232007919, "Delhi", 0), (1220846429, "Mumbai", 1), (124782724, "Bharat", 1)]

MB_KEY = os.environ.get("MB_KEY")  # PUBLIC repo — never hardcode; provided via MB_KEY secret
if not MB_KEY:
    raise SystemExit("MB_KEY not set — provide it via the MB_KEY env var / GitHub secret.")

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
MIN_SCOPE = 100   # sanity floor — a mangled city tab must not silently shrink the report
NETBOX = "PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.NETBOX_CUSTODY"


def log(m):
    print("[%s] %s" % (datetime.datetime.now(IST).strftime("%H:%M:%S"), m), flush=True)


def mb(q):
    r = U.Request("https://metabase.wiom.in/api/dataset",
                  data=json.dumps({"database": 113, "type": "native", "native": {"query": q}}).encode(),
                  headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
    j = json.loads(U.urlopen(r, timeout=300).read().decode())
    if j.get("status") == "failed" or not j.get("data"):
        raise RuntimeError("Metabase query failed: %s" % str(j.get("error"))[:400])
    rows = j["data"]["rows"]
    if len(rows) >= 2000:
        raise RuntimeError("hit the 2000-row cap (%d rows) — re-bucket this query" % len(rows))
    return rows


# ---- 1) live scope from the city tabs, and the device -> CSP pairing from the base tab ----
import gspread
from google.oauth2.service_account import Credentials
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_sa = os.environ.get("GOOGLE_SA_JSON")
creds = (Credentials.from_service_account_info(json.loads(_sa), scopes=_SCOPES) if _sa
         else Credentials.from_service_account_file(r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json", scopes=_SCOPES))
sh = gspread.authorize(creds).open_by_key(SHEET)

city = {}
for gid, name, idx in CITY_TABS:
    for r in sh.get_worksheet_by_id(gid).get_values()[1:]:     # row 1 is the header
        if len(r) > idx and r[idx].strip():
            city.setdefault(r[idx].strip(), name)
counts = collections.Counter(city.values())
log("scope: %d CSPs (%s)" % (len(city), dict(counts)))
if len(city) < MIN_SCOPE:
    raise SystemExit("only %d CSPs in scope (< %d floor) — refusing to write" % (len(city), MIN_SCOPE))


def norm(x):
    x = str(x or "").strip()
    return x[:-2] if x.endswith(".0") else x


dev2csp = {}
for r in sh.get_worksheet_by_id(BASE_GID).get_values("A2:C"):
    if len(r) >= 3 and norm(r[2]):
        dev2csp[norm(r[2])] = norm(r[0])
log("base tab: %d devices across %d CSPs" % (len(dev2csp), len(set(dev2csp.values()))))

today = datetime.datetime.now(IST).date()
ndays = (today - START).days + 1
if ndays < 1:
    raise SystemExit(0)
days = [START + datetime.timedelta(days=i) for i in range(ndays)]
DAYS_SQL = ("SELECT DATEADD(day, SEQ4(), DATE '%s') AS d FROM TABLE(GENERATOR(ROWCOUNT => %d))"
            % (START.isoformat(), ndays))
allids = "','".join(sorted(city))


def idlist(c):
    return "','".join(sorted(k for k, v in city.items() if v == c))

# ---- 2) CSPs charged each day ----
charged = {d.isoformat(): set() for d in days}
for d, ids in mb("""
SELECT DATE(CREATED_AT) AS d, LISTAGG(DISTINCT CSP_ID, ',') AS ids
FROM PROD_DB.CSP_PAYMENT_SETTLEMENT_SERVICE_CSP_PAYMENT_SETTLEMENT_SERVICE.WALLET_LEDGER_ENTRIES
WHERE ENTRY_TYPE = 'CARRY_FEE' AND _FIVETRAN_ACTIVE = TRUE AND CREATED_AT >= '%s'
GROUP BY 1""" % START.isoformat()):
    k = str(d)[:10]
    if k in charged and ids:
        charged[k] = set(ids.split(","))

# ---- 3) per-CSP active base per day (as-of: CUSTOMER_BASE lands a day behind) ----
r15, asof = {}, {}
for d, ids, bdate in mb("""
WITH scope AS (SELECT DISTINCT CSP_ID, PARTNER_ID FROM PROD_DB.DBT_CSP.DIM_CSP
               WHERE ETL_CURRENT = TRUE AND CSP_ID IN ('%(ids)s')),
days AS (%(days)s),
cb AS (
  SELECT CASE WHEN partner_account_id LIKE '%%.0'
              THEN LEFT(partner_account_id, LENGTH(partner_account_id)-2)
              ELSE partner_account_id END AS pid,
         DATE(date) AS d, MAX(TRY_CAST(active_r15_customers AS NUMBER)) AS r15
  FROM PROD_DB.PUBLIC.CUSTOMER_BASE
  WHERE DATE(date) >= DATEADD(day, -30, DATE '%(start)s')
  GROUP BY 1, 2
),
grid AS (SELECT s.CSP_ID, s.PARTNER_ID, d.d FROM scope s CROSS JOIN days d),
csp_day AS (
  SELECT g.CSP_ID, g.d, cb.r15, cb.d AS cb_d
  FROM grid g LEFT JOIN cb ON cb.pid = g.PARTNER_ID::string AND cb.d <= g.d
  QUALIFY ROW_NUMBER() OVER (PARTITION BY g.CSP_ID, g.d ORDER BY cb.d DESC NULLS LAST) = 1
)
SELECT d, LISTAGG(CSP_ID || ':' || COALESCE(r15, 0), ',') AS ids, MAX(cb_d) AS base_asof
FROM csp_day GROUP BY 1""" % {"ids": allids, "days": DAYS_SQL, "start": START.isoformat()}):
    k = str(d)[:10]
    r15[k] = {p.split(":")[0]: int(p.split(":")[1]) for p in ids.split(",")} if ids else {}
    if bdate:
        asof[k] = str(bdate)[:10]

# ---- 4) device state per day, from the SCD2 history ----
# A device is "pending" if, as of end of that day, it was carry-fee-active or
# retrieval-pending. Today is capped at now rather than a future end-of-day.
# Non-scope CSPs are tagged OTHER rather than filtered out: a base-tab device can
# sit with a CSP outside the 727 today, and dropping it would miscount col F.
CITY_CASE = """CASE WHEN %(c)s IN ('%(delhi)s')  THEN 'Delhi'
                    WHEN %(c)s IN ('%(mumbai)s') THEN 'Mumbai'
                    WHEN %(c)s IN ('%(bharat)s') THEN 'Bharat'
                    ELSE 'OTHER' END"""


def city_case(col):
    return CITY_CASE % {"c": col, "delhi": idlist("Delhi"),
                        "mumbai": idlist("Mumbai"), "bharat": idlist("Bharat")}


scd = collections.defaultdict(set)     # (day, kind, city) -> device ids
for d, kind, cty, _b, ids in mb("""
WITH days AS (%(days)s)
SELECT d.d,
       CASE WHEN n.STATUS = 'RETRIEVAL_PENDING' THEN 'RP' ELSE 'CF' END AS kind,
       %(case)s AS city,
       MOD(ABS(HASH(n.DEVICE_ID)), 8) AS b,
       LISTAGG(DISTINCT n.DEVICE_ID, ',') AS ids
FROM days d
JOIN %(nb)s n
  ON n._FIVETRAN_START <= LEAST(DATEADD(day,1,d.d)::TIMESTAMP_LTZ, CURRENT_TIMESTAMP())
 AND n._FIVETRAN_END   >  LEAST(DATEADD(day,1,d.d)::TIMESTAMP_LTZ, CURRENT_TIMESTAMP())
WHERE n.CARRY_FEE_ACTIVE = TRUE OR n.STATUS = 'RETRIEVAL_PENDING'
GROUP BY 1, 2, 3, 4""" % {"days": DAYS_SQL, "nb": NETBOX, "case": city_case("n.CSP_ID")}):
    if ids:
        scd[(str(d)[:10], kind, cty)].update(ids.split(","))

# per (day, csp) count of pending devices, restricted to the base tab's device list
CITIES = ("Delhi", "Mumbai", "Bharat")
npend = {}
for dt in days:
    k = dt.isoformat()
    allpend = set()
    for kind in ("CF", "RP"):
        for cty in CITIES + ("OTHER",):
            allpend |= scd[(k, kind, cty)]
    c = collections.Counter()
    for dv in allpend:
        owner = dev2csp.get(dv)
        if owner:
            c[owner] += 1
    npend[k] = c

# ---- 4b) devices carry fee was actually CHARGED on (the money leg), per day x city ----
# Ledger fact, deliberately NOT restricted to the base tab: that tab is a current
# snapshot and has already dropped returned devices, which would undercount history.
charged_dev = collections.defaultdict(set)
for d, cty, _b, ids in mb("""
WITH ch AS (
  SELECT DATE(w.CREATED_AT) AS d, w.CSP_ID, TRIM(f.value::string) AS dev
  FROM PROD_DB.CSP_PAYMENT_SETTLEMENT_SERVICE_CSP_PAYMENT_SETTLEMENT_SERVICE.WALLET_LEDGER_ENTRIES w,
       LATERAL SPLIT_TO_TABLE(TRY_PARSE_JSON(w.REMARKS):device_id::string, ',') f
  WHERE w.ENTRY_TYPE = 'CARRY_FEE' AND w._FIVETRAN_ACTIVE = TRUE AND w.CREATED_AT >= '%(start)s'
    AND w.CSP_ID IN ('%(ids)s'))
SELECT d, %(case)s AS city, MOD(ABS(HASH(dev)), 8) AS b, LISTAGG(DISTINCT dev, ',') AS ids
FROM ch GROUP BY 1, 2, 3""" % {"start": START.isoformat(), "ids": allids,
                               "case": city_case("CSP_ID")}):
    if ids:
        charged_dev[(str(d)[:10], cty)].update(ids.split(","))

# ---- 5) roll up per day x city ----
HDR = ["", "# CSPs with Carry fees charged on", "Active Base",
       "# CSPs with No carry fee charged on", "Active base",
       "# CSPs with No pending devices (i.e. no carry fees + retrieval pending)", "Active base",
       "# Devices Carry Fee charged on",
       "# Devices Carry Fee charged on + retrival pending"]
blocks = [("Overall", len(city)), ("Delhi", counts["Delhi"]),
          ("Mumbai", counts["Mumbai"]), ("Bharat", counts["Bharat"])]
NCOL = len(HDR)

grid, stale = [], []
for bi, (name, n) in enumerate(blocks):
    members = sorted(city) if name == "Overall" else sorted(c for c in city if city[c] == name)
    towns = CITIES if name == "Overall" else (name,)
    grid.append(["%s (%d CSPs)" % (name, n)] + [""] * (NCOL - 1))
    grid.append(HDR)
    for dt in days:
        k = dt.isoformat()
        base, chg, pc = r15.get(k, {}), charged.get(k, set()), npend.get(k, {})
        yes = [c for c in members if c in chg]
        no = [c for c in members if c not in chg]
        clear = [c for c in members if pc.get(c, 0) == 0]
        # device-level: union across the block's cities (a device belongs to one CSP,
        # so the city sets are disjoint and Overall is just their union)
        dch = set().union(*[charged_dev[(k, t)] for t in towns]) if towns else set()
        drp = set().union(*[scd[(k, "RP", t)] for t in towns]) if towns else set()
        if bi == 0 and asof.get(k) and asof[k] != k:
            stale.append("%s (base as of %s)" % (k, asof[k]))
        grid.append([dt.strftime("%dth %B").lstrip("0"),
                     len(yes), sum(base.get(c, 0) for c in yes),
                     len(no), sum(base.get(c, 0) for c in no),
                     len(clear), sum(base.get(c, 0) for c in clear),
                     len(dch), len(dch | drp)])
    if bi < len(blocks) - 1:
        grid.append([""] * NCOL)

ws = sh.get_worksheet_by_id(TAB_GID)
if ws.row_count < len(grid):
    ws.add_rows(len(grid) - ws.row_count + 20)
ws.update(values=grid, range_name="A1:I%d" % len(grid), value_input_option="USER_ENTERED")
ws.update_note("A1",
               "Auto-refreshed ~every 15 min by mbg-datav2-cron / netbox-status.yml\n"
               "Scope read live from the Delhi/Mumbai/Bharat tabs.\n"
               "Active Base = ACTIVE_R15_CUSTOMERS (CUSTOMER_BASE); today carries the\n"
               "latest snapshot forward until that day lands, then self-corrects.\n"
               "Last update: %s IST" % datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M"))

ov = grid[1 + ndays]        # Overall block: title, header, then one row per day
log("wrote A1:I%d — %d days x 4 blocks; Overall %s: %s charged / %s not / %s no-pending"
    "; devices %s charged, %s charged+RP"
    % (len(grid), ndays, days[-1].isoformat(), ov[1], ov[3], ov[5], ov[7], ov[8]))
if stale:
    log("carried base forward for: %s" % ", ".join(stale))
