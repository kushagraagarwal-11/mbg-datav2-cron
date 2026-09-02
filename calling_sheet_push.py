"""
Pushes yesterday's Kapture call data to the 'Calling Sheet' tab of the
Wiom calling Google Sheet. One row per caller (dedup), three buckets
stacked: L1 / L2 / NewProject.

Run daily via Windows Task Scheduler.
"""

import json
import os
import sys
import tempfile
from datetime import datetime

import requests
import gspread
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------------------
# Config — secrets come from env (GitHub Actions). This repo is PUBLIC, so the
# Metabase key must NEVER be hardcoded here — it is provided via the MB_KEY secret.
# ---------------------------------------------------------------------------
MB_URL = "https://metabase.wiom.in/api/dataset"
MB_KEY = os.environ.get("MB_KEY", "")
if not MB_KEY:
    sys.exit("MB_KEY not set — provide it via the MB_KEY env var / GitHub secret.")
DB_ID = 113

SHEET_ID = "1HB79kOjNIeHJXPlDET4ksE3QiUvPi6MfG6i5RSw-gm4"
TAB_NAME = "Calling Sheet"   # default; re-bound at runtime from TAB_GID
# Bind the calling tab by GID, not by name. On 2026-09-02 the tab was renamed
# 'Calling Sheet' -> 'calling'; sh.worksheet(TAB_NAME) then raised
# WorksheetNotFound and push_to_sheet happily auto-created a NEW empty
# 'Calling Sheet' tab, so 01/09 rows landed in a fork while the team's real tab
# silently went stale. GID survives renames.
TAB_GID = 0
SUMMARY_TAB = "Summary"

# If GOOGLE_SA_JSON env var is set (base64 or raw JSON), write to temp file;
# otherwise use local path.
_sa_env = os.environ.get("GOOGLE_SA_JSON", "")
if _sa_env:
    _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    _tmp.write(_sa_env)
    _tmp.close()
    SA_JSON = _tmp.name
else:
    SA_JSON = r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"

SHEET_HEADERS = [
    "Date",
    "Category",
    "Sub Category",
    "Phone",
    "Question by Partner",
    "Final Bucket",
    "Bucket",
    "Ticket ID",
]

