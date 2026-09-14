"""
Soft Winback Pre/Post dashboard  --  refreshes every 2 hours.

Reads the 'Kushagra/Fahad Winback' tab of the P1/P2 Winbacks sheet, takes every CSP
flagged Soft Winback = Y (col J), and rebuilds the 'Pre/Post Winback' tab as a
dashboard: headline KPIs, a movement summary, then the per-CSP detail below.

GRAIN  (the important bit)
  Both Pre and Post are DISTINCT CONNECTION_ID per CSP per window.
  The main tab mixes grains -- its Pre is connection grain but its Post is offer grain
  (EXECUTION_CANDIDATE_ID), which inflates Post by up to 50% (a0b9l1: 140 offers
  vs 93 connections). Comparing those is meaningless, so this recomputes BOTH sides on
  connection grain. Post here therefore reads lower than the main tab's Post column.

WINDOWS
  Pre  = 2026-08-25 .. 2026-08-31   (last week of August)
  Post = each CSP's date of calling (col D) .. now.
  48h AGING on both sides: a lead counts only once it is 48 hours old (winback_common.AGED),
  so Post is not dragged down by leads too fresh to have been assigned or installed.

DEFINITIONS   source: PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES, ETL_CURRENT
  lead      = distinct CONNECTION_ID first seen in the window, aged >= 48h
  assigned  = MAX(EXECUTOR_ID IS NOT NULL) over that connection's in-window rows
  installed = MAX(OTP_VERIFIED_FLAG OR INSTALLATION_COMPLETED_AT IS NOT NULL OR COMPLETED_STEP>=7)
  B2A % = assigned / leads    A2I % = installed / assigned    B2I % = installed / leads

FORMATTING (reviewer)
  green = post > pre, red = post < pre, on the PERCENTAGE columns and the three pre -> post
  KPI rows. Counts are never coloured: Pre spans 7 days and Post a variable number, so
  post<pre on a count is arithmetic rather than performance. Whole-number percentages.
  "-" never "NA".
"""
import re
import sys
import datetime as dt

import gspread

from winback_common import SHEET_ID, PRE_START, PRE_END, CONN, AGED, AGING_HOURS, mb, gclient

SRC_GID = 0
OUT_TAB = "Pre/Post Winback"

INK = {"red": 0.09, "green": 0.24, "blue": 0.20}          # dark green, titles
MUTED = {"red": 0.42, "green": 0.46, "blue": 0.45}
TILE = {"red": 0.93, "green": 0.96, "blue": 0.94}
HDR = {"red": 0.17, "green": 0.33, "blue": 0.29}
GREEN = {"red": 0.80, "green": 0.92, "blue": 0.82}
RED = {"red": 0.98, "green": 0.83, "blue": 0.83}
WHITE = {"red": 1, "green": 1, "blue": 1}

# Layout is driven off the KPI count -- adding a KPI used to silently collide with the
# MOVEMENT heading below it.
N_KPIS = 8
KPI_ROW0 = 5                                               # sheet row of the first KPI
N_MOVE = 4                                                 # B2A, A2I, B2I lines + caveat
MOVE_ROW = KPI_ROW0 + N_KPIS + 1                           # blank line, then the heading
TABLE_ROW = MOVE_ROW + N_MOVE + 2                          # movement lines, blank, header
HDRS = ["CSP ID", "CSP Name", "Called", "Active\nBase", "Unique\nRecoverable",
        "Pre\nLeads", "Post\nLeads", "Aug\nInstalls/day", "Post\nInstalls/day",
        "Pre\nB2A %", "Post\nB2A %",
        "Pre\nA2I %", "Post\nA2I %", "Pre\nB2I %", "Post\nB2I %", "Hard\nWinback"]
# B2A = offered -> technician assigned ;  A2I = assigned -> installed
# B2I = offered -> installed (end to end). Hard Winback is judged on B2I: he has to take
# more AND convert them, which neither B2A nor A2I shows on its own.
NCOL = len(HDRS)
# 1-based positions within the table, resolved from HDRS so they follow column changes
COL_POST_B2A = HDRS.index("Post\nB2A %") + 1
COL_POST_A2I = HDRS.index("Post\nA2I %") + 1
COL_POST_B2I = HDRS.index("Post\nB2I %") + 1
COL_HARD = HDRS.index("Hard\nWinback") + 1
COL_POST_IPD = HDRS.index("Post\nInstalls/day") + 1

