# -*- coding: utf-8 -*-
"""Every-15-min refresh of the live NETBOX_CUSTODY columns on the
'Charged & Pending Devices New' tab of the 'For Return' sheet (1VNhP2...).

For each DEVICE_ID in column C, writes that device's currently-active Fivetran
record (NETBOX_CUSTODY where _FIVETRAN_ACTIVE = TRUE — unique per device in the
SCD2 history, so the active record IS the most recent state):
    col J  CARRY_FEE_ACTIVE  -> live boolean (in-place refresh of an existing column)
    col L  LIVE_STATUS       -> live STATUS text
Touches columns J and L and nothing else. Headers are left alone except L1.
Creds from env (GitHub Actions secrets); falls back to local files for laptop runs."""
import os, json, datetime, urllib.request as U

SHEET   = "1VNhP2DNwnF73UiRoO_E-HtkNwCtETr8ZVzHcpKINBfs"
TAB_GID = 2056261339          # "Charged & Pending Devices New"
DEV_COL = "C"                 # DEVICE_ID
CFA_COL = "J"                 # CARRY_FEE_ACTIVE (existing column, header kept as-is)
OUT_COL = "L"                 # LIVE_STATUS (added 2026-08-22)
HEADER  = "LIVE_STATUS"

MB_KEY = os.environ.get("MB_KEY")  # PUBLIC repo — never hardcode; provided via MB_KEY secret
if not MB_KEY:
    raise SystemExit("MB_KEY not set — provide it via the MB_KEY env var / GitHub secret.")

# 224k device->status pairs blow past Metabase's silent ~2000-row cap as raw rows,
# so bucket them: 32 hash buckets x 9 statuses x 2 flags = ~320 rows of comma-joined
# ID lists. Raise the bucket count if the cell size ever becomes a problem.
SQL = """
SELECT MOD(ABS(HASH(DEVICE_ID)), 32) AS bucket,
       STATUS,
       CARRY_FEE_ACTIVE,
       LISTAGG(DEVICE_ID, ',') AS ids
FROM PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.NETBOX_CUSTODY
WHERE _FIVETRAN_ACTIVE = TRUE
GROUP BY 1, 2, 3
"""

MIN_EXPECTED = 150000   # sanity floor — abort rather than blank the columns
MAX_MISS_PCT = 1.0      # abort if >1% of sheet devices are missing from the warehouse
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def log(m):
    print("[%s] %s" % (datetime.datetime.now(IST).strftime("%H:%M:%S"), m), flush=True)


def mb(q):
    r = U.Request("https://metabase.wiom.in/api/dataset",
                  data=json.dumps({"database": 113, "type": "native", "native": {"query": q}}).encode(),
                  headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
    j = json.loads(U.urlopen(r, timeout=180).read().decode())
    if j.get("status") == "failed" or not j.get("data"):
        raise RuntimeError("Metabase query failed: %s" % str(j.get("error"))[:400])
    return j["data"]["rows"]


def norm(x):
    """Sheet cells sometimes carry a numeric '.0' tail; warehouse IDs never do."""
    x = (x or "").strip()
    return x[:-2] if x.endswith(".0") else x


# ---- 1) device -> (status, carry_fee_active), in one query ----
rows = mb(SQL)
if len(rows) >= 2000:
    raise SystemExit("hit the 2000-row cap (%d rows) — bucketing is no longer sufficient" % len(rows))

live = {}
for _bucket, st, cfa, ids in rows:
    if ids:
        for dev in ids.split(","):
            live[dev] = (st, cfa)
log("warehouse: %d active devices, %d distinct statuses" % (len(live), len(set(v[0] for v in live.values()))))
if len(live) < MIN_EXPECTED:
    raise SystemExit("only %d devices returned (< %d floor) — refusing to write" % (len(live), MIN_EXPECTED))

# ---- 2) sheet ----
import gspread
from google.oauth2.service_account import Credentials
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_sa = os.environ.get("GOOGLE_SA_JSON")
creds = (Credentials.from_service_account_info(json.loads(_sa), scopes=_SCOPES) if _sa
         else Credentials.from_service_account_file(r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json", scopes=_SCOPES))
ws = gspread.authorize(creds).open_by_key(SHEET).get_worksheet_by_id(TAB_GID)

devs = ws.get_values("%s2:%s" % (DEV_COL, DEV_COL))   # everything below the header
n = len(devs)
log("sheet: %d device rows" % n)
if n == 0:
    raise SystemExit(0)

status_col, cfa_col, hits, miss = [], [], 0, 0
for row in devs:
    d = norm(row[0] if row else "")
    if not d:
        status_col.append([""]); cfa_col.append([""])
        continue
    v = live.get(d)
    if v is None:
        miss += 1
        status_col.append(["NOT FOUND"]); cfa_col.append([""])
        continue
    hits += 1
    status_col.append([v[0]])
    cfa_col.append(["TRUE" if v[1] else "FALSE"])

# A sudden burst of unknown devices means the source moved, not that the sheet is wrong.
# Bail out before overwriting J with blanks.
if n and 100.0 * miss / n > MAX_MISS_PCT:
    raise SystemExit("%d/%d devices (%.1f%%) missing from the warehouse — refusing to write"
                     % (miss, n, 100.0 * miss / n))

# col L: text status. Header is ours, so write it too.
ws.update(values=[[HEADER]] + status_col,
          range_name="%s1:%s%d" % (OUT_COL, OUT_COL, n + 1),
          value_input_option="RAW")

# col J: real booleans (the column already holds bools, so USER_ENTERED to keep the
# type identical). Header row is NOT touched.
ws.update(values=cfa_col,
          range_name="%s2:%s%d" % (CFA_COL, CFA_COL, n + 1),
          value_input_option="USER_ENTERED")

# If the tab shrank since the last run, wipe the leftover tails.
if ws.row_count > n + 1:
    ws.batch_clear(["%s%d:%s%d" % (OUT_COL, n + 2, OUT_COL, ws.row_count),
                    "%s%d:%s%d" % (CFA_COL, n + 2, CFA_COL, ws.row_count)])

stamp = datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M")
ws.update_note("%s1" % OUT_COL,
               "Live NETBOX_CUSTODY STATUS (_FIVETRAN_ACTIVE = TRUE).\n"
               "Auto-refreshed ~every 15 min by mbg-datav2-cron / netbox-status.yml\n"
               "Last update: %s IST" % stamp)
ws.update_note("%s1" % CFA_COL,
               "Live NETBOX_CUSTODY CARRY_FEE_ACTIVE (_FIVETRAN_ACTIVE = TRUE).\n"
               "Auto-refreshed ~every 15 min by mbg-datav2-cron / netbox-status.yml\n"
               "Last update: %s IST" % stamp)

n_true = sum(1 for r in cfa_col if r[0] == "TRUE")
log("wrote %s2:%s%d (%d TRUE / %d FALSE) and %s1:%s%d — %d matched, %d not found"
    % (CFA_COL, CFA_COL, n + 1, n_true, hits - n_true, OUT_COL, OUT_COL, n + 1, hits, miss))