SQL = r"""
WITH sla_monthly AS (
  SELECT CAST(DATE_TRUNC('MONTH',TASK_RESOLVED_TIME) AS DATE) AS MONTH,
    PARTNER_ACCOUNTID, PERFORMANCE_METRIC,
    COALESCE(SUM(SCORE)/COUNT(ENTRY_EPOCH_TIME),0) AS sla
  FROM PROD_DB.DYNAMODB.TASK_PERFORMANCE
  WHERE CAST(DATE_TRUNC('MONTH',TASK_RESOLVED_TIME) AS DATE) > DATEADD('MONTH',-6,CURRENT_TIMESTAMP)
  GROUP BY 1,2,3
),
sla_agg AS (
  SELECT PARTNER_ACCOUNTID,
    COALESCE(AVG(CASE WHEN PERFORMANCE_METRIC='STRIKE_RATE'
      AND MONTH >= DATE_TRUNC('month', CURRENT_TIMESTAMP - INTERVAL '3 month')
      AND MONTH <  DATE_TRUNC('month', CURRENT_TIMESTAMP) THEN sla END),-1) AS sla_avg
  FROM sla_monthly GROUP BY 1
),
app_eng AS (
  SELECT PARTNER_ACCOUNT_ID,
    COUNT(DISTINCT DATE_TRUNC('minute',TO_TIMESTAMP(timestamp))) AS app_opens
  FROM PROD_DB.PUBLIC.CT_PARTNER_APP_LAUNCH
  WHERE DATE(timestamp) >= DATEADD(day,-30,CURRENT_DATE())
    AND PARTNER_ACCOUNT_ID IS NOT NULL AND PARTNER_ACCOUNT_ID != ''
    AND USER_ROLE IN ('OWNER','ADMIN')
  GROUP BY 1
),
partner_bucket AS (
  SELECT sm.PARTNER_ACCOUNT_ID,
    CASE WHEN COALESCE(s.sla_avg,-1) < 0.8 OR COALESCE(s.sla_avg,-1) < 0 THEN 'L' ELSE 'H' END
      || CASE WHEN COALESCE(ae.app_opens,0) > 0 THEN 'H' ELSE 'L' END AS final_bucket
  FROM SUPPLY_MODEL sm
  LEFT JOIN sla_agg s  ON s.PARTNER_ACCOUNTID   = sm.PARTNER_ACCOUNT_ID
  LEFT JOIN app_eng ae ON ae.PARTNER_ACCOUNT_ID = sm.PARTNER_ACCOUNT_ID
),
kapture_base AS (
  SELECT
    k.TICKET_NO, k.RESOLVED_DATE, k.INGESTED_AT, k.CUSTOMER_CODE, k.PHONE,
    k.DISPOSITION_FOLDER_LEVEL_2, k.DISPOSITION_FOLDER_LEVEL_3, k.DISPOSED_FOLDER,
    k.QUESTION_BY_PARTNER, k.SUB_STATUS,
    CASE
      WHEN k.DISPOSED_FOLDER = 'New Project' THEN 'NewProject'
      WHEN k.SUB_STATUS = 'Resolved on Call' THEN 'L1'
      WHEN k.SUB_STATUS IN ('Completed','Resolved')
           AND (k.DISPOSED_FOLDER IS NULL OR k.DISPOSED_FOLDER != 'New Project') THEN 'L2'
      ELSE NULL
    END AS bucket
  FROM PROD_DB.PUBLIC.KAPTURE_PARTNER_TICKETS_REPORT k
  WHERE TRY_TO_DATE(k.RESOLVED_DATE,'DD/MM/YYYY')
        BETWEEN DATEADD(day,-2,CURRENT_DATE()) AND DATEADD(day,-1,CURRENT_DATE())
    AND k.STATUS = 'Complete'
    AND k.TICKET_TYPE_PARENT_SUB = 'Parent Ticket'
    AND k.SUB_STATUS NOT IN ('Customer Replied','Unattended','Replied')
    AND (k.DISPOSED_FOLDER IS NULL OR k.DISPOSED_FOLDER NOT IN
         ('Not Pick / Unreachable / Switched Off / Call by Mistake', 'Duplicate Tickets',
          'Want partnership of wiom'))
    AND (k.CUSTOMER_NAME IS NULL OR UPPER(k.CUSTOMER_NAME) != 'SANJAY_WIOM_TEST_ACCOUNT')
  QUALIFY ROW_NUMBER() OVER (PARTITION BY k.TICKET_NO ORDER BY k.INGESTED_AT DESC NULLS LAST) = 1
),
joined AS (
  SELECT
    k.RESOLVED_DATE,
    k.DISPOSITION_FOLDER_LEVEL_2,
    k.DISPOSITION_FOLDER_LEVEL_3,
    k.QUESTION_BY_PARTNER,
    k.TICKET_NO,
    k.bucket,
    COALESCE(NULLIF(k.PHONE,''), sm.PARTNER_MOBILE) AS caller_phone,
    COALESCE(pb.final_bucket,'Unknown') AS final_bucket
  FROM kapture_base k
  LEFT JOIN SUPPLY_MODEL sm   ON sm.PARTNER_ACCOUNT_ID  = k.CUSTOMER_CODE
  LEFT JOIN partner_bucket pb ON pb.PARTNER_ACCOUNT_ID  = k.CUSTOMER_CODE
  WHERE k.bucket IS NOT NULL
    AND COALESCE(NULLIF(k.PHONE,''), sm.PARTNER_MOBILE) IS NOT NULL
)
SELECT
  RESOLVED_DATE               AS "Date",
  DISPOSITION_FOLDER_LEVEL_2  AS "Category",
  DISPOSITION_FOLDER_LEVEL_3  AS "Sub Category",
  caller_phone                AS "Phone",
  QUESTION_BY_PARTNER         AS "Question by Partner",
  final_bucket                AS "Final Bucket",
  bucket                      AS "Bucket",
  TICKET_NO                   AS "Ticket ID"
FROM joined
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY TRY_TO_DATE(RESOLVED_DATE,'DD/MM/YYYY'), caller_phone
  ORDER BY TICKET_NO
) = 1
ORDER BY TRY_TO_DATE("Date",'DD/MM/YYYY'), "Bucket", final_bucket
"""


def run_query():
    payload = {
        "database": DB_ID,
        "type": "native",
        "native": {"query": SQL},
    }
    headers = {"x-api-key": MB_KEY, "Content-Type": "application/json"}
    r = requests.post(MB_URL, headers=headers, json=payload, timeout=180)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"Metabase error: {d['error']}")
    cols = [c["name"].lower() for c in d["data"]["cols"]]
    rows = d["data"]["rows"]
    return cols, rows


def resolve_tab_name():
    """Re-bind TAB_NAME to whatever the tab at TAB_GID is currently called."""
    global TAB_NAME
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(SA_JSON, scopes=scopes)
    ws = gspread.authorize(creds).open_by_key(SHEET_ID).get_worksheet_by_id(TAB_GID)
    if ws.title != TAB_NAME:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] Calling tab (gid {TAB_GID}) is named {ws.title!r}, "
              f"not {TAB_NAME!r} — binding to it.")
        TAB_NAME = ws.title
    return TAB_NAME


