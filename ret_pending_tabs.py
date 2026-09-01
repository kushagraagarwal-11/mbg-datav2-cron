# -*- coding: utf-8 -*-
"""Rebuild the two 'Ret Pending' tabs on the 'For Return' sheet from live data.

Scope is a fixed list of non-consented CSPs (CSPS below). For each, every device
currently sitting at STATUS='RETRIEVAL_PENDING' in NETBOX_CUSTODY.

  Ret Pending - CSP summary   one row per CSP in scope (zeros included, sorted last)
  Ret Pending - Device level  one row per pending device

Name/phone come from CSP_ACCOUNT; city/address from PARTNER_JANAM_KUNDLI
(CSP_ACCOUNT has PINCODE empty and ADDRESS for only 18 of the 344).
Both tabs are fully rewritten each run, so a skipped tick costs nothing.
Creds from env (GitHub Actions secrets); local files as a laptop fallback.
"""
import os, json, collections, datetime
import urllib.request as U

SHEET   = "1VNhP2DNwnF73UiRoO_E-HtkNwCtETr8ZVzHcpKINBfs"
SUM_TAB = "Ret Pending - CSP summary"
DEV_TAB = "Ret Pending - Device level"
NETBOX  = "PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.NETBOX_CUSTODY"
ACCT    = "PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_ACCOUNT"
JK      = "PROD_DB.PUBLIC.PARTNER_JANAM_KUNDLI"
IST     = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
PINK    = {"red": 0.851, "green": 0.0, "blue": 0.553}

MB_KEY = os.environ.get("MB_KEY")  # PUBLIC repo — never hardcode
if not MB_KEY:
    raise SystemExit("MB_KEY not set — provide it via the MB_KEY env var / GitHub secret.")