# Installs per day (user, 14-Sep). Dated by when the install COMPLETED, not by lead cohort:
#   Aug  = installs completed 1-31 Aug  / 31
#   Post = installs completed from the call date to YESTERDAY / full days since the call
AUG_START, AUG_END, AUG_DAYS = "2026-08-01", "2026-08-31", 31


def parse_ddmm(s):
    """Sheet holds four different date formats, all day-first. Returns date or None."""
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", (s or "").strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def pct(n, d):
    return "%d%%" % round(100.0 * n / d) if d else "-"


def cnt(v):
    return int(v) if v else "-"


def fetch_pre(csp_ids):
    sql = """
    WITH conn AS (%s
      WHERE f.ETL_CURRENT AND f.CSP_ID IN ('%s')
        AND TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata',f.CREATED_AT)) BETWEEN '%s' AND '%s'
      GROUP BY 1,2 %s)
    SELECT CSP_ID, COUNT(*), SUM(assigned), SUM(installed) FROM conn GROUP BY 1
    """ % (CONN, "','".join(sorted(csp_ids)), PRE_START, PRE_END, AGED)
    return {r[0]: (r[1] or 0, r[2] or 0, r[3] or 0) for r in mb(sql)}


def fetch_post(dated):
    # Metabase mangles VALUES lists -- use UNION ALL for the per-CSP windows.
    union = " UNION ALL ".join(
        "SELECT '%s' AS csp_id, DATE '%s' AS start_d" % (r["csp"], r["called"].isoformat())
        for r in dated)
    sql = """
    WITH w AS (%s),
    conn AS (%s
      JOIN w ON w.csp_id = f.CSP_ID
      WHERE f.ETL_CURRENT
        AND TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata',f.CREATED_AT)) >= w.start_d
      GROUP BY 1,2 %s)
    SELECT CSP_ID, COUNT(*), SUM(assigned), SUM(installed) FROM conn GROUP BY 1
    """ % (union, CONN, AGED)
    return {r[0]: (r[1] or 0, r[2] or 0, r[3] or 0) for r in mb(sql)}


def fetch_active(csp_ids):
    """Active base = ACTIVE_CONNECTION_COUNT, latest Quality OS snapshot (live, daily).
    Note the planning workbook's active_cx is a frozen figure and reads lower; this tab
    refreshes every 2h so the live count is the consistent choice here."""
    out = {}
    ids = sorted(csp_ids)
    for i in range(0, len(ids), 60):
        for r in mb("""
        SELECT CSP_ID, ACTIVE_CONNECTION_COUNT
        FROM PROD_DB.CSP_QUALITY_SERVICE_CSP_QUALITY_SERVICE.DAILY_METRIC_SNAPSHOTS
        WHERE _FIVETRAN_ACTIVE AND CSP_ID IN ('%s')
          AND SNAPSHOT_DATE=(SELECT MAX(SNAPSHOT_DATE)
              FROM PROD_DB.CSP_QUALITY_SERVICE_CSP_QUALITY_SERVICE.DAILY_METRIC_SNAPSHOTS
              WHERE _FIVETRAN_ACTIVE)
        """ % "','".join(ids[i:i + 60])):
            out[r[0]] = int(r[1] or 0)
    return out