def push_to_sheet(cols, rows):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(SA_JSON, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    try:
        ws = sh.worksheet(TAB_NAME)
    except gspread.WorksheetNotFound:
        # NEVER auto-create: that forks the sheet and strands the team's manual
        # columns in the old tab. Fail loudly so the run goes red instead.
        raise SystemExit(
            f"Calling tab {TAB_NAME!r} (gid {TAB_GID}) not found — refusing to "
            f"auto-create a duplicate tab. Check the tab gid/name."
        )

    col_idx = {c: i for i, c in enumerate(cols)}
    ordered = [
        [
            (r[col_idx[h.lower()]] if r[col_idx[h.lower()]] is not None else "")
            for h in SHEET_HEADERS
        ]
        for r in rows
    ]

    header_row = ws.row_values(1)

    if not header_row:
        ws.update(
            values=[SHEET_HEADERS],
            range_name=f"A1:H1",
            value_input_option="USER_ENTERED",
        )
    else:
        if "Ticket ID" not in header_row:
            ws.insert_cols([["Ticket ID"]], col=7)
            header_row = ws.row_values(1)
        if "Sub Category" not in header_row:
            # Insert new column at C (index 3); shifts old C..L right to D..M.
            # Manual cols H..L → I..M; data preserved.
            ws.insert_cols([["Sub Category"]], col=3)

    existing = ws.get("A2:H")

    def norm_phone(p):
        p = str(p).strip()
        if p.endswith(".0"):
            p = p[:-2]
        return p.lstrip("+").strip()

    existing_tickets = {r[7].strip() for r in existing if len(r) >= 8 and r[7].strip()}
    existing_date_phones = {(r[0].strip(), norm_phone(r[3]))
                            for r in existing if len(r) >= 4 and r[3].strip()}

    phone_idx  = SHEET_HEADERS.index("Phone")
    ticket_idx = SHEET_HEADERS.index("Ticket ID")
    date_idx   = SHEET_HEADERS.index("Date")

    new_rows = []
    for r in ordered:
        tid = str(r[ticket_idx]).strip()
        ph  = norm_phone(r[phone_idx])
        d   = str(r[date_idx]).strip()
        if tid and tid in existing_tickets:
            continue
        if ph and (d, ph) in existing_date_phones:
            continue
        new_rows.append(r)
        if tid:
            existing_tickets.add(tid)
        if ph:
            existing_date_phones.add((d, ph))

    if not new_rows:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] Skipped — no new tickets/phones to add (pulled {len(ordered)}).")
        return

    next_row = len(existing) + 2  # +1 for header, +1 to land on first empty row
    end_row = next_row + len(new_rows) - 1
    if end_row > ws.row_count:
        ws.add_rows(end_row - ws.row_count + 100)  # grow + buffer
    ws.update(
        values=new_rows,
        range_name=f"A{next_row}:H{end_row}",
        value_input_option="USER_ENTERED",
    )
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"[{stamp}] Appended {len(new_rows)} new rows of {len(ordered)} pulled "
        f"to '{TAB_NAME}' (rows {next_row}-{end_row})."
    )


# Folders that mean "this should never be in the sheet". Kapture frequently
# re-tags a ticket into one of these AFTER the append has already captured it as
# L1/L2/NewProject; the append-only push never revisits the row, so it strands
# stale. Reconcile FLAGS these (does NOT delete — deleting would shift the team's
# manual columns I:L out of alignment).
EXCLUDED_FOLDERS = {
    "Duplicate Tickets": "REMOVED - Duplicate",
    "Not Pick / Unreachable / Switched Off / Call by Mistake": "REMOVED - Not Pick",
    "Want partnership of wiom": "REMOVED - Want partnership",  # prospective-partner enquiries, not support
}

# Current disposition of specific tickets — looked up by explicit Ticket ID (NO
# exclusion filter, so we can also SEE the ones now tagged Duplicate/Not-Pick).
# Queried by ID (not by date window) so it never hits Metabase's ~2000-row cap:
# each batch of IDs returns at most one row per ID. {ids} = comma-separated ints.
RECONCILE_SQL_TMPL = r"""
SELECT k.TICKET_NO, k.DISPOSED_FOLDER AS FOLDER,
  CASE
    WHEN k.DISPOSED_FOLDER = 'New Project' THEN 'NewProject'
    WHEN k.SUB_STATUS = 'Resolved on Call' THEN 'L1'
    WHEN k.SUB_STATUS IN ('Completed','Resolved')
         AND (k.DISPOSED_FOLDER IS NULL OR k.DISPOSED_FOLDER != 'New Project') THEN 'L2'
    ELSE NULL END AS BUCKET
FROM PROD_DB.PUBLIC.KAPTURE_PARTNER_TICKETS_REPORT k
WHERE k.TICKET_NO IN ({ids})
  AND k.STATUS = 'Complete'
  AND k.TICKET_TYPE_PARENT_SUB = 'Parent Ticket'
QUALIFY ROW_NUMBER() OVER (PARTITION BY k.TICKET_NO ORDER BY k.INGESTED_AT DESC NULLS LAST) = 1
"""


