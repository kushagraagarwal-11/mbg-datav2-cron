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

Scope = every CSP EXCEPT the 727 consented ones in EXCLUDE below.
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

EXCLUDE = [
    "a0a6y4", "a0a6y5", "a0a6y6", "a0a6y9", "a0a6z0", "a0a6z1", "a0a6z2", "a0a6z3", "a0a6z4",
    "a0a6z7", "a0a6z8", "a0a7a0", "a0a7a2", "a0a7a3", "a0a7a4", "a0a7a5", "a0a7a6", "a0a7a7",
    "a0a7a9", "a0a7b0", "a0a7b1", "a0a7b2", "a0a7b3", "a0a7b4", "a0a7b5", "a0a7b6", "a0a7b7",
    "a0a7b8", "a0a7b9", "a0a7c0", "a0a7c1", "a0a7c2", "a0a7c3", "a0a7c4", "a0a7c5", "a0a7c6",
    "a0a7c7", "a0a7d2", "a0a7d3", "a0a7d4", "a0a7d5", "a0a7d6", "a0a7d8", "a0a7d9", "a0a7e3",
    "a0a7e4", "a0a7e7", "a0a7e9", "a0a7f4", "a0a7f5", "a0a7f6", "a0a7f7", "a0a7f8", "a0a7g0",
    "a0a7g1", "a0a7g2", "a0a7g3", "a0a7g5", "a0a7g6", "a0a7g7", "a0a7g8", "a0a7g9", "a0a7h0",
    "a0a7h1", "a0a7h2", "a0a8a5", "a0a8c2", "a0a9d4", "a0a9j4", "a0a9m4", "a0a9o4", "a0a9s3",
    "a0a9w2", "a0b0a0", "a0b0g5", "a0b0n9", "a0b0w0", "a0b1b5", "a0b1s1", "a0b1u5", "a0b2w1",
    "a0b3l5", "a0b3m4", "a0b5l8", "a0b5l9", "a0b5m0", "a0b5m1", "a0b5m3", "a0b5m5", "a0b5m7",
    "a0b5m8", "a0b5m9", "a0b5n0", "a0b5n2", "a0b5n3", "a0b5n4", "a0b5n6", "a0b5n7", "a0b5n8",
    "a0b5o0", "a0b5o2", "a0b5o3", "a0b5o4", "a0b5o5", "a0b5o6", "a0b5o8", "a0b5o9", "a0b5p2",
    "a0b5p3", "a0b5p5", "a0b5p6", "a0b5p7", "a0b5q2", "a0b5q4", "a0b5q5", "a0b5q7", "a0b5q8",
    "a0b5q9", "a0b5r2", "a0b5r3", "a0b5r4", "a0b5r7", "a0b5r8", "a0b5r9", "a0b5s0", "a0b5s1",
    "a0b5s2", "a0b5s4", "a0b5s6", "a0b5s7", "a0b5t0", "a0b5t1", "a0b5t2", "a0b5t3", "a0b5t5",
    "a0b5t9", "a0b5u1", "a0b5u4", "a0b5u5", "a0b5u7", "a0b5u8", "a0b5u9", "a0b5v0", "a0b5v2",
    "a0b5v3", "a0b5v4", "a0b5v5", "a0b5v6", "a0b5v7", "a0b5v8", "a0b5v9", "a0b5w1", "a0b5w2",
    "a0b5w4", "a0b5w5", "a0b5w7", "a0b5w8", "a0b5w9", "a0b5x0", "a0b5x1", "a0b5x2", "a0b5x3",
    "a0b5x5", "a0b5x7", "a0b5x8", "a0b5x9", "a0b5y0", "a0b5y3", "a0b5y5", "a0b5y6", "a0b5y7",
    "a0b5y9", "a0b5z0", "a0b5z3", "a0b5z6", "a0b5z7", "a0b6a1", "a0b6a2", "a0b6a3", "a0b6a6",
    "a0b6a8", "a0b6a9", "a0b6b0", "a0b6b1", "a0b6b2", "a0b6b3", "a0b6b4", "a0b6b5", "a0b6b6",
    "a0b6b8", "a0b6b9", "a0b6c0", "a0b6c1", "a0b6c2", "a0b6c4", "a0b6c5", "a0b6c6", "a0b6c9",
    "a0b6d0", "a0b6d1", "a0b6d2", "a0b6d3", "a0b6d4", "a0b6d8", "a0b6d9", "a0b6e0", "a0b6e1",
    "a0b6e2", "a0b6e3", "a0b6e4", "a0b6e6", "a0b6e7", "a0b6e8", "a0b6e9", "a0b6f0", "a0b6f1",
    "a0b6f2", "a0b6f3", "a0b6f5", "a0b6f6", "a0b6f8", "a0b6f9", "a0b6g1", "a0b6g2", "a0b6g3",
    "a0b6g6", "a0b6g7", "a0b6g9", "a0b6h1", "a0b6h2", "a0b6h3", "a0b6h4", "a0b6h5", "a0b6h6",
    "a0b6h7", "a0b6h8", "a0b6h9", "a0b6i0", "a0b6i1", "a0b6i2", "a0b6i4", "a0b6i6", "a0b6i7",
    "a0b6i8", "a0b6j2", "a0b6j3", "a0b6j6", "a0b6j7", "a0b6j8", "a0b6j9", "a0b6k0", "a0b6k5",
    "a0b6k6", "a0b6k8", "a0b6k9", "a0b6l0", "a0b6l2", "a0b6l3", "a0b6l6", "a0b6l7", "a0b6m0",
    "a0b6m3", "a0b6m4", "a0b6m6", "a0b6m9", "a0b6n2", "a0b6n3", "a0b6n7", "a0b6n8", "a0b6o0",
    "a0b6o1", "a0b6o2", "a0b6o4", "a0b6o5", "a0b6o7", "a0b6o9", "a0b6p0", "a0b6p1", "a0b6p2",
    "a0b6p3", "a0b6p4", "a0b6p5", "a0b6p7", "a0b6p8", "a0b6p9", "a0b6q1", "a0b6q2", "a0b6q3",
    "a0b6q4", "a0b6q5", "a0b6q6", "a0b6q7", "a0b6q9", "a0b6r0", "a0b6r2", "a0b6r4", "a0b6r5",
    "a0b6r6", "a0b6r8", "a0b6s4", "a0b6s6", "a0b6s7", "a0b6s8", "a0b6t0", "a0b6t1", "a0b6t2",
    "a0b6t3", "a0b6t4", "a0b6t5", "a0b6t7", "a0b6t8", "a0b6t9", "a0b6u0", "a0b6u2", "a0b6u3",
    "a0b6u4", "a0b6u8", "a0b6v0", "a0b6v1", "a0b6v3", "a0b6v4", "a0b6v5", "a0b6v6", "a0b6v7",
    "a0b6v8", "a0b6v9", "a0b6w0", "a0b6w1", "a0b6w2", "a0b6w3", "a0b6w4", "a0b6w5", "a0b6w7",
    "a0b6w8", "a0b6x1", "a0b6x2", "a0b6x3", "a0b6x4", "a0b6x5", "a0b6x6", "a0b6x7", "a0b6x8",
    "a0b6x9", "a0b6y1", "a0b6y2", "a0b6y5", "a0b6y6", "a0b6y7", "a0b6y8", "a0b6z1", "a0b6z2",
    "a0b6z3", "a0b6z4", "a0b6z5", "a0b6z7", "a0b6z8", "a0b6z9", "a0b7a1", "a0b7a2", "a0b7a4",
    "a0b7a6", "a0b7a7", "a0b7a8", "a0b7a9", "a0b7b1", "a0b7b3", "a0b7b4", "a0b7b5", "a0b7b7",
    "a0b7b9", "a0b7c0", "a0b7c1", "a0b7c2", "a0b7c3", "a0b7c4", "a0b7c5", "a0b7c6", "a0b7c7",
    "a0b7d0", "a0b7d1", "a0b7d3", "a0b7d7", "a0b7d8", "a0b7e1", "a0b7e2", "a0b7e3", "a0b7e4",
    "a0b7e5", "a0b7e6", "a0b7e7", "a0b7f0", "a0b7f1", "a0b7f2", "a0b7f3", "a0b7f4", "a0b7f6",
    "a0b7f9", "a0b7g1", "a0b7g2", "a0b7g4", "a0b7g6", "a0b7g7", "a0b7g9", "a0b7h2", "a0b7h4",
    "a0b7h5", "a0b7h7", "a0b7h8", "a0b7h9", "a0b7i0", "a0b7i2", "a0b7i3", "a0b7i4", "a0b7i5",
    "a0b7i6", "a0b7i7", "a0b7j1", "a0b7j2", "a0b7j3", "a0b7j4", "a0b7j5", "a0b8o1", "a0b8o2",
    "a0b8o3", "a0b8o4", "a0b8o5", "a0b8o6", "a0b8o7", "a0b8o8", "a0b8p0", "a0b8p2", "a0b8p6",
    "a0b8p8", "a0b8q1", "a0b8q2", "a0b8q4", "a0b8q5", "a0b8q9", "a0b8r0", "a0b8r1", "a0b8r2",
    "a0b8r3", "a0b8r5", "a0b8r9", "a0b8s0", "a0b8s1", "a0b8s2", "a0b8s3", "a0b8s4", "a0b8s5",
    "a0b8s7", "a0b8s8", "a0b8t5", "a0b8t6", "a0b8t9", "a0b8u0", "a0b8u1", "a0b8u2", "a0b8u4",
    "a0b8u5", "a0b8u6", "a0b8u9", "a0b8v0", "a0b8v3", "a0b8v4", "a0b8v5", "a0b8v7", "a0b8v8",
    "a0b8v9", "a0b8w1", "a0b8w2", "a0b8w3", "a0b8w5", "a0b8w6", "a0b8w7", "a0b8w8", "a0b8w9",
    "a0b8x0", "a0b8x1", "a0b8x2", "a0b8x3", "a0b8x4", "a0b8x6", "a0b8x7", "a0b8y0", "a0b8y3",
    "a0b8y4", "a0b8y6", "a0b8y7", "a0b8y8", "a0b8y9", "a0b8z0", "a0b8z1", "a0b8z2", "a0b8z3",
    "a0b8z4", "a0b8z5", "a0b8z7", "a0b8z8", "a0b8z9", "a0b9a0", "a0b9a2", "a0b9a4", "a0b9a8",
    "a0b9a9", "a0b9b1", "a0b9b4", "a0b9b5", "a0b9b6", "a0b9b7", "a0b9b9", "a0b9c0", "a0b9c2",
    "a0b9c3", "a0b9c4", "a0b9c5", "a0b9c8", "a0b9c9", "a0b9d1", "a0b9d2", "a0b9d3", "a0b9d5",
    "a0b9d6", "a0b9d9", "a0b9e3", "a0b9e5", "a0b9e8", "a0b9e9", "a0b9f1", "a0b9f3", "a0b9f4",
    "a0b9f6", "a0b9f8", "a0b9g1", "a0b9g3", "a0b9g4", "a0b9g6", "a0b9g7", "a0b9g8", "a0b9h1",
    "a0b9h2", "a0b9h3", "a0b9h4", "a0b9h5", "a0b9h6", "a0b9h7", "a0b9h8", "a0b9h9", "a0b9i0",
    "a0b9i1", "a0b9i3", "a0b9i4", "a0b9i5", "a0b9i8", "a0b9i9", "a0b9j0", "a0b9j1", "a0b9j2",
    "a0b9j3", "a0b9j4", "a0b9j5", "a0b9j6", "a0b9j7", "a0b9k0", "a0b9k4", "a0b9k6", "a0b9k7",
    "a0b9k9", "a0b9l1", "a0b9l2", "a0b9l3", "a0b9l5", "a0b9l6", "a0b9l7", "a0b9l8", "a0b9m0",
    "a0b9m1", "a0b9m2", "a0b9m3", "a0b9m4", "a0b9m6", "a0b9m7", "a0b9m8", "a0b9n1", "a0b9n2",
    "a0b9n3", "a0b9n5", "a0b9n6", "a0b9n7", "a0b9n8", "a0b9o0", "a0b9o1", "a0b9o3", "a0b9o5",
    "a0b9p0", "a0b9p1", "a0b9p2", "a0b9p4", "a0b9p5", "a0b9p6", "a0b9p9", "a0b9q0", "a0b9q3",
    "a0b9q7", "a0b9q9", "a0b9r0", "a0b9r3", "a0b9r4", "a0b9r5", "a0b9r6", "a0b9r8", "a0b9r9",
    "a0b9s2", "a0b9s3", "a0b9s4", "a0b9s5", "a0b9s6", "a0b9s8", "a0b9t0", "a0b9t1", "a0b9t2",
    "a0b9t3", "a0b9t4", "a0b9t7", "a0b9t8", "a0b9u0", "a0b9u2", "a0b9u5", "a0b9u6", "a0b9u7",
    "a0b9u8", "a0b9u9", "a0b9v0", "a0b9v4", "a0b9v6", "a0b9v7", "a0b9v8", "a0b9v9", "a0b9w3",
    "a0b9w5", "a0b9w7", "a0b9w8", "a0b9x0", "a0b9x1", "a0b9x3", "a0b9x4", "a0b9x5", "a0b9x6",
    "a0b9x7", "a0b9x8", "a0b9x9", "a0b9y0", "a0b9y1", "a0b9y2", "a0b9y3", "a0b9y4", "a0b9y5",
    "a0b9y6", "a0b9y8", "a0b9y9", "a0b9z2", "a0b9z3", "a0b9z4", "a0b9z6", "a0b9z7", "a0b9z8",
    "a0b9z9", "a0c0a0", "a0c0a1", "a0c0a2", "a0c0a4", "a0c0a5", "a0c0a6", "a0c0a9", "a0c0b0",
    "a0c0b1", "a0c0b4", "a0c0b5", "a0c0b8", "a0c0c0", "a0c0c1", "a0c0c3", "a0c0c6", "a0c0c7",
    "a0c0c8", "a0c0c9", "a0c0d1", "a0c0d2", "a0c0d6", "a0c0d8", "a0c0d9", "a0c0e0", "a0c0e1",
    "a0c0e2", "a0c0e3", "a0c0e4", "a0c0e5", "a0c0e6", "a0c0e7", "a0c0e9", "a0c0f0", "a0c0f1",
    "a0c0f2", "a0c0f3", "a0c0f4", "a0c0f6", "a0c0f7", "a0c0f8", "a0c0g0", "a0c0g1", "a0c0g3",
    "a0c0g4", "a0c0g7", "a0c0g8", "a0c0g9", "a0c0h1", "a0c0h2", "a0c0h3", "a0c0h6", "a0c0h7",
    "a0c0h8", "a0c0i1", "a0c0i3", "a0c0i4", "a0c0i5", "a0c0j0", "a0c0j2"
]


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


