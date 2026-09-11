# -*- coding: utf-8 -*-
"""Daily retrieval-request log -> Delhi / Mumbai / Bharat tabs.

One row per (CSP, request date) from 2-Sep-2026 onwards. A CSP appears on a date
only if it asked for at least one device to be retrieved that day; if it asks
again on a later date it gets a NEW row for that date, never a change to the old
one.

  Request date            the day the retrieval was requested (IST)
  Total Device to Return  that CSP's retrieval-pending devices as at the END of
                          the request date, including the ones raised that day,
                          read from NETBOX's SCD2 trail.

  Current Date Req Count  devices that CSP requested on the request date itself
  Priority                P0 20+, P1 10-19, P2 5-9, P3 1-4, on Total Device to Return

Runs ONCE a day at 00:01 IST, so every run covers the day that has just ended and
each row is written complete, never as a part-day figure.

The write is APPEND ONLY. A row is keyed on (Request date, csp_id); if that key is
already on the tab it is left untouched, and nothing right of column H is ever
written. Add your own columns and work in them freely — they survive every run.

Scope = every CSP. (EXCLUDE below is empty; fill it to filter CSPs out again.)
Creds from env (GitHub Actions secrets); local files as a laptop fallback.
"""
import os, json, datetime, collections
import urllib.request as U

SHEET = "1bm8eTVzaTRG68s_MXy9WOVVGi1kJFJ3XrdYbvThnU3s"
NB    = "PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.NETBOX_CUSTODY"
ACCT  = "PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_ACCOUNT"
JK    = "PROD_DB.PUBLIC.PARTNER_JANAM_KUNDLI"
START = datetime.date(2026, 9, 2)
IST   = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
PINK  = {"red": 0.851, "green": 0.0, "blue": 0.553}

MB_KEY = os.environ.get("MB_KEY")  # PUBLIC repo — never hardcode
if not MB_KEY:
    raise SystemExit("MB_KEY not set — provide it via the MB_KEY env var / GitHub secret.")

# No CSP filter: the tabs cover EVERY CSP. This previously held the 727 consented
# CSPs as an exclusion list; removed at the user's request on 11-Sep-2026.
# Put CSP ids back in this list to re-introduce a filter.
EXCLUDE = []


def log(m):
    print("[%s] %s" % (datetime.datetime.now(IST).strftime("%H:%M:%S"), m), flush=True)