def reconcile_recent(ws=None, lookback_rows=2000):
    """Re-check recently-captured rows against CURRENT Kapture disposition and fix
    column G (Bucket) in place. The append-only push freezes a row at capture and
    never revisits it, so a ticket re-dispositioned afterwards (L1<->NewProject
    re-bucketing, or moved into an excluded folder like 'Duplicate Tickets' /
    'Not Pick') stays wrong forever otherwise.
      - bucket changed among L1 / L2 / NewProject -> rewrite G to the new bucket
      - now in an excluded folder                 -> flag G as 'REMOVED - <reason>'
      - no longer qualifying (e.g. reopened)       -> left as-is (conservative)
    Only column G is written; manual columns I:L are never touched. Row numbers
    are derived from a single fresh read taken immediately before the write, so
    there is no drift between matching a Ticket ID and writing its bucket. Scope is
    the last `lookback_rows` rows (append order is chronological, ~110 rows/day),
    and Kapture is queried by explicit Ticket ID in chunks so the lookup never
    hits Metabase's ~2000-row native-query cap."""
    if ws is None:
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(SA_JSON, scopes=scopes)
        ws = gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(TAB_NAME)

    grid = ws.get("A2:H")  # fresh read; sheet row (i+2) == grid[i]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Scope to the most recent rows (bottom of an append-ordered sheet) and collect
    # their numeric ticket ids. Bounds the Kapture lookup to just these tickets.
    start = max(0, len(grid) - lookback_rows)
    tickets = sorted({
        grid[i][7].strip()
        for i in range(start, len(grid))
        if len(grid[i]) >= 8 and grid[i][7].strip().isdigit()
    })
    if not tickets:
        print(f"[{stamp}] Reconcile: no ticketed rows in scope.")
        return

    # Look up current disposition by id, chunked to stay well under the 2000 cap.
    kap = {}
    CHUNK = 800
    for j in range(0, len(tickets), CHUNK):
        ids = ",".join(tickets[j:j + CHUNK])
        _, krows = _run_sql(RECONCILE_SQL_TMPL.format(ids=ids))
        for t, folder, bucket in krows:
            kap[str(t).strip()] = (folder, bucket)

    updates = []
    rebucketed = flagged = 0
    for i, r in enumerate(grid):
        if len(r) < 8:
            continue
        tid = r[7].strip()
        cur = r[6].strip()
        if not tid or tid not in kap:
            continue
        folder, newb = kap[tid]
        if folder in EXCLUDED_FOLDERS:
            target = EXCLUDED_FOLDERS[folder]
        elif newb in ("L1", "L2", "NewProject"):
            target = newb
        else:
            continue  # no longer qualifying — leave as-is
        if target == cur:
            continue
        updates.append({"range": f"G{i + 2}", "values": [[target]]})
        if target.startswith("REMOVED"):
            flagged += 1
        else:
            rebucketed += 1

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        print(f"[{stamp}] Reconciled {len(updates)} rows "
              f"({rebucketed} re-bucketed, {flagged} flagged REMOVED).")
    else:
        print(f"[{stamp}] Reconcile: no stale rows found.")


SUMMARY_SQL = r"""
WITH ist AS (
    SELECT CONVERT_TIMEZONE('UTC','Asia/Kolkata',CURRENT_TIMESTAMP())::DATE AS today
),
kapture AS (
    SELECT
        TRY_TO_DATE(k.CREATED_DATE,'DD/MM/YYYY') AS cdate,
        k.TICKET_NO, k.STATUS, k.SUB_STATUS, k.DISPOSED_FOLDER,
        (TRY_TO_NUMBER(SPLIT_PART(k.DIFF_TIME_CREATE_AND_RESOLVE_WORKING_HOURS,':',1))*60
         + TRY_TO_NUMBER(SPLIT_PART(k.DIFF_TIME_CREATE_AND_RESOLVE_WORKING_HOURS,':',2))
         + TRY_TO_NUMBER(SPLIT_PART(k.DIFF_TIME_CREATE_AND_RESOLVE_WORKING_HOURS,':',3))/60.0) AS tat_mins,
        CASE
            WHEN k.DISPOSED_FOLDER = 'New Project' THEN 'NewProject'
            WHEN k.SUB_STATUS = 'Resolved on Call' THEN 'L1'
            WHEN k.SUB_STATUS IN ('Completed','Open','Resolved')
                 AND (k.DISPOSED_FOLDER IS NULL OR k.DISPOSED_FOLDER != 'New Project') THEN 'L2'
            ELSE NULL
        END AS bucket
    FROM PROD_DB.PUBLIC.KAPTURE_PARTNER_TICKETS_REPORT k, ist
    WHERE TRY_TO_DATE(k.CREATED_DATE,'DD/MM/YYYY') >= LEAST(
        DATE_TRUNC('month', ist.today),
        DATEADD(day, -7, ist.today)
      )
      AND TRY_TO_DATE(k.CREATED_DATE,'DD/MM/YYYY') < ist.today
      AND k.TICKET_TYPE_PARENT_SUB = 'Parent Ticket'
      AND k.SUB_STATUS NOT IN ('Customer Replied','Unattended','Replied')
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY k.TICKET_NO
      ORDER BY CONVERT_TIMEZONE('UTC','Asia/Kolkata',k.INGESTED_AT) DESC NULLS LAST
    ) = 1
)
SELECT cdate, bucket,
       COUNT(*)                                                              AS total,
       COUNT_IF(STATUS='Complete')                                           AS resolved,
       COUNT_IF(STATUS='Complete' AND tat_mins IS NOT NULL AND tat_mins<=1440) AS resolved_24h,
       MEDIAN(CASE WHEN STATUS='Complete' AND tat_mins>0 THEN tat_mins END)  AS med_tat
FROM kapture
WHERE bucket IS NOT NULL AND cdate IS NOT NULL
GROUP BY cdate, bucket
ORDER BY cdate, bucket
"""


