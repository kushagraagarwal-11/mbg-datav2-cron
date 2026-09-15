"""
'Netbox Order Funnel' tab of the P1/P2 Winbacks sheet -- for a fixed CSP list, since 8 Sep 2026:
eligible to order -> placed an order -> order delivered -> installed after delivery.

INPUT
  The CSP IDs in column B of the per-CSP table (below the funnel). That column is the list:
  add or remove an ID there and the next refresh picks it up. The first build seeds it from
  --seed <file>. The script never writes column B of the table.

STAGES (nested -- each stage is a subset of the one above)
  1  CSPs in the list
  2  Eligible to order     CSP_PAYMENT_SETTLEMENT_SERVICE.CSP_EXPOSURE.DEVICE_ORDERING_ALLOWED
                           was TRUE at some point since 8 Sep (history-mode table). This is the
                           app's per-CSP ordering switch (gate 0). The app's other eligibility
                           gates (idle-stock / pending receipt / SD affordability / MOQ) are
                           computed live and NOT stored in the warehouse, so they can't be shown.
  3  Order placed          >=1 DEVICE_ORDERS request created on/after 8 Sep (IST), any status
  4  Order delivered       >=1 of those requests reached FULFILLED; delivered-at = the first
                           history version with STATUS = FULFILLED
  5  Installed after delivery   >=1 install completed at/after the CSP's first delivery
  Per stage also: active base (latest Quality OS snapshot), installs 25-31 Aug (last week of
  August) and installs since 8 Sep -- summed over that stage's CSPs -- each with a per-day
  average: Aug / 7; since 8 Sep / exact days elapsed since 8 Sep 00:00 IST (count runs to now).

INSTALLS  PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES, all row versions, attributed to the installing
          CSP; completed-at = INSTALLATION_COMPLETED_AT, else first version showing the install.
NETBOX IN HAND  NETBOX_CUSTODY current, STATUS IN (CUSTODIED, IDLE, RETRIEVAL_PENDING).
"""
import sys
import datetime as dt

import gspread

from winback_common import SHEET_ID, IST, mb, gclient

TAB = "Netbox Order Funnel"
START = dt.date(2026, 9, 8)
START_TS = "'2026-09-08 00:00:00 +05:30'::TIMESTAMP_TZ"
TABLE_HDR_ROW = 14                      # per-CSP header row; CSP IDs from the row below
AUG_START, AUG_END = dt.date(2026, 8, 25), dt.date(2026, 8, 31)     # last week of August
HDRS = ["CSP ID", "CSP Name", "Active\nbase", "Eligible to\norder", "Orders placed\nsince 8 Sep",
        "Devices\nrequested", "Orders\ndelivered", "Devices\ndelivered", "First\ndelivered on",
        "Installs\n25-31 Aug", "Installs\nsince 8 Sep", "Installs after\ndelivery",
        "Netboxes\nin hand now", "Latest order\nstatus"]

INK = {"red": 0.09, "green": 0.24, "blue": 0.20}
MUTED = {"red": 0.42, "green": 0.46, "blue": 0.45}
TILE = {"red": 0.93, "green": 0.96, "blue": 0.94}
HDR = {"red": 0.17, "green": 0.33, "blue": 0.29}
WHITE = {"red": 1, "green": 1, "blue": 1}