CSPS = [
    "a0a7d1", "a0b8p3", "a0b9e1", "a0a7a1", "a0a7e0", "a0a7f3", "a0c0j1", "a0b6k3", "a0b9a6",
    "a0a7a8", "a0a7e6", "a0a7g4", "a0b5q3", "a0b7h1", "a0b8o9", "a0b8z6", "a0c0a7", "a0c0c2",
    "a0b5u6", "a0b5z4", "a0b6z0", "a0b8r7", "a0b9e4", "a0c0d0", "a0b9k1", "a0b6u5", "a0b6k4",
    "a0b9a1", "a0b6o8", "a0b5z1", "a0b7h0", "a0b6y3", "a0b5u0", "a0b6g0", "a0b9j9", "a0a7e5",
    "a0b9v1", "a0b9w6", "a0b8q3", "a0b8t0", "a0c0a3", "a0a6y7", "a0a7e1", "a0a7f0", "a0a9m3",
    "a0b3k4", "a0b5y2", "a0b6l4", "a0b6l5", "a0b6s3", "a0b7a0", "a0b7e9", "a0b8u7", "a0b9g0",
    "a0b9g2", "a0b9g5", "a0b9q1", "a0c0i6", "a0b5t7", "a0b6h0", "a0b6n4", "a0c0j3", "a0b8r8",
    "a0b7d4", "a0b8q6", "a0b8w0", "a0b9l0", "a0b9w1", "a0b5n5", "a0b6r1", "a0b5x4", "a0b2v8",
    "a0b5m4", "a0b5s8", "a0b5x6", "a0b6d6", "a0b6j4", "a0b7f7", "a0b9z1", "a0c0a8", "a0b6a5",
    "a0b5p1", "a0b5p4", "a0b5r5", "a0b5t4", "a0b5v1", "a0b6j1", "a0b6n9", "a0b6p6", "a0b7c9",
    "a0b8q8", "a0b8t8", "a0b8v1", "a0b8x9", "a0b9f0", "a0b9o9", "a0b9r7", "a0b9s9", "a0b9v2",
    "a0c0b7", "a0c0d3", "a0c0d5", "a0c0e8", "a0b5l7", "a0b6r7", "a0b6s2", "a0b9c7", "a0b5q6",
    "a0b6f7", "a0b8r4", "a0b9u3", "a0b6u9", "a0b5y1", "a0b9q4", "a0c0b6", "a0b9v5", "a0b5w3",
    "a0b5q0", "a0b9q5", "a0b8v6", "a0b5q1", "a0c0h0", "a0c0i9", "a0b9p7", "a0b6a0", "a0b6c7",
    "a0b6j0", "a0b6s9", "a0b6u1", "a0b6w9", "a0b6y9", "a0b7e8", "a0b7g0", "a0b8s6", "a0b8t7",
    "a0b9i6", "a0b9s0", "a0b9t6", "a0b9z0", "a0c0h5", "a0c0i2", "a0b7i9", "a0b5z9", "a0b6i3",
    "a0b7b0", "a0b9a3", "a0b9e6", "a0b9c1", "a0b7a3", "a0b9o4", "a0b5p0", "a0c0g5", "a0b6u7",
    "a0b8y2", "a0c0b9", "a0b7g5", "a0b5m6", "a0b9f2", "a0b5o1", "a0b9t5", "a0b9z5", "a0b7d5",
    "a0b5r6", "a0b5u2", "a0b5w6", "a0b6m2", "a0b6q8", "a0b6r3", "a0b8p9", "a0b9e2", "a0b9e7",
    "a0b9r2", "a0c0f9", "a0c0h4", "a0b6k7", "a0b5t6", "a0b7c8", "a0b9r1", "a0b9y7", "a0c0f5",
    "a0c0g2", "a0c0g6", "a0b6c3", "a0b6k2", "a0b9f7", "a0b5r0", "a0b7g3", "a0b7j6", "a0b8v2",
    "a0b6v2", "a0b6s5", "a0b8o0", "a0c0i7", "a0c0i8", "a0b7b6", "a0b7d6", "a0b6l9", "a0b8t3",
    "a0b5z5", "a0b8r6", "a0b8y5", "a0b5r1", "a0b6k1", "a0b7i1", "a0b5y8", "a0b8p5", "a0b9d7",
    "a0b9i2", "a0b7h3", "a0b3v1", "a0b5p9", "a0b5s9", "a0b9k3", "a0b5z2", "a0b5s3", "a0b8p7",
    "a0b9d8", "a0b9w0", "a0b9s7", "a0b9k2", "a0b6e5", "a0b8t2", "a0b7d9", "a0b6f4", "a0c0c5",
    "a0b7f8", "a0b8p1", "a0c0d7", "a0b6g4", "a0b5u3", "a0b6t6", "a0b9p8", "a0b8n9", "a0c0b2",
    "a0b7e0", "a0b8p4", "a0b9c6", "a0b5p8", "a0b8w4", "a0b6a4", "a0b6m8", "a0b6b7", "a0c0h9",
    "a0b8u3", "a0b9o2", "a0b5y4", "a0b6n1", "a0b6u6", "a0b6n5", "a0b9a5", "a0b6a7", "a0b9l4",
    "a0b9m5", "a0b6i9", "a0b5n1", "a0b6r9", "a0b5s5", "a0b9s1", "a0b9b8", "a0c0c4", "a0b8s9",
    "a0b6l1", "a0b6w6", "a0b6y0", "a0b9k8", "a0b8x8", "a0b7i8", "a0a9a9", "a0b9n9", "a0b6m7",
    "a0b8q0", "a0b7j0", "a0b9u1", "a0b9d0", "a0b6z6", "a0b6j5", "a0b1g8", "a0b6l8", "a0b9j8",
    "a0b9m9", "a0b5o7", "a0b5m2", "a0b6g5", "a0b6o3", "a0b6y4", "a0b9b0", "a0b9i7", "a0b9w9",
    "a0b2o2", "a0b5n9", "a0b6m1", "a0b6n6", "a0b6o6", "a0b7f5", "a0b8x5", "a0b9a7", "a0b9e0",
    "a0b9f5", "a0b9k5", "a0b9o8", "a0b9q8", "a0b9u4", "a0b9x2", "a0c0i0", "a0b5w0", "a0b5z8",
    "a0b6c8", "a0b6g8", "a0b6m5", "a0b6n0", "a0b7b2", "a0b7b8", "a0b7d2", "a0b7g8", "a0b7h6",
    "a0b8q7", "a0b8u8", "a0b9b2", "a0b9b3", "a0b9d4", "a0b9g9", "a0b9h0", "a0b9n0", "a0b9n4",
    "a0b9o6", "a0b9o7", "a0b9p3", "a0b9q2", "a0b9w4", "a0b8t4", "a0b6s1", "a0b5t8", "a0b6d5",
    "a0b6d7", "a0b6i5", "a0b6s0", "a0b6x0", "a0b7a5", "a0b8t1", "a0b8y1", "a0b9f9", "a0b9q6",
    "a0c0b3", "a0c0d4"
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
    return j["data"]["rows"]


def lit(ids):
    return ",".join("'%s'" % i for i in ids)


def pull_devices(ids):
    """Chunked so no single response can brush the ~2000-row API cap.
    A chunk that comes back suspiciously large is split and retried."""
    out, queue = [], [ids[i:i + 60] for i in range(0, len(ids), 60)]
    while queue:
        chunk = queue.pop()
        rows = mb("""
SELECT DEVICE_ID, DEVICE_TYPE, CSP_ID, STATUS,
       TO_VARCHAR(RETRIEVAL_START_DATE,'YYYY-MM-DD'),
       DATEDIFF('day', RETRIEVAL_START_DATE, CURRENT_DATE()),
       CARRY_FEE_ACTIVE, CARRY_FEE_STATE, CARRY_FEE_ACCRUAL_UNITS,
       HOLDER_TYPE, RETRIEVAL_REASON, RETRIEVAL_INITIATOR,
       TO_VARCHAR(RECOVERY_PENDING_SINCE,'YYYY-MM-DD')
FROM %s
WHERE _FIVETRAN_ACTIVE = TRUE AND STATUS = 'RETRIEVAL_PENDING'
  AND CSP_ID IN (%s)""" % (NETBOX, lit(chunk)))
        if len(rows) >= 1900 and len(chunk) > 1:      # too close to the cap — split
            mid = len(chunk) // 2
            queue += [chunk[:mid], chunk[mid:]]
            continue
        if len(rows) >= 1900:
            raise RuntimeError("single CSP %s returned %d rows — cannot split further"
                               % (chunk[0], len(rows)))
        out += rows
    return out


def bucket(c):
    c = (c or "").strip().lower()
    return "Delhi" if c == "delhi" else "Mumbai" if c == "mumbai" else "Bharat"


# ---------------------------------------------------------------- pull
acct = {x[0]: x for x in mb("""SELECT CSP_ID, PARTNER_ID, NAME, MOBILE_NUMBER
FROM %s WHERE _FIVETRAN_ACTIVE = TRUE AND CSP_ID IN (%s)""" % (ACCT, lit(CSPS)))}
jk = {x[0]: x for x in mb("""SELECT CSP_ID, ANY_VALUE(PARTNER_NAME), ANY_VALUE(CITY), ANY_VALUE(ADDRESS)
FROM %s WHERE CSP_ID IN (%s) GROUP BY 1""" % (JK, lit(CSPS)))}
log("CSP_ACCOUNT %d/%d, JANAM_KUNDLI %d/%d" % (len(acct), len(CSPS), len(jk), len(CSPS)))
if not acct or not jk:
    raise SystemExit("reference pull came back empty — refusing to overwrite the tabs")

devs = pull_devices(CSPS)
log("retrieval-pending devices: %d (unique %d)" % (len(devs), len(set(d[0] for d in devs))))

cnt   = collections.Counter(d[2] for d in devs)
cfcnt = collections.Counter(d[2] for d in devs if d[6] is True)

# ---------------------------------------------------------------- shape
SUM_HDR = ["CSP_ID", "PARTNER_ID", "CSP_NAME", "CITY", "CITY_BUCKET", "ADDRESS",
           "PHONE", "RET_PENDING_DEVICES", "OF_WHICH_CARRY_FEE_ACTIVE"]
srows = []
for c in CSPS:
    a = acct.get(c, [c, "", "", ""])
    j = jk.get(c, [c, "", "", ""])
    srows.append([c, str(a[1] or ""), a[2] or j[1] or "", j[2] or "", bucket(j[2]),
                  j[3] or "", str(a[3] or ""), cnt.get(c, 0), cfcnt.get(c, 0)])
srows.sort(key=lambda r: (-r[7], r[3], r[2]))

DEV_HDR = ["DEVICE_ID", "DEVICE_TYPE", "CSP_ID", "CSP_NAME", "CITY", "CITY_BUCKET",
           "PHONE", "STATUS", "RETRIEVAL_START_DATE", "DAYS_PENDING",
           "CARRY_FEE_ACTIVE", "CARRY_FEE_STATE", "CARRY_FEE_ACCRUAL_UNITS",
           "HOLDER_TYPE", "RETRIEVAL_REASON", "RETRIEVAL_INITIATOR", "RECOVERY_PENDING_SINCE"]
drows = []
for d in devs:
    a = acct.get(d[2], [d[2], "", "", ""])
    j = jk.get(d[2], [d[2], "", "", ""])
    drows.append([d[0], d[1] or "", d[2], a[2] or j[1] or "", j[2] or "", bucket(j[2]),
                  str(a[3] or ""), d[3] or "", d[4] or "",
                  d[5] if d[5] is not None else "",
                  "TRUE" if d[6] is True else "FALSE", d[7] or "",
                  d[8] if d[8] is not None else "", d[9] or "", d[10] or "",
                  d[11] or "", d[12] or ""])
drows.sort(key=lambda r: (r[5], r[3], r[0]))

if sum(r[7] for r in srows) != len(drows):
    raise SystemExit("summary counts (%d) != device rows (%d) — refusing to write"
                     % (sum(r[7] for r in srows), len(drows)))

# ---------------------------------------------------------------- write
import gspread
from google.oauth2.service_account import Credentials
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_sa = os.environ.get("GOOGLE_SA_JSON")
creds = (Credentials.from_service_account_info(json.loads(_sa), scopes=_SCOPES) if _sa
         else Credentials.from_service_account_file(
             r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json", scopes=_SCOPES))
sh = gspread.authorize(creds).open_by_key(SHEET)
have = {w.title: w for w in sh.worksheets()}
stamp = datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")


def put(title, hdr, rows, note):
    if title in have:
        ws = have[title]
        ws.clear()
        ws.resize(rows=max(len(rows) + 10, 50), cols=len(hdr))
    else:
        ws = sh.add_worksheet(title=title, rows=max(len(rows) + 10, 50), cols=len(hdr))
    ws.update(values=[hdr] + rows, range_name="A1", value_input_option="RAW")
    ws.format("A1:%s1" % chr(64 + len(hdr)),
              {"textFormat": {"bold": True,
                              "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
               "backgroundColor": PINK})
    ws.freeze(rows=1)
    ws.update_note("A1", note)
    log("%s -> %d rows" % (title, len(rows)))


put(SUM_TAB, SUM_HDR, srows,
    "Retrieval-pending devices for the %d non-consented CSPs in scope.\n"
    "NETBOX_CUSTODY (_FIVETRAN_ACTIVE=TRUE, STATUS='RETRIEVAL_PENDING').\n"
    "Name/phone: CSP_ACCOUNT. City/address: PARTNER_JANAM_KUNDLI.\n"
    "All %d CSPs listed; zero-pending sort to the bottom.\n"
    "Auto-refreshed by mbg-datav2-cron / netbox-status.yml\n"
    "Last update: %s" % (len(CSPS), len(CSPS), stamp))

put(DEV_TAB, DEV_HDR, drows,
    "One row per retrieval-pending device (%d) across the %d CSPs in scope.\n"
    "NETBOX_CUSTODY (_FIVETRAN_ACTIVE=TRUE, STATUS='RETRIEVAL_PENDING').\n"
    "Auto-refreshed by mbg-datav2-cron / netbox-status.yml\n"
    "Last update: %s" % (len(drows), len(CSPS), stamp))

b = collections.Counter(r[5] for r in drows)
log("done — %d devices | Delhi %d / Mumbai %d / Bharat %d | CSPs with >=1 pending: %d"
    % (len(drows), b["Delhi"], b["Mumbai"], b["Bharat"], sum(1 for r in srows if r[7] > 0)))
