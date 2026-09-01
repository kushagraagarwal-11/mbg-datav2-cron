# -*- coding: utf-8 -*-
"""Keep column L (RETURNED) live on the 'non consented not returned - raw' tab
of the 'For Return' sheet.

Reads DEVICE_ID from column A and writes "Returned" / "Not returned" from the
device's current NETBOX_CUSTODY record. Touches column L and nothing else.
Runs alongside the col J/L sync in netbox_status_sync.py, on the same workflow.
Creds from env (GitHub Actions secrets); falls back to local files for laptop runs.
"""
import os, json, datetime
import urllib.request as U

SHEET   = "1VNhP2DNwnF73UiRoO_E-HtkNwCtETr8ZVzHcpKINBfs"
TAB_GID = 591154732                    # "non consented not returned - raw"
DEV_COL = "A"                          # DEVICE_ID
OUT_COL = "L"                          # RETURNED
HEADER  = "RETURNED"
NETBOX  = "PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.NETBOX_CUSTODY"
IST     = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

MB_KEY = os.environ.get("MB_KEY")  # PUBLIC repo — never hardcode; provided via MB_KEY secret
if not MB_KEY:
    raise SystemExit("MB_KEY not set — provide it via the MB_KEY env var / GitHub secret.")

MIN_EXPECTED = 10000   # sanity floor for the RETURNED population (~20k today).
# NOT the ~223k all-device floor used in netbox_status_sync.py — different set.


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
        raise RuntimeError("hit the 2000-row cap (%d rows) — re-bucket" % len(rows))
    return rows


def norm(x):
    """Sheet cells sometimes carry a numeric '.0' tail; warehouse IDs never do."""
    x = str(x or "").strip()
    return x[:-2] if x.endswith(".0") else x


# ---- 1) every device that is currently RETURNED, in one bucketed query ----
returned = set()
for _b, ids in mb("""
SELECT MOD(ABS(HASH(DEVICE_ID)), 32) AS b, LISTAGG(DEVICE_ID, ',') AS ids
FROM %s WHERE _FIVETRAN_ACTIVE = TRUE AND STATUS = 'RETURNED'
GROUP BY 1""" % NETBOX):
    if ids:
        returned.update(ids.split(","))
log("warehouse: %d devices currently RETURNED" % len(returned))
if len(returned) < MIN_EXPECTED:
    raise SystemExit("only %d returned devices (< %d floor) — refusing to write"
                     % (len(returned), MIN_EXPECTED))

# ---- 2) sheet ----
import gspread
from google.oauth2.service_account import Credentials
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_sa = os.environ.get("GOOGLE_SA_JSON")
creds = (Credentials.from_service_account_info(json.loads(_sa), scopes=_SCOPES) if _sa
         else Credentials.from_service_account_file(
             r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json", scopes=_SCOPES))
ws = gspread.authorize(creds).open_by_key(SHEET).get_worksheet_by_id(TAB_GID)

devs = ws.get_values("%s2:%s" % (DEV_COL, DEV_COL))     # everything below the header
n = len(devs)
log("sheet: %d device rows" % n)
if n == 0:
    raise SystemExit(0)

out, hit = [], 0
for row in devs:
    d = norm(row[0] if row else "")
    if not d:
        out.append([""])
        continue
    if d in returned:
        hit += 1
        out.append(["Returned"])
    else:
        out.append(["Not returned"])

ws.update(values=[[HEADER]] + out,
          range_name="%s1:%s%d" % (OUT_COL, OUT_COL, n + 1),
          value_input_option="RAW")

# If the tab shrank since the last run, wipe the leftover tail.
if ws.row_count > n + 1:
    ws.batch_clear(["%s%d:%s%d" % (OUT_COL, n + 2, OUT_COL, ws.row_count)])

ws.update_note("%s1" % OUT_COL,
               "Live NETBOX_CUSTODY status (_FIVETRAN_ACTIVE = TRUE).\n"
               "Auto-refreshed by mbg-datav2-cron / netbox-status.yml\n"
               "Last update: %s IST" % datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M"))

log("wrote %s1:%s%d — %d returned, %d not returned"
    % (OUT_COL, OUT_COL, n + 1, hit, n - hit))