def fetch_installs(dated_or_all, today):
    """-> {csp: (aug_installs, post_installs)} by install completion date (IST).
    All row versions: INSTALLATION_COMPLETED_AT, or for the few installs that never get one,
    the VALID_FROM of the first version showing the install. Attributed to the installing CSP."""
    out = {}
    rows = sorted(dated_or_all, key=lambda r: r["csp"])
    for i in range(0, len(rows), 60):
        chunk = rows[i:i + 60]
        union = " UNION ALL ".join(
            "SELECT '%s' AS csp_id, %s AS start_d" % (
                r["csp"], ("DATE '%s'" % r["called"].isoformat()) if r["called"] else "NULL::DATE")
            for r in chunk)
        for rr in mb("""
        WITH w AS (%s),
        v AS (
          SELECT f.CSP_ID, f.CONNECTION_ID,
                 TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata',
                   COALESCE(MIN(f.INSTALLATION_COMPLETED_AT),
                            MIN(IFF(f.OTP_VERIFIED_FLAG OR f.COMPLETED_STEP>=7, f.VALID_FROM, NULL))))) AS d
          FROM PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES f
          JOIN w ON w.csp_id = f.CSP_ID
          WHERE f.OTP_VERIFIED_FLAG OR f.INSTALLATION_COMPLETED_AT IS NOT NULL OR f.COMPLETED_STEP>=7
          GROUP BY 1, 2)
        SELECT v.CSP_ID,
               SUM(IFF(v.d BETWEEN '%s' AND '%s', 1, 0)),
               SUM(IFF(w.start_d IS NOT NULL AND v.d >= w.start_d AND v.d < DATE '%s', 1, 0))
        FROM v JOIN w ON w.csp_id = v.CSP_ID
        GROUP BY 1
        """ % (union, AUG_START, AUG_END, today.isoformat())):
            out[rr[0]] = (int(rr[1] or 0), int(rr[2] or 0))
    return out


def per_day(n, days):
    return round(float(n) / days, 2) if days else "-"


def txt(v, size=10, bold=False, colour=None, align="LEFT", bg=None, wrap=False):
    f = {"textFormat": {"fontSize": size, "bold": bold,
                        "foregroundColor": colour or {"red": 0, "green": 0, "blue": 0}},
         "horizontalAlignment": align, "verticalAlignment": "MIDDLE"}
    if bg:
        f["backgroundColor"] = bg
    if wrap:
        f["wrapStrategy"] = "WRAP"
    return f


