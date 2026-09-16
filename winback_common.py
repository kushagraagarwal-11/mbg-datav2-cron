"""
Shared plumbing for the P1/P2 Winbacks refresh (winback_prepost.py, winback_maintab_prepost.py,
daily_dashboard.py).

PUBLIC repo -- no keys or cohort lists in code. Everything comes from env / GitHub secrets:
  MB_KEY             Metabase API key
  GOOGLE_SA_JSON     service-account JSON (wiom-sheets-writer)
  NONCOMPLIANT_B64   base64 of the 115-CSP Non_compliant list (JSON array of CSP IDs)
Locally the SA key and the list fall back to files in Desktop\\mbg\\mbg-cron.

Retries: the laptop cron failed ~1 run in 3 on connection resets to Metabase / Google, which
left the tabs stale until someone re-ran them. mb() retries transient failures with backoff;
the workflow also re-runs a script once if it still fails.
"""
import base64
import json
import os
import time
import datetime as dt

import requests
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1YiMTfSNRWzjxr1zvTyNWOYRGw80o6GuGU1_Tfyms1NE"
LOCAL = r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron"
MB_URL = "https://metabase.wiom.in/api/dataset"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Pre = last week of August (reviewer, 11-Sep).
PRE_START, PRE_END = "2026-08-25", "2026-08-31"

# 48h aging (reviewer, 12-Sep): a lead only counts once it is 48 hours old, on BOTH sides.
# Without it, Post is dragged down by leads offered in the last day or two that have not had
# time to be assigned or installed yet -- the A2I drop he flagged (51% -> 43%) was mostly that.
# Pre (25-31 Aug) is already fully aged; the filter is applied to it anyway so both sides
# run through the same rule.
AGING_HOURS = 48

# One row per (CSP, connection): the lead, whether it was ever assigned, ever installed.
# The HAVING clause is the 48h aging -- the connection's FIRST offer to this CSP must be at
# least AGING_HOURS old.
CONN = """
      SELECT f.CSP_ID, f.CONNECTION_ID,
             MAX(IFF(f.EXECUTOR_ID IS NOT NULL,1,0)) AS assigned,
             MAX(IFF(f.OTP_VERIFIED_FLAG OR f.INSTALLATION_COMPLETED_AT IS NOT NULL
                     OR f.COMPLETED_STEP>=7,1,0)) AS installed
      FROM PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES f
"""
AGED = "HAVING MIN(f.CREATED_AT) <= DATEADD(hour, -%d, CURRENT_TIMESTAMP())" % AGING_HOURS


def _mb_key():
    k = os.environ.get("MB_KEY")
    if not k:
        raise SystemExit("MB_KEY not set -- provide it via env / GitHub secret.")
    return k


def mb(sql, attempts=4):
    """Metabase native query with retry on transient network / 5xx failures.
    A SQL error is not retried -- it will fail the same way every time."""
    key = _mb_key()
    last = None
    for i in range(attempts):
        try:
            r = requests.post(MB_URL,
                              headers={"x-api-key": key, "Content-Type": "application/json"},
                              json={"database": 113, "type": "native", "native": {"query": sql}},
                              timeout=180)
            if r.status_code >= 500:
                raise requests.HTTPError("Metabase HTTP %d" % r.status_code)
            d = r.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError, ValueError) as e:
            last = e
            if i < attempts - 1:
                time.sleep(10 * (i + 1) ** 2)            # 10s, 40s, 90s
            continue
        if isinstance(d, dict) and d.get("error"):
            raise RuntimeError("Metabase: %s" % str(d["error"])[:300])
        return d["data"]["rows"]
    raise RuntimeError("Metabase unreachable after %d attempts: %s" % (attempts, last))


def gclient(extra_scopes=()):
    scopes = ["https://www.googleapis.com/auth/spreadsheets"] + list(extra_scopes)
    sa = os.environ.get("GOOGLE_SA_JSON")
    if sa:
        cr = Credentials.from_service_account_info(json.loads(sa), scopes=scopes)
    else:
        cr = Credentials.from_service_account_file(LOCAL + r"\wiom-sheets-writer.json", scopes=scopes)
    gc = gspread.authorize(cr)
    gc.set_timeout(120)
    return gc


def load_noncompliant():
    """The reviewer-supplied Non_compliant cohort. Refuses to return an empty list: a missing
    list would otherwise silently write zeros into the dashboard."""
    b64 = os.environ.get("NONCOMPLIANT_B64")
    if b64:
        ids = json.loads(base64.b64decode(b64).decode("utf-8"))
    else:
        with open(LOCAL + r"\noncompliant.json", encoding="utf-8") as fh:
            ids = json.load(fh)
    if not ids:
        raise SystemExit("Non_compliant list is empty -- refusing to write zeros.")
    return set(ids)


REFRESH_EVERY = "every 20 min"
STAMP_PREFIX = "Last synced:"


def stamp(ws, what="", cell="B1"):
    """Write 'Last synced: <IST time> · refreshes every 20 min' into a header cell of `ws`.
    Called at the END of a successful run, so the time shown is when this tab's data was last
    written. Only writes into an empty cell or one holding an earlier stamp -- never over
    anything a reviewer typed there."""
    cur = (ws.get_values(cell) or [[""]])[0]
    cur = cur[0].strip() if cur else ""
    if cur and not cur.startswith(STAMP_PREFIX):
        print("  stamp skipped: %s!%s holds %r" % (ws.title, cell, cur[:40]))
        return
    txt = "%s %s  ·  refreshes automatically %s%s" % (
        STAMP_PREFIX, dt.datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST"), REFRESH_EVERY,
        ("  ·  " + what) if what else "")
    ws.update(values=[[txt]], range_name=cell, value_input_option="RAW")
    ws.format(cell, {"textFormat": {"italic": True, "bold": False, "fontSize": 9,
                                    "foregroundColor": {"red": 0.2, "green": 0.45, "blue": 0.2}},
                     "wrapStrategy": "OVERFLOW_CELL", "horizontalAlignment": "LEFT"})