def _run_sql(sql):
    r = requests.post(
        MB_URL,
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"},
        json={"database": DB_ID, "type": "native", "native": {"query": sql},
              "constraints": None},
        timeout=180,
    )
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"Metabase error: {d['error']}")
    return [c["name"].lower() for c in d["data"]["cols"]], d["data"]["rows"]


def _read_calling_psat(ws):
    from collections import defaultdict
    from datetime import datetime as dt

    all_rows = ws.get_all_values()
    psat = defaultdict(list)
    conn = defaultdict(float)
    psat_fb = defaultdict(list)
    for row in all_rows[1:]:
        if len(row) < 13:
            row = row + [""] * (13 - len(row))
        d_str = row[0].strip()
        final_b = row[5].strip()   # Final Bucket (col F after Sub Category insert)
        bucket = row[6].strip()    # Bucket (col G)
        k_val = row[11].strip()    # Connected (col L, was K)
        l_val = row[12].strip()    # PSAT (col M, was L)
        try:
            dd = dt.strptime(d_str, "%d/%m/%Y").date()
        except ValueError:
            continue
        if k_val:
            try:
                conn[(bucket, dd)] += float(k_val)
            except ValueError:
                if k_val.upper() in ("Y", "YES"):
                    conn[(bucket, dd)] += 1
        if l_val:
            try:
                lv = float(l_val.rstrip("%"))
                psat[(bucket, dd)].append(lv)
                if final_b:
                    psat_fb[(bucket, final_b, dd)].append(lv)
            except ValueError:
                pass
    return psat, conn, psat_fb