EXC = ",".join("'%s'" % x for x in sorted(EXCLUDE))


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

# ---- 1) who requested a retrieval on which day, and how many devices ----
req = mb("""
SELECT req_date, CSP_ID, COUNT(DISTINCT DEVICE_ID) FROM (
  SELECT DEVICE_ID, CSP_ID,
         TO_VARCHAR(DATE(CONVERT_TIMEZONE('Asia/Kolkata', RETRIEVAL_START_DATE)),
                    'YYYY-MM-DD') AS req_date
  FROM %s
  WHERE RETRIEVAL_START_DATE IS NOT NULL
    AND DATE(CONVERT_TIMEZONE('Asia/Kolkata', RETRIEVAL_START_DATE)) >= '%s'
    AND CSP_ID IS NOT NULL AND CSP_ID NOT IN (%s)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY DEVICE_ID, req_date
                             ORDER BY _FIVETRAN_START) = 1)
GROUP BY 1, 2""" % (NB, START.isoformat(), EXC))
log("request rows (csp x date): %d" % len(req))

dates = sorted({r[0] for r in req})
csps = sorted({r[1] for r in req})
if not csps:
    log("no retrieval requests in scope since %s — nothing to write" % START)
    raise SystemExit(0)

# ---- 2) retrieval-pending total per CSP, as at the END of each request date ----
# Past dates come from the SCD2 trail so they reproduce exactly on every run;
# the current date is read live, so today's row moves through the day.
totals = {}
for d in dates:
    if d == today.isoformat():
        where = "_FIVETRAN_ACTIVE = TRUE"
    else:
        where = ("_FIVETRAN_START <= '%s 23:59:59' AND _FIVETRAN_END > '%s 23:59:59'"
                 % (d, d))
    for csp, n in mb("""SELECT CSP_ID, COUNT(DISTINCT DEVICE_ID) FROM %s
WHERE %s AND STATUS = 'RETRIEVAL_PENDING'
  AND CSP_ID IS NOT NULL AND CSP_ID NOT IN (%s)
GROUP BY 1""" % (NB, where, EXC)):
        totals[(d, csp)] = n
    log("  %s -> totals for %d CSPs%s" % (d, sum(1 for k in totals if k[0] == d),
                                          "  (live)" if d == today.isoformat() else ""))

# ---- 3) CSP attributes ----
lit = ",".join("'%s'" % c for c in csps)
acct = {x[0]: x for x in mb("""SELECT CSP_ID, PARTNER_ID, NAME, MOBILE_NUMBER
FROM %s WHERE _FIVETRAN_ACTIVE = TRUE AND CSP_ID IN (%s)""" % (ACCT, lit))}
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
                   "Scope excludes the 727 consented CSPs.\n"
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