def chunks(seq, n=40):
    seq = sorted(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def q(ids):
    return "','".join(ids)


def fetch(ids):
    names, allowed, orders, installs, netbox, active = {}, {}, {}, {}, {}, {}
    for c in chunks(ids):
        for r in mb("SELECT CSP_ID, MAX(PARTNER_NAME) FROM PROD_DB.DBT_CSP.DIM_CSP "
                    "WHERE ETL_CURRENT AND CSP_ID IN ('%s') GROUP BY 1" % q(c)):
            names[r[0]] = r[1] or ""
        for r in mb("""
          SELECT CSP_ID, MAX(IFF(DEVICE_ORDERING_ALLOWED AND _FIVETRAN_END >= %s, 1, 0))
          FROM PROD_DB.CSP_PAYMENT_SETTLEMENT_SERVICE_CSP_PAYMENT_SETTLEMENT_SERVICE.CSP_EXPOSURE
          WHERE CSP_ID IN ('%s') GROUP BY 1""" % (START_TS, q(c))):
            allowed[r[0]] = bool(r[1])
        # one row per request: current state + when it was first FULFILLED
        for r in mb("""
          WITH v AS (
            SELECT REQUEST_ID, CSP_ID, STATUS, QUANTITY_REQUESTED, QUANTITY_APPROVED, CREATED_AT,
                   _FIVETRAN_ACTIVE, _FIVETRAN_START
            FROM PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.DEVICE_ORDERS
            WHERE CSP_ID IN ('%s') AND CREATED_AT >= %s)
          SELECT CSP_ID, REQUEST_ID,
                 MAX(IFF(_FIVETRAN_ACTIVE, STATUS, NULL)),
                 MAX(IFF(_FIVETRAN_ACTIVE, QUANTITY_REQUESTED, NULL)),
                 MAX(IFF(_FIVETRAN_ACTIVE, QUANTITY_APPROVED, NULL)),
                 MIN(IFF(STATUS = 'FULFILLED', _FIVETRAN_START, NULL)),
                 MIN(CREATED_AT)
          FROM v GROUP BY 1, 2""" % (q(c), START_TS)):
            orders.setdefault(r[0], []).append(dict(
                status=r[2] or "", req=int(r[3] or 0), appr=int(r[4] or 0),
                delivered=ts(r[5]) if r[5] else None,
                created=ts(r[6])))
        for r in mb("""
          WITH v AS (
            SELECT CSP_ID, CONNECTION_ID,
                   COALESCE(MIN(INSTALLATION_COMPLETED_AT),
                            MIN(IFF(OTP_VERIFIED_FLAG OR COMPLETED_STEP>=7, VALID_FROM, NULL))) AS done_at
            FROM PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES
            WHERE CSP_ID IN ('%s')
              AND (OTP_VERIFIED_FLAG OR INSTALLATION_COMPLETED_AT IS NOT NULL OR COMPLETED_STEP>=7)
            GROUP BY 1, 2)
          SELECT CSP_ID, done_at FROM v
          WHERE done_at >= '2026-08-25 00:00:00 +05:30'::TIMESTAMP_TZ""" % q(c)):
            installs.setdefault(r[0], []).append(ts(r[1]))
        # active base = ACTIVE_CONNECTION_COUNT on the latest Quality OS snapshot
        for r in mb("""
          SELECT CSP_ID, ACTIVE_CONNECTION_COUNT
          FROM PROD_DB.CSP_QUALITY_SERVICE_CSP_QUALITY_SERVICE.DAILY_METRIC_SNAPSHOTS
          WHERE _FIVETRAN_ACTIVE AND CSP_ID IN ('%s')
            AND SNAPSHOT_DATE = (SELECT MAX(SNAPSHOT_DATE)
                FROM PROD_DB.CSP_QUALITY_SERVICE_CSP_QUALITY_SERVICE.DAILY_METRIC_SNAPSHOTS
                WHERE _FIVETRAN_ACTIVE)""" % q(c)):
            active[r[0]] = int(r[1] or 0)
        for r in mb("""
          SELECT CSP_ID, COUNT(*)
          FROM PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.NETBOX_CUSTODY
          WHERE _FIVETRAN_ACTIVE AND CSP_ID IN ('%s')
            AND STATUS IN ('CUSTODIED','IDLE','RETRIEVAL_PENDING')
          GROUP BY 1""" % q(c)):
            netbox[r[0]] = int(r[1] or 0)
    return names, allowed, orders, installs, netbox, active


def ts(v):
    return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))


def pct(n, d):
    return "%d%%" % round(100.0 * n / d) if d else "-"