def main():
    gc = gclient()
    sh = gc.open_by_key(SHEET_ID)
    src = sh.get_worksheet_by_id(SRC_GID)

    rows = []
    for r in src.get_values("B4:AB2000"):
        def g(k):
            return r[k].strip() if len(r) > k else ""
        if not g(0) or not g(8).upper().startswith("Y"):
            continue
        rows.append(dict(csp=g(0), name=g(1), date_raw=g(2), recoverable=g(5),
                         soft=g(8), hard=g(26), called=parse_ddmm(g(2))))
    if not rows:
        print("no soft-winback rows found; aborting without writing")
        return 1

    pre = fetch_pre({r["csp"] for r in rows})
    active = fetch_active({r["csp"] for r in rows})
    dated = [r for r in rows if r["called"]]
    post = fetch_post(dated) if dated else {}
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
    inst = fetch_installs(rows, today)

    body, colours = [], []
    agg = {"pl": 0, "pa": 0, "pi": 0, "ql": 0, "qa": 0, "qi": 0,
           "aug": 0, "aug_days": 0, "pinst": 0, "pdays": 0}
    move = {"asg": [0, 0, 0], "ins": [0, 0, 0], "b2i": [0, 0, 0]}            # improved, declined, flat/none
    recov_total = 0
    active_total = 0
    hard_count = [0]

    for r in sorted(rows, key=lambda x: (x["called"] or dt.date(2099, 1, 1), x["name"])):
        pl, pa, pi = pre.get(r["csp"], (0, 0, 0))
        pre_asg, pre_ins = pct(pa, pl), pct(pi, pa)
        agg["pl"] += pl; agg["pa"] += pa; agg["pi"] += pi
        try:
            recov_total += int(r["recoverable"])
        except (ValueError, TypeError):
            pass
        active_total += active.get(r["csp"], 0)

        pre_b2i = pct(pi, pl)                       # offered -> installed, end to end
        aug_n, post_n = inst.get(r["csp"], (0, 0))
        aug_ipd = per_day(aug_n, AUG_DAYS)
        agg["aug"] += aug_n; agg["aug_days"] += AUG_DAYS
        if r["called"]:
            ql, qa, qi = post.get(r["csp"], (0, 0, 0))
            post_asg, post_ins, post_b2i = pct(qa, ql), pct(qi, qa), pct(qi, ql)
            post_l, post_t = cnt(ql), cnt(qa)
            agg["ql"] += ql; agg["qa"] += qa; agg["qi"] += qi
            days = max((today - r["called"]).days, 0)     # full days, call date .. yesterday
            post_ipd = per_day(post_n, days)
            if days:
                agg["pinst"] += post_n; agg["pdays"] += days
        else:
            post_asg = post_ins = post_b2i = post_l = post_t = post_ipd = "-"

        # Hard winback = post B2I beats pre B2I. B2I is the end-to-end rate, so it only
        # moves if he both accepted more work and converted it. Computed here, not read
        # from the tracker's own Hard Winback column.
        hard = "-"
        if pre_b2i != "-" and post_b2i != "-":
            hard = "Yes" if float(post_b2i.rstrip("%")) > float(pre_b2i.rstrip("%")) else "-"
        if hard == "Yes":
            hard_count[0] += 1

        body.append([r["csp"], r["name"], r["date_raw"] or "-",
                     cnt(active.get(r["csp"], 0)),
                     cnt(int(r["recoverable"]) if r["recoverable"].isdigit() else 0),
                     cnt(pl), post_l, aug_ipd, post_ipd, pre_asg, post_asg,
                     pre_ins, post_ins, pre_b2i, post_b2i, hard])
        rown = TABLE_ROW + len(body)

        def band(a_s, b_s, col, bucket):
            if a_s == "-" or b_s == "-":
                move[bucket][2] += 1
                return
            a, b = float(a_s.rstrip("%")), float(b_s.rstrip("%"))
            if b > a:
                colours.append((rown, col, GREEN)); move[bucket][0] += 1
            elif b < a:
                colours.append((rown, col, RED)); move[bucket][1] += 1
            else:
                move[bucket][2] += 1

        # Look the columns up rather than hardcoding -- adding/removing a column has
        # silently shifted these twice, painting the wrong cells.
        band(pre_asg, post_asg, COL_POST_B2A, "asg")
        band(pre_ins, post_ins, COL_POST_A2I, "ins")
        band(pre_b2i, post_b2i, COL_POST_B2I, "b2i")
        if aug_ipd != "-" and post_ipd != "-" and post_ipd != aug_ipd:   # per day IS comparable
            colours.append((rown, COL_POST_IPD, GREEN if post_ipd > aug_ipd else RED))
        if hard == "Yes":                      # green flag on the verdict itself
            colours.append((rown, COL_HARD, GREEN))

    # TOTAL row -- counts summed; % columns pooled (sum of numerators / sum of denominators),
    # never an average of the per-CSP rates. Installs/day = SUM of the rows above (user,
    # 14-Sep: "do total of 21 to 87") -> the cohort's installs per day; written as a live
    # =SUM formula over the body rows.
    first_b, last_b = TABLE_ROW + 1, TABLE_ROW + len(body)
    ipd_col = lambda h: gspread.utils.rowcol_to_a1(1, 2 + HDRS.index(h)).rstrip("1")
    f_aug = "=SUM(%s%d:%s%d)" % (ipd_col("Aug\nInstalls/day"), first_b, ipd_col("Aug\nInstalls/day"), last_b)
    f_post = "=SUM(%s%d:%s%d)" % (ipd_col("Post\nInstalls/day"), first_b, ipd_col("Post\nInstalls/day"), last_b)
    t_aug = round(sum(b[7] for b in body if b[7] != "-"), 2)
    t_post = round(sum(b[8] for b in body if b[8] != "-"), 2)
    total = ["Total", "%d CSPs" % len(rows), "-", active_total, recov_total,
             agg["pl"], agg["ql"], t_aug, t_post,
             pct(agg["pa"], agg["pl"]), pct(agg["qa"], agg["ql"]),
             pct(agg["pi"], agg["pa"]), pct(agg["qi"], agg["qa"]),
             pct(agg["pi"], agg["pl"]), pct(agg["qi"], agg["ql"]), hard_count[0]]
    total_row = TABLE_ROW + len(body) + 1
    for pre_s, post_s, col in ((t_aug, t_post, COL_POST_IPD),
                               (total[9], total[10], COL_POST_B2A),
                               (total[11], total[12], COL_POST_A2I),
                               (total[13], total[14], COL_POST_B2I)):
        if pre_s == "-" or post_s == "-":
            continue
        a = float(str(pre_s).rstrip("%")); b = float(str(post_s).rstrip("%"))
        if a != b:
            colours.append((total_row, col, GREEN if b > a else RED))

    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    try:
        ws = sh.worksheet(OUT_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=OUT_TAB, rows=TABLE_ROW + len(body) + 20, cols=NCOL + 3)

    # A reviewer added a filter sorted by Hard Winback (14-Sep). Remember its sort / filter
    # columns BY HEADER NAME, drop it for the rebuild, and put it back over the new table at the
    # end -- otherwise a column added to HDRS leaves it pointing at the wrong columns.
    keep = None
    meta = sh.fetch_sheet_metadata({"fields": "sheets(properties(sheetId),basicFilter)"})
    bf = next((s.get("basicFilter") for s in meta["sheets"]
               if s["properties"]["sheetId"] == ws.id), None)
    if bf:
        col_b = ws.col_values(2)
        hr = next((i for i, v in enumerate(col_b) if v.strip() == "CSP ID"), None)
        old_hdr = ws.row_values(hr + 1) if hr is not None else []

        def remap(ci):
            h = old_hdr[ci] if ci < len(old_hdr) else ""
            return 1 + HDRS.index(h) if h in HDRS else None

        keep = ([dict(s, dimensionIndex=remap(s["dimensionIndex"])) for s in bf.get("sortSpecs", [])
                 if remap(s["dimensionIndex"]) is not None],
                [dict(f, columnIndex=remap(f["columnIndex"])) for f in bf.get("filterSpecs", [])
                 if remap(f["columnIndex"]) is not None])
        # NOT cleared here: if the run fails later the reviewer's filter must survive.
        # setBasicFilter at the end replaces it in one step.
    ws.clear()
    # ws.clear() wipes VALUES ONLY -- old backgrounds/merges survive and pile up run to run.
    # Reset the whole canvas first, otherwise last run's red/green lands in the KPI area.
    wipe = {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": max(ws.row_count, 400),
            "startColumnIndex": 0, "endColumnIndex": max(ws.col_count, 30)}
    sh.batch_update({"requests": [
        {"unmergeCells": {"range": wipe}},
        {"repeatCell": {"range": wipe,
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                            "horizontalAlignment": "LEFT",
                            "wrapStrategy": "OVERFLOW_CELL",
                            "textFormat": {"bold": False, "fontSize": 10,
                                           "foregroundColor": {"red": 0, "green": 0, "blue": 0}}}},
                        "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,"
                                  "wrapStrategy,textFormat)"}},
    ]})

    kpis = [
        ("CSPs on soft winback", str(len(rows))),
        ("Called so far", str(len(dated))),
        ("Active base", "{:,}".format(active_total)),
        ("Recoverable leads", "{:,}".format(recov_total)),
        ("Hard winback", str(hard_count[0])),
        ("B2A %  pre \u2192 post", "%s \u2192 %s" % (pct(agg["pa"], agg["pl"]), pct(agg["qa"], agg["ql"]))),
        ("A2I %  pre \u2192 post", "%s \u2192 %s" % (pct(agg["pi"], agg["pa"]), pct(agg["qi"], agg["qa"]))),
        ("B2I %  pre → post", "%s → %s" % (pct(agg["pi"], agg["pl"]), pct(agg["qi"], agg["ql"]))),
    ]
    # the three pre -> post rows get the same green/red rule as the table
    kpi_move = {5: (pct(agg["pa"], agg["pl"]), pct(agg["qa"], agg["ql"])),
                6: (pct(agg["pi"], agg["pa"]), pct(agg["qi"], agg["qa"])),
                7: (pct(agg["pi"], agg["pl"]), pct(agg["qi"], agg["ql"]))}
    pre_lab = "%s - %s" % (dt.date.fromisoformat(PRE_START).strftime("%d %b"),
                           dt.date.fromisoformat(PRE_END).strftime("%d %b %Y"))

    grid = [[""] * NCOL for _ in range(TABLE_ROW - 2)]
    grid[0][0] = "SOFT WINBACK  ·  PRE vs POST"
    grid[1][0] = ("Pre = %s   ·   Post = date called → now   ·   leads aged ≥ %dh on both sides   ·   "
                  "distinct connections   ·   refreshed %s IST"
                  % (pre_lab, AGING_HOURS, stamp.strftime("%d %b %Y, %H:%M")))
    if len(kpis) != N_KPIS:
        raise RuntimeError("N_KPIS is %d but %d KPIs were built -- layout would collide"
                           % (N_KPIS, len(kpis)))
    for i, (lab, val) in enumerate(kpis):          # label in B:C, value in D:E
        grid[KPI_ROW0 - 2 + i][0] = lab
        grid[KPI_ROW0 - 2 + i][2] = val
    mv = MOVE_ROW - 2                                      # grid index of the heading
    grid[mv][0] = "MOVEMENT SINCE THE CALL"
    grid[mv + 1][0] = "B2A %%   ▲ %d improved    ▼ %d declined    – %d flat / no post data" % tuple(move["asg"])
    grid[mv + 2][0] = "A2I %%   ▲ %d improved    ▼ %d declined    – %d flat / no post data" % tuple(move["ins"])
    grid[mv + 3][0] = "B2I %%   ▲ %d improved    ▼ %d declined    – %d flat / no post data" % tuple(move["b2i"])
    grid[mv + 4][0] = ("Read with care: Pre is a 7-day window (%s leads); Post runs from each call to "
                       "%dh ago (%s leads), so a CSP called in the last 2 days shows no Post yet. "
                       "Rates are comparable, but Post sits on small denominators. Installs/day: "
                       "Aug = August installs / 31; Post = installs from the call date to yesterday / "
                       "days since the call. Total row: counts and installs/day are sums of the "
                       "rows; %% columns are pooled (sum / sum), never an average of averages."
                       % ("{:,}".format(agg["pl"]), AGING_HOURS, "{:,}".format(agg["ql"])))

    ws.update(values=grid + [HDRS] + body + [total], range_name="B2", value_input_option="RAW")
    # the two installs/day totals as live SUM formulas over the body rows
    ws.update(values=[[f_aug, f_post]],
              range_name="%s%d:%s%d" % (ipd_col("Aug\nInstalls/day"), total_row,
                                        ipd_col("Post\nInstalls/day"), total_row),
              value_input_option="USER_ENTERED")

    sid = ws.id
    last = TABLE_ROW + len(body)          # last body row -- the filter stops here
    tot_r = last + 1                      # total row sits outside the filter, so sorting skips it
    reqs = [
        {"updateSheetProperties": {
            "properties": {"sheetId": sid,
                           "gridProperties": {"hideGridlines": True,
                                              "frozenRowCount": 0,          # no freeze -- user asked 14-Sep
                                              "frozenColumnCount": 0}},
            "fields": "gridProperties(hideGridlines,frozenRowCount,frozenColumnCount)"}},
        # title + subtitle
        {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + NCOL},
                        "mergeType": "MERGE_ROWS"}},
        {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 3,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + NCOL},
                        "mergeType": "MERGE_ROWS"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + NCOL},
                        "cell": {"userEnteredFormat": txt("", 15, True, INK)},
                        "fields": "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 3,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + NCOL},
                        "cell": {"userEnteredFormat": txt("", 9, False, MUTED)},
                        "fields": "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment)"}},
        # section heading
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": MOVE_ROW - 1, "endRowIndex": MOVE_ROW,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + NCOL},
                        "cell": {"userEnteredFormat": txt("", 10, True, INK)},
                        "fields": "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": MOVE_ROW, "endRowIndex": MOVE_ROW + N_MOVE,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + NCOL},
                        "cell": {"userEnteredFormat": txt("", 10, False)},
                        "fields": "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment)"}},
        # table header
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": TABLE_ROW - 1, "endRowIndex": TABLE_ROW,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + NCOL},
                        "cell": {"userEnteredFormat": txt("", 9, True, WHITE, "CENTER", HDR, True)},
                        "fields": "userEnteredFormat(textFormat,horizontalAlignment,"
                                  "verticalAlignment,backgroundColor,wrapStrategy)"}},
        # body: numeric block centred
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": TABLE_ROW, "endRowIndex": tot_r,
                                  "startColumnIndex": 4, "endColumnIndex": 1 + NCOL},
                        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                        "fields": "userEnteredFormat.horizontalAlignment"}},
        # total row: bold on a light band
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": tot_r - 1, "endRowIndex": tot_r,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + NCOL},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                       "backgroundColor": TILE}},
                        "fields": "userEnteredFormat(textFormat.bold,backgroundColor)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": TABLE_ROW, "endRowIndex": tot_r,
                                  "startColumnIndex": 1 + HDRS.index("Aug\nInstalls/day"),
                                  "endColumnIndex": 1 + COL_POST_IPD},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER",
                                                                        "pattern": "0.00"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 34}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": KPI_ROW0 - 1,
                      "endIndex": KPI_ROW0 - 1 + N_KPIS},
            "properties": {"pixelSize": 24}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 26}, "fields": "pixelSize"}},
    ]
    widths = {"CSP ID": 88, "CSP Name": 178, "Called": 88, "Active\nBase": 76,
              "Unique\nRecoverable": 90, "Hard\nWinback": 84}
    for idx, w in enumerate(widths.get(h, 64 if "Leads" in h else 80 if "Installs" in h else 76)
                            for h in HDRS):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": 1 + idx, "endIndex": 2 + idx},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    reqs += [
    ]
    # KPI tiles
    for i in range(len(kpis)):
        r0 = KPI_ROW0 - 1 + i                       # 0-based sheet row of this KPI
        reqs += [
            {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0 + 1,
                                      "startColumnIndex": 1, "endColumnIndex": 3},
                            "mergeType": "MERGE_ROWS"}},
            {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0 + 1,
                                      "startColumnIndex": 3, "endColumnIndex": 5},
                            "mergeType": "MERGE_ROWS"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0 + 1,
                                      "startColumnIndex": 1, "endColumnIndex": 3},
                            "cell": {"userEnteredFormat": txt("", 10, False, MUTED, "LEFT", TILE)},
                            "fields": "userEnteredFormat(textFormat,horizontalAlignment,"
                                      "verticalAlignment,backgroundColor)"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0 + 1,
                                      "startColumnIndex": 3, "endColumnIndex": 5},
                            "cell": {"userEnteredFormat": txt("", 12, True, INK, "LEFT", TILE)},
                            "fields": "userEnteredFormat(textFormat,horizontalAlignment,"
                                      "verticalAlignment,backgroundColor)"}},
        ]
        a_s, b_s = kpi_move.get(i, ("-", "-"))
        if a_s != "-" and b_s != "-" and a_s != b_s:
            up = float(b_s.rstrip("%")) > float(a_s.rstrip("%"))
            reqs.append({"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0 + 1,
                          "startColumnIndex": 3, "endColumnIndex": 5},
                "cell": {"userEnteredFormat": {"backgroundColor": GREEN if up else RED}},
                "fields": "userEnteredFormat.backgroundColor"}})
    for rown, col, bg in colours:
        reqs.append({"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": rown - 1, "endRowIndex": rown,
                      "startColumnIndex": col - 1 + 1, "endColumnIndex": col + 1},
            "cell": {"userEnteredFormat": {"backgroundColor": bg}},
            "fields": "userEnteredFormat.backgroundColor"}})
    sh.batch_update({"requests": reqs})

    if keep is not None:                    # restore the reviewer's filter / sort, last
        sh.batch_update({"requests": [{"setBasicFilter": {"filter": {
            "range": {"sheetId": sid, "startRowIndex": TABLE_ROW - 1, "endRowIndex": last,
                      "startColumnIndex": 1, "endColumnIndex": 1 + NCOL},
            "sortSpecs": keep[0], "filterSpecs": keep[1]}}}]})

    print("dashboard rebuilt: %d CSPs (%d called, %d awaiting), %d cells coloured | "
          "B2A %s->%s, A2I %s->%s, B2I %s->%s, hard winback %d | leads pre %d post %d (aged %dh)"
          % (len(rows), len(dated), len(rows) - len(dated), len(colours),
             pct(agg["pa"], agg["pl"]), pct(agg["qa"], agg["ql"]),
             pct(agg["pi"], agg["pa"]), pct(agg["qi"], agg["qa"]),
             pct(agg["pi"], agg["pl"]), pct(agg["qi"], agg["ql"]),
             hard_count[0], agg["pl"], agg["ql"], AGING_HOURS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