def mb(q):
    r = U.Request("https://metabase.wiom.in/api/dataset",
                  data=json.dumps({"database": 113, "type": "native",
                                   "native": {"query": q}}).encode(),
                  headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
    j = json.loads(U.urlopen(r, timeout=600).read().decode())
    if j.get("status") == "failed" or not j.get("data"):
        raise RuntimeError("Metabase query failed: %s" % str(j.get("error"))[:400])
    rows = j["data"]["rows"]
    if len(rows) >= 2000:
        raise RuntimeError("hit the 2000-row cap (%d rows) — re-bucket" % len(rows))
    return rows


EXC_SQL = ("" if not EXCLUDE else
           " AND CSP_ID NOT IN (%s)" % ",".join("'%s'" % x for x in sorted(EXCLUDE)))


def priority(total):
    if total >= 20: return "P0"
    if total >= 10: return "P1"
    if total >= 5:  return "P2"
    return "P3"


def bucket(c):
    c = (c or "").strip().lower()
    return "Delhi" if c == "delhi" else "Mumbai" if c == "mumbai" else "Bharat"


now_ist = datetime.datetime.now(IST)
today = now_ist.date()

# Only days that have fully closed are written. Rows are frozen once appended, so a
# run part-way through a day would otherwise pin that day at a half-count forever.
# At the 00:01 slot this excludes the day that is one minute old and nothing else.
CUTOFF = today - datetime.timedelta(days=1)

# ---- 1) who requested a retrieval on which day, and how many devices ----
# Queried one day at a time: a single query over the whole range grows by ~50 rows a
# day and would silently brush the ~2000-row API cap within a couple of months.
req = []
d = START
while d <= CUTOFF:
    req += [(d.isoformat(), c, n) for c, n in mb("""
SELECT CSP_ID, COUNT(DISTINCT DEVICE_ID) FROM (
  SELECT DEVICE_ID, CSP_ID
  FROM %s
  WHERE RETRIEVAL_START_DATE IS NOT NULL
    AND DATE(CONVERT_TIMEZONE('Asia/Kolkata', RETRIEVAL_START_DATE)) = '%s'
    AND CSP_ID IS NOT NULL%s
  QUALIFY ROW_NUMBER() OVER (PARTITION BY DEVICE_ID ORDER BY _FIVETRAN_START) = 1)
GROUP BY 1""" % (NB, d.isoformat(), EXC_SQL))]
    d += datetime.timedelta(days=1)
log("request rows (csp x date): %d, covering %s..%s"
    % (len(req), START.isoformat(), CUTOFF.isoformat()))

dates = sorted({r[0] for r in req})
csps = sorted({r[1] for r in req})
if not csps:
    log("no retrieval requests in scope since %s — nothing to write" % START)
    raise SystemExit(0)

# ---- 2) retrieval-pending total per CSP, as at the END of each request date ----
# Every date here has closed, so all of them come from the SCD2 trail and reproduce
# exactly on every run.
totals = {}
for d in dates:
    for csp, n in mb("""SELECT CSP_ID, COUNT(DISTINCT DEVICE_ID) FROM %s
WHERE _FIVETRAN_START <= '%s 23:59:59' AND _FIVETRAN_END > '%s 23:59:59'
  AND STATUS = 'RETRIEVAL_PENDING'
  AND CSP_ID IS NOT NULL%s
GROUP BY 1""" % (NB, d, d, EXC_SQL)):
        totals[(d, csp)] = n
    log("  %s -> totals for %d CSPs" % (d, sum(1 for k in totals if k[0] == d)))

# ---- 3) CSP attributes ----
lit = ",".join("'%s'" % c for c in csps)
# Prefer the live row, but fall back to the most recent superseded one: a handful of
# CSPs (a0a7d0, a0a7f9, ...) have no _FIVETRAN_ACTIVE record at all, and filtering on
# active alone left their name / partner_id / mobile blank.
acct = {x[0]: x for x in mb("""SELECT CSP_ID, PARTNER_ID, NAME, MOBILE_NUMBER
FROM %s WHERE CSP_ID IN (%s)
QUALIFY ROW_NUMBER() OVER (PARTITION BY CSP_ID
                           ORDER BY _FIVETRAN_ACTIVE DESC, _FIVETRAN_START DESC) = 1"""
                            % (ACCT, lit))}
jk = {x[0]: x for x in mb("""SELECT CSP_ID, ANY_VALUE(PARTNER_NAME), ANY_VALUE(CITY)
FROM %s WHERE CSP_ID IN (%s) GROUP BY 1""" % (JK, lit))}
log("attributes: CSP_ACCOUNT %d/%d, JANAM_KUNDLI %d/%d"
    % (len(acct), len(csps), len(jk), len(csps)))

# ---- 4) shape ----
HDR = ["Request date", "csp_id", "partner_id", "csp_name", "csp_mobile",
       "Total Device to Return", "Current Date Req Count", "Priority"]
rows = collections.defaultdict(list)
for d, csp, n in req:
    a = acct.get(csp, [csp, "", "", ""])
    j = jk.get(csp, [csp, "", ""])
    total = totals.get((d, csp), 0)
    rows[bucket(j[2])].append([d, csp, str(a[1] or ""), a[2] or j[1] or "",
                               str(a[3] or ""), total, n, priority(total)])
for c in rows:
    rows[c].sort(key=lambda r: (r[0], -r[5], r[1]))

# ---- 5) write — APPEND ONLY ----
# The team adds their own columns to the right of H and works in them, so this
# never clears, never resizes, and never reorders. A row is identified by
# (Request date, csp_id): if that key is already on the tab it is left completely
# alone, which keeps both the frozen figures and anything typed alongside them.
# Only genuinely new keys are appended, at the bottom, in columns A:H only.
import gspread
from google.oauth2.service_account import Credentials
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_sa = os.environ.get("GOOGLE_SA_JSON")
creds = (Credentials.from_service_account_info(json.loads(_sa), scopes=_SCOPES) if _sa
         else Credentials.from_service_account_file(
             r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json", scopes=_SCOPES))
sh = gspread.authorize(creds).open_by_key(SHEET)
have = {w.title: w for w in sh.worksheets()}
stamp = now_ist.strftime("%Y-%m-%d %H:%M IST")
LAST = chr(64 + len(HDR))          # 'H'

added_total = 0
for city in ("Delhi", "Mumbai", "Bharat"):
    body = rows.get(city, [])
    if city in have:
        ws = have[city]
    else:
        ws = sh.add_worksheet(title=city, rows=max(len(body) + 100, 200),
                              cols=max(len(HDR) + 8, 16))

    cur = ws.get_values("A1:%s1" % LAST)
    if not cur or [str(x).strip() for x in (list(cur[0]) + [""] * len(HDR))[:len(HDR)]] != HDR:
        ws.update(values=[HDR], range_name="A1:%s1" % LAST, value_input_option="RAW")
        ws.format("A1:%s1" % LAST,
                  {"textFormat": {"bold": True,
                                  "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                   "backgroundColor": PINK})
        ws.freeze(rows=1)

    # keys already on the tab, in their existing row order
    seen = set()
    existing = ws.get_values("A2:B")
    for r in existing:
        r = list(r) + ["", ""]
        k = (str(r[0]).strip(), str(r[1]).strip())
        if k != ("", ""):
            seen.add(k)

    new = [r for r in body if (r[0], r[1]) not in seen]
    if new:
        first = 2 + len(existing)
        last = first + len(new) - 1
        if ws.row_count < last:
            ws.add_rows(last - ws.row_count + 50)
        ws.update(values=new, range_name="A%d:%s%d" % (first, LAST, last),
                  value_input_option="RAW")
    added_total += len(new)

    ws.update_note("A1",
                   "Retrieval requests from %s onwards, %s CSPs.\n"
                   "One row per CSP per request date; a later request adds a NEW row.\n"
                   "Total Device to Return = retrieval-pending at the END of the request "
                   "date, from the NETBOX SCD2 trail.\n"
                   "Priority: P0 20+, P1 10-19, P2 5-9, P3 1-4.\n"
                   "Scope: ALL CSPs (no filter).\n"
                   "APPEND ONLY — existing rows are never rewritten and columns to the "
                   "right of %s are never touched, so you can add your own and work in them.\n"
                   "Runs once daily at 00:01 IST, covering the day that just ended.\n"
                   "Last run: %s" % (START.isoformat(), city, LAST, stamp))
    log("%-7s -> %d already there, %d appended" % (city, len(body) - len(new), len(new)))

tot = sum(len(v) for v in rows.values())
if tot != len(req):
    raise SystemExit("row split lost data: %d in tabs vs %d request rows" % (tot, len(req)))
p = collections.Counter(r[7] for v in rows.values() for r in v)
log("done — %d source rows, %d newly appended | %s"
    % (tot, added_total, "  ".join("%s:%d" % (k, p[k]) for k in ("P0", "P1", "P2", "P3"))))