def main():
    gc = gclient()
    sh = gc.open_by_key(SHEET_ID)
    seed = None
    if "--seed" in sys.argv:
        seed = [l.strip() for l in open(sys.argv[sys.argv.index("--seed") + 1]) if l.strip()]
    try:
        ws = sh.worksheet(TAB)
    except gspread.WorksheetNotFound:
        if not seed:
            print("ABORT: tab %r missing and no --seed list given" % TAB)
            return 1
        ws = sh.add_worksheet(TAB, rows=TABLE_HDR_ROW + len(seed) + 30, cols=len(HDRS) + 2)

    if seed:
        ids = list(dict.fromkeys(seed))
    else:
        ids = [c[0].strip() for c in ws.get_values("B%d:B2000" % (TABLE_HDR_ROW + 1)) if c and c[0].strip()]
        ids = list(dict.fromkeys(ids))
    if not ids:
        print("ABORT: no CSP IDs in column B below row %d" % TABLE_HDR_ROW)
        return 1

    names, allowed, orders, installs, netbox, active = fetch(ids)
    start_dt = dt.datetime(2026, 9, 8, tzinfo=IST)

    body, per, st = [], {}, {"elig": set(), "placed": set(), "deliv": set(), "inst": set()}
    vol = dict(orders=0, req=0, deliv_orders=0, deliv_dev=0, inst_all=0, inst_after=0)
    for c in ids:
        o = orders.get(c, [])
        deliv = [x for x in o if x["status"] == "FULFILLED"]
        first_deliv = min((x["delivered"] for x in deliv if x["delivered"]), default=None)
        all_ins = installs.get(c, [])
        aug = [t for t in all_ins if AUG_START <= t.astimezone(IST).date() <= AUG_END]
        ins = [t for t in all_ins if t >= start_dt]                 # since 8 Sep
        after = [t for t in ins if first_deliv and t >= first_deliv]
        latest = max(o, key=lambda x: x["created"])["status"] if o else "-"
        el = allowed.get(c, False)
        if el:
            st["elig"].add(c)
            if o:
                st["placed"].add(c)
                if deliv:
                    st["deliv"].add(c)
                    if after:
                        st["inst"].add(c)
        vol["orders"] += len(o); vol["req"] += sum(x["req"] for x in o)
        vol["deliv_orders"] += len(deliv); vol["deliv_dev"] += sum(x["appr"] for x in deliv)
        vol["inst_all"] += len(ins); vol["inst_after"] += len(after)
        per[c] = (active.get(c, 0), len(aug), len(ins))
        body.append([c, names.get(c, ""), active.get(c, 0), "Yes" if el else "No", len(o),
                     sum(x["req"] for x in o), len(deliv), sum(x["appr"] for x in deliv),
                     first_deliv.astimezone(IST).strftime("%d %b") if first_deliv else "-",
                     len(aug), len(ins), len(after) if first_deliv else "-", netbox.get(c, 0), latest])

    n = len(ids)
    groups = [set(ids), st["elig"], st["placed"], st["deliv"], st["inst"]]

    def sums(g):                      # active base, installs 25-31 Aug, installs since 8 Sep
        return tuple(sum(per[c][k] for c in g) for k in range(3))

    stages = [
        ("CSPs in the list", n, ""),
        ("Eligible to order (ordering switch ON)", len(st["elig"]), ""),
        ("Placed a netbox order since 8 Sep", len(st["placed"]),
         "%d orders · %d devices requested" % (vol["orders"], vol["req"])),
        ("Order delivered", len(st["deliv"]),
         "%d orders · %d devices delivered" % (vol["deliv_orders"], vol["deliv_dev"])),
        ("Installed after delivery", len(st["inst"]),
         "%d installs after delivery" % vol["inst_after"]),
    ]
    now = dt.datetime.now(IST)
    # installs since 8 Sep run up to NOW, so their per-day average divides by the exact time
    # elapsed since 8 Sep 00:00 IST (e.g. 8.7 days), not a whole-day count
    days_since = (now - start_dt).total_seconds() / 86400.0

    ws.clear()
    wipe = {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": max(ws.row_count, 200),
            "startColumnIndex": 0, "endColumnIndex": max(ws.col_count, 16)}
    sh.batch_update({"requests": [
        {"unmergeCells": {"range": wipe}},
        {"repeatCell": {"range": wipe, "cell": {"userEnteredFormat": {
            "backgroundColor": WHITE, "horizontalAlignment": "LEFT",
            "textFormat": {"bold": False, "fontSize": 10, "foregroundColor": {"red": 0, "green": 0, "blue": 0}}}},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)"}}]})

    top = [[""] * 12 for _ in range(TABLE_HDR_ROW - 2)]
    top[0][0] = "NETBOX ORDER FUNNEL  ·  since 8 Sep 2026"
    top[1][0] = ("%d CSPs · eligible = app ordering switch ON (other eligibility gates are not stored "
                 "in the warehouse) · each stage is a subset of the one above · installs by completion "
                 "date · refreshed %s IST" % (n, now.strftime("%d %b %Y, %H:%M")))
    top[3] = ["Stage", "", "CSPs", "% of list", "% of previous", "Funnel", "Active base",
              "Installs 25-31 Aug\n(7 days)", "Per day\n(Aug)",
              "Installs since 8 Sep\n(%.1f days)" % days_since, "Per day\n(since 8 Sep)", "Volume"]
    ipd_colours = []
    for i, (lab, cnt, v) in enumerate(stages):
        prev = stages[i - 1][1] if i else None
        ab, ia, i8 = sums(groups[i])
        a_pd, s_pd = round(ia / 7.0, 1), round(i8 / days_since, 1)
        top[4 + i] = [lab, "", cnt, pct(cnt, n), pct(cnt, prev) if prev is not None else "-", "",
                      ab, ia, a_pd, i8, s_pd, v]
        if s_pd != a_pd:
            ipd_colours.append((5 + i, s_pd > a_pd))
    ws.update(values=top, range_name="B2", value_input_option="RAW")
    ws.update(values=[HDRS] + body, range_name="B%d" % TABLE_HDR_ROW, value_input_option="RAW")
    # funnel bars: in-cell bar sized to the stage's share of the list
    ws.update(values=[['=SPARKLINE(D%d,{"charttype","bar";"max",$D$6;"color1","#2b5449"})' % (6 + i)]
                      for i in range(len(stages))],
              range_name="G6:G%d" % (5 + len(stages)), value_input_option="USER_ENTERED")

    sid = ws.id
    last = TABLE_HDR_ROW + len(body)
    reqs = [
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
            "hideGridlines": True, "frozenRowCount": 0}},
            "fields": "gridProperties(hideGridlines,frozenRowCount)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2,
                                  "startColumnIndex": 1, "endColumnIndex": 2},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 15,
                                                                      "foregroundColor": INK}}},
                        "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 3,
                                  "startColumnIndex": 1, "endColumnIndex": 2},
                        "cell": {"userEnteredFormat": {"textFormat": {"fontSize": 9, "foregroundColor": MUTED}}},
                        "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 4, "endRowIndex": 5,
                                  "startColumnIndex": 1, "endColumnIndex": 13},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "foregroundColor": WHITE},
                                                       "backgroundColor": HDR, "horizontalAlignment": "CENTER",
                                                       "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment,"
                                  "verticalAlignment,wrapStrategy)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 5, "endRowIndex": 5 + len(stages),
                                  "startColumnIndex": 1, "endColumnIndex": 13},
                        "cell": {"userEnteredFormat": {"backgroundColor": TILE}},
                        "fields": "userEnteredFormat.backgroundColor"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 5, "endRowIndex": 5 + len(stages),
                                  "startColumnIndex": 3, "endColumnIndex": 6},
                        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER",
                                                       "textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat(horizontalAlignment,textFormat.bold)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 5, "endRowIndex": 5 + len(stages),
                                  "startColumnIndex": 7, "endColumnIndex": 12},
                        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                        "fields": "userEnteredFormat.horizontalAlignment"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": TABLE_HDR_ROW - 1, "endRowIndex": TABLE_HDR_ROW,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + len(HDRS)},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 9,
                                                                      "foregroundColor": WHITE},
                                                       "backgroundColor": HDR, "horizontalAlignment": "CENTER",
                                                       "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment,"
                                  "verticalAlignment,wrapStrategy)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": TABLE_HDR_ROW, "endRowIndex": last,
                                  "startColumnIndex": 3, "endColumnIndex": 1 + len(HDRS)},
                        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                        "fields": "userEnteredFormat.horizontalAlignment"}},
    ]
    for i in range(len(stages)):                       # stage label spans B:C
        reqs.append({"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 5 + i, "endRowIndex": 6 + i,
                                              "startColumnIndex": 1, "endColumnIndex": 3},
                                    "mergeType": "MERGE_ALL"}})
    reqs.append({"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 4, "endRowIndex": 5,
                                          "startColumnIndex": 1, "endColumnIndex": 3},
                                "mergeType": "MERGE_ALL"}})
    for idx, w in enumerate([80, 190, 80, 80, 90, 110, 110, 130, 80, 90, 90, 90, 80, 100]):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 1 + idx, "endIndex": 2 + idx},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 6, "endIndex": 7},
        "properties": {"pixelSize": 170}, "fields": "pixelSize"}})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 4, "endIndex": 5},
        "properties": {"pixelSize": 36}, "fields": "pixelSize"}})
    for col in (9, 11):                                 # per-day columns: always one decimal
        reqs.append({"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 5, "endRowIndex": 5 + len(stages),
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.0"}}},
            "fields": "userEnteredFormat.numberFormat"}})
    for r0, up in ipd_colours:                          # per day since 8 Sep vs Aug
        reqs.append({"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0 + 1,
                      "startColumnIndex": 11, "endColumnIndex": 12},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.80, "green": 0.92, "blue": 0.82}
                                           if up else {"red": 0.98, "green": 0.83, "blue": 0.83}}},
            "fields": "userEnteredFormat.backgroundColor"}})
    sh.batch_update({"requests": reqs})

    print("netbox funnel: %s | vol %s" % ([(s[0], s[1]) for s in stages], vol))
    return 0


if __name__ == "__main__":
    sys.exit(main())