def refresh_summary():
    import statistics
    from datetime import datetime as dt, date as D, timedelta as td

    cols, raw = _run_sql(SUMMARY_SQL)
    ci = {c: i for i, c in enumerate(cols)}

    today = D.today()
    day_periods = [(f"D-{i}", today - td(days=i), today - td(days=i)) for i in range(1, 8)]
    mtd_p0 = today.replace(day=1)
    mtd_p1 = max(mtd_p0, today - td(days=1))
    periods = day_periods + [("MTD", mtd_p0, mtd_p1)]

    # Parse aggregated rows: each row is (cdate, bucket, total, resolved, resolved_24h, med_tat)
    from collections import defaultdict
    agg = {}  # (date, bucket) -> {total, resolved, resolved_24h, med_tat}
    for r in raw:
        cdate = r[ci["cdate"]]
        if isinstance(cdate, str):
            cdate = dt.strptime(cdate[:10], "%Y-%m-%d").date()
        bucket = r[ci["bucket"]]
        agg[(cdate, bucket)] = {
            "total": r[ci["total"]] or 0,
            "resolved": r[ci["resolved"]] or 0,
            "resolved_24h": r[ci["resolved_24h"]] or 0,
            "med_tat": r[ci["med_tat"]],
        }

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(SA_JSON, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws_call = sh.worksheet(TAB_NAME)
    psat_vals, conn_vals, psat_fb_vals = _read_calling_psat(ws_call)

    buckets = ["L2", "L1", "NewProject", "L2_WIDE"]
    metrics = {}
    for bkey in buckets:
        for name, p0, p1 in periods:
            total = 0
            resolved = 0
            resolved_24h = 0
            tat_vals = []

            d = p0
            while d <= p1:
                if bkey == "L2_WIDE":
                    for b in ("L2", "NewProject"):
                        a = agg.get((d, b), {})
                        total += a.get("total", 0)
                        resolved += a.get("resolved", 0)
                        resolved_24h += a.get("resolved_24h", 0)
                        if a.get("med_tat") is not None:
                            tat_vals.append(a["med_tat"])
                else:
                    a = agg.get((d, bkey), {})
                    total += a.get("total", 0)
                    resolved += a.get("resolved", 0)
                    resolved_24h += a.get("resolved_24h", 0)
                    if a.get("med_tat") is not None:
                        tat_vals.append(a["med_tat"])
                d += td(days=1)

            med_tat = int(round(statistics.median(tat_vals))) if tat_vals else None
            pct_24h = round(100 * resolved_24h / total, 1) if total else None
            pct_till = round(100 * resolved / total, 1) if total else None

            psat_list = []
            conn_sum = 0
            d = p0
            while d <= p1:
                psat_list.extend(psat_vals.get((bkey, d), []))
                conn_sum += conn_vals.get((bkey, d), 0)
                d += td(days=1)
            psat_avg = round(statistics.mean(psat_list), 1) if psat_list else None

            metrics[(bkey, name)] = {
                "total": total,
                "pct_24h": pct_24h,
                "pct_till": pct_till,
                "med_tat": med_tat,
                "psat": psat_avg,
                "connected": int(conn_sum),
            }

    period_names = [p[0] for p in periods]
    date_labels = [
        (p[1].strftime("%#d-%b") if p[0] != "MTD" else "")
        for p in periods
    ]

    def pct(v):
        return f"{v}%" if v is not None else "-"

    def num(v):
        return v if v is not None else "-"

    output = []
    output.append(["(Dates)"] + date_labels)
    output.append(["Metric"] + period_names)
    output.append(["Total L2"] + [metrics[("L2_WIDE", n)]["total"] for n in period_names])
    output.append(["Resolved% (<=24 Hours)"] + [pct(metrics[("L2_WIDE", n)]["pct_24h"]) for n in period_names])
    output.append(["Resolved% (till date)"] + [pct(metrics[("L2_WIDE", n)]["pct_till"]) for n in period_names])
    output.append(["Resolution TAT - median(mins)"] + [num(metrics[("L2_WIDE", n)]["med_tat"]) for n in period_names])
    output.append(["PSAT L2"] + [pct(metrics[("L2", n)]["psat"]) for n in period_names])
    output.append([""] * (1 + len(period_names)))
    output.append(["PSAT L1"] + [pct(metrics[("L1", n)]["psat"]) for n in period_names])
    output.append(["PSAT NEW PROJECT"] + [pct(metrics[("NewProject", n)]["psat"]) for n in period_names])
    output.append([""] * (1 + len(period_names)))
    output.append(["# Connected L2"] + [metrics[("L2", n)]["connected"] for n in period_names])
    output.append(["# Connected L1"] + [metrics[("L1", n)]["connected"] for n in period_names])
    output.append(["# Connected NEW PROJECT"] + [metrics[("NewProject", n)]["connected"] for n in period_names])

    main_last_row = len(output)  # 1-indexed: rows 1..main_last_row belong to main table

    fb_tables_spec = [
        ("L2",         "PSAT L2 by Final Bucket"),
        ("L1",         "PSAT L1 by Final Bucket"),
        ("NewProject", "PSAT NEW PROJECT by Final Bucket"),
    ]
    final_buckets = ["HH", "HL", "LH", "LL"]
    fb_ranges = []  # list of (start_row_1idx, end_row_1idx, title_row_1idx)

    for bkey, title in fb_tables_spec:
        output.append([""] * (1 + len(period_names)))  # spacer above
        start_1 = len(output) + 1  # next row
        output.append(["(Dates)"] + date_labels)
        output.append(["Metric"] + period_names)
        title_row_1 = len(output) + 1
        output.append(["PSAT", title] + [""] * (len(period_names) - 1))
        for fb in final_buckets:
            row = [fb]
            for pname, p0, p1 in periods:
                vals = []
                d = p0
                while d <= p1:
                    vals.extend(psat_fb_vals.get((bkey, fb, d), []))
                    d += td(days=1)
                avg = round(statistics.mean(vals), 1) if vals else None
                row.append(pct(avg))
            output.append(row)
        output.append(["Overall PSAT"] + [pct(metrics[(bkey, n)]["psat"]) for n in period_names])
        end_1 = len(output)
        fb_ranges.append((start_1, end_1, title_row_1))

    total_last_row = len(output)

    try:
        ws_sum = sh.worksheet(SUMMARY_TAB)
    except gspread.WorksheetNotFound:
        ws_sum = sh.add_worksheet(title=SUMMARY_TAB, rows=50, cols=12)

    ws_sum.clear()
    ws_sum.update(values=output, range_name="A1", value_input_option="USER_ENTERED")

    meta = sh.fetch_sheet_metadata()
    sum_sheet = next(s for s in meta["sheets"] if s["properties"]["title"] == SUMMARY_TAB)
    sum_sid = sum_sheet["properties"]["sheetId"]

    reqs = []
    for c in sum_sheet.get("charts", []):
        reqs.append({"deleteEmbeddedObject": {"objectId": c["chartId"]}})

    n_cols = 1 + len(period_names)
    last_col = chr(ord("A") + n_cols - 1)
    header_bg = {"red": 0.761, "green": 0.094, "blue": 0.365}
    label_bg = {"red": 0.976, "green": 0.902, "blue": 0.941}
    white = {"red": 1, "green": 1, "blue": 1}
    border = {"style": "SOLID", "width": 1,
              "color": {"red": 0.8, "green": 0.8, "blue": 0.8}}

    last_data_row = main_last_row
    reqs.extend([
        {"repeatCell": {
            "range": {"sheetId": sum_sid, "startRowIndex": 0, "endRowIndex": 2,
                      "startColumnIndex": 0, "endColumnIndex": n_cols},
            "cell": {"userEnteredFormat": {
                "backgroundColor": header_bg,
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {"foregroundColor": white, "bold": True, "fontSize": 11},
            }},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
        }},
        {"repeatCell": {
            "range": {"sheetId": sum_sid, "startRowIndex": 2, "endRowIndex": last_data_row,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": label_bg,
                "textFormat": {"bold": True, "italic": True, "fontSize": 10},
                "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
        }},
        {"repeatCell": {
            "range": {"sheetId": sum_sid, "startRowIndex": 2, "endRowIndex": last_data_row,
                      "startColumnIndex": 1, "endColumnIndex": n_cols},
            "cell": {"userEnteredFormat": {
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {"fontSize": 10},
            }},
            "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment,textFormat)",
        }},
        {"updateBorders": {
            "range": {"sheetId": sum_sid, "startRowIndex": 0, "endRowIndex": last_data_row,
                      "startColumnIndex": 0, "endColumnIndex": n_cols},
            "top": border, "bottom": border, "left": border, "right": border,
            "innerHorizontal": border, "innerVertical": border,
        }},
        {"updateSheetProperties": {
            "properties": {"sheetId": sum_sid,
                           "gridProperties": {"frozenRowCount": 2, "frozenColumnCount": 1}},
            "fields": "gridProperties(frozenRowCount,frozenColumnCount)",
        }},
        {"autoResizeDimensions": {
            "dimensions": {"sheetId": sum_sid, "dimension": "COLUMNS",
                           "startIndex": 0, "endIndex": n_cols},
        }},
    ])

    # Force integer-percent display on percent rows in main table.
    # Output rows (1-indexed): 4=Resolved%(24h), 5=Resolved%(till), 7=PSAT L2,
    # 9=PSAT L1, 10=PSAT NEW PROJECT. 0-indexed: 3,4,6,8,9.
    for pr in (3, 4, 6, 8, 9):
        reqs.append({"repeatCell": {
            "range": {"sheetId": sum_sid, "startRowIndex": pr, "endRowIndex": pr + 1,
                      "startColumnIndex": 1, "endColumnIndex": n_cols},
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "PERCENT", "pattern": "0%"},
            }},
            "fields": "userEnteredFormat.numberFormat",
        }})

    # Formatting for each Final-Bucket PSAT table
    for start_1, end_1, title_row_1 in fb_ranges:
        s0 = start_1 - 1  # 0-indexed start (dates row)
        e0 = end_1        # 0-indexed exclusive end
        t0 = title_row_1 - 1  # 0-indexed title row

        reqs.extend([
            # Dates + Metric rows => header_bg
            {"repeatCell": {
                "range": {"sheetId": sum_sid, "startRowIndex": s0, "endRowIndex": s0 + 2,
                          "startColumnIndex": 0, "endColumnIndex": n_cols},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": header_bg,
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {"foregroundColor": white, "bold": True, "fontSize": 11},
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
            }},
            # Title row (merged look): label_bg, bold italic, spans all cols
            {"repeatCell": {
                "range": {"sheetId": sum_sid, "startRowIndex": t0, "endRowIndex": t0 + 1,
                          "startColumnIndex": 0, "endColumnIndex": n_cols},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": label_bg,
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {"bold": True, "italic": True, "fontSize": 11,
                                   "foregroundColor": header_bg},
                }},
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
            }},
            {"mergeCells": {
                "range": {"sheetId": sum_sid, "startRowIndex": t0, "endRowIndex": t0 + 1,
                          "startColumnIndex": 1, "endColumnIndex": n_cols},
                "mergeType": "MERGE_ALL",
            }},
            # Label column (A) for bucket rows + overall row
            {"repeatCell": {
                "range": {"sheetId": sum_sid, "startRowIndex": t0 + 1, "endRowIndex": e0,
                          "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": label_bg,
                    "textFormat": {"bold": True, "italic": True, "fontSize": 10},
                    "verticalAlignment": "MIDDLE",
                }},
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
            }},
            # Data cells center-aligned + integer-percent format
            {"repeatCell": {
                "range": {"sheetId": sum_sid, "startRowIndex": t0 + 1, "endRowIndex": e0,
                          "startColumnIndex": 1, "endColumnIndex": n_cols},
                "cell": {"userEnteredFormat": {
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {"fontSize": 10},
                    "numberFormat": {"type": "PERCENT", "pattern": "0%"},
                }},
                "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment,textFormat,numberFormat)",
            }},
            # Emphasize Overall PSAT row (last row of table)
            {"repeatCell": {
                "range": {"sheetId": sum_sid, "startRowIndex": e0 - 1, "endRowIndex": e0,
                          "startColumnIndex": 0, "endColumnIndex": n_cols},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True, "fontSize": 10},
                }},
                "fields": "userEnteredFormat.textFormat",
            }},
            # Borders around whole table
            {"updateBorders": {
                "range": {"sheetId": sum_sid, "startRowIndex": s0, "endRowIndex": e0,
                          "startColumnIndex": 0, "endColumnIndex": n_cols},
                "top": border, "bottom": border, "left": border, "right": border,
                "innerHorizontal": border, "innerVertical": border,
            }},
        ])

    chart_specs = [
        ("L2",          "L2 PSAT Trend — Last 7 Days",           7, 12),
        ("L1",          "L1 PSAT Trend — Last 7 Days",           9, 13),
        ("NEW PROJECT", "New Project PSAT Trend — Last 7 Days", 10, 14),
    ]
    anchor_row_0 = total_last_row + 2
    for _, title, psat_row, conn_row in chart_specs:
        reqs.append({
            "addChart": {
                "chart": {
                    "spec": {
                        "title": title,
                        "titleTextFormat": {"bold": True, "fontSize": 14,
                                            "foregroundColor": header_bg},
                        "basicChart": {
                            "chartType": "COMBO",
                            "legendPosition": "BOTTOM_LEGEND",
                            "axis": [
                                {"position": "BOTTOM_AXIS", "title": "# of Calls (Connected)"},
                                {"position": "LEFT_AXIS",   "title": "PSAT"},
                                {"position": "RIGHT_AXIS",  "title": "# Connected"},
                            ],
                            "domains": [{
                                "domain": {"sourceRange": {"sources": [{
                                    "sheetId": sum_sid,
                                    "startRowIndex": 0, "endRowIndex": 1,
                                    "startColumnIndex": 1, "endColumnIndex": 8,
                                }]}}
                            }],
                            "series": [
                                {
                                    "series": {"sourceRange": {"sources": [{
                                        "sheetId": sum_sid,
                                        "startRowIndex": conn_row - 1, "endRowIndex": conn_row,
                                        "startColumnIndex": 1, "endColumnIndex": 8,
                                    }]}},
                                    "targetAxis": "RIGHT_AXIS",
                                    "type": "COLUMN",
                                    "color": {"red": 0.73, "green": 0.50, "blue": 0.62},
                                },
                                {
                                    "series": {"sourceRange": {"sources": [{
                                        "sheetId": sum_sid,
                                        "startRowIndex": psat_row - 1, "endRowIndex": psat_row,
                                        "startColumnIndex": 1, "endColumnIndex": 8,
                                    }]}},
                                    "targetAxis": "LEFT_AXIS",
                                    "type": "LINE",
                                    "color": header_bg,
                                    "lineStyle": {"type": "SOLID", "width": 3},
                                },
                            ],
                            "headerCount": 0,
                        },
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {
                                "sheetId": sum_sid,
                                "rowIndex": anchor_row_0 - 1,
                                "columnIndex": 0,
                            },
                            "widthPixels": 820,
                            "heightPixels": 340,
                        }
                    },
                }
            }
        })
        anchor_row_0 += 18

    sh.batch_update({"requests": reqs})

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] Summary refreshed.")


def main():
    cols, rows = run_query()
    push_to_sheet(cols, rows)
    reconcile_recent()   # fix rows whose Kapture disposition changed after capture
    refresh_summary()


def _calling_sheet_needs_push():
    """Return True if yesterday's resolved-date data hasn't been pushed yet."""
    from datetime import date, timedelta
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(SA_JSON, scopes=scopes)
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(SHEET_ID).worksheet(TAB_NAME)
    yesterday = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
    dates_in_sheet = {r.strip() for r in ws.col_values(1)[1:] if r.strip()}
    return yesterday not in dates_in_sheet


if __name__ == "__main__":
    try:
        resolve_tab_name()
        if "--summary-only" in sys.argv:
            if _calling_sheet_needs_push():
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{stamp}] Yesterday's data not in sheet yet — pushing first.")
                cols, rows = run_query()
                push_to_sheet(cols, rows)
            reconcile_recent()   # keep buckets/duplicates in sync on every refresh
            refresh_summary()
        else:
            main()
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        raise
