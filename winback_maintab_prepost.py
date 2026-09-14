"""
Rebuild T..AA (Pre/Post funnel) on the MAIN 'Kushagra/Fahad Winback' tab.

SCOPE
  Rebuilds T..AA for every row that is NOT Soft Winback = Y.
  Soft-winback CSPs moved to the 'Pre/Post Winback' tab (winback_prepost.py) per the reviewer's
  09-Sep instruction ("take the pre post in a separate dashboard and remove from here"),
  so their T..AA cells are CLEARED here on every run.

WHY THIS EXISTS
  The tab's Pre was connection grain but its Post was offer grain
  (EXECUTION_CANDIDATE_ID), inflating Post by up to 50% -- a0b9l1 read 17 Post leads
  where distinct connections give 12. Pre vs Post was therefore not a like-for-like
  comparison. This puts BOTH sides on DISTINCT CONNECTION_ID.

WINDOWS
  Pre = 25-31 Aug 2026 (last week of August); Post = date of calling -> now.
  48h AGING on both sides (reviewer, 12-Sep): a lead counts only once it is 48 hours old, so
  Post is not dragged down by leads too fresh to have been assigned or installed.

FORMATTING (reviewer, 09-Sep)
  * green where post > pre, red where post < pre -- on the two % columns (W, AA) only.
    Counts are not coloured: Pre spans 7 days and Post a variable number, so post<pre on a
    count is arithmetic rather than performance.
  * "-" everywhere a value cannot be computed. Never "NA".

SAFETY
  Writes a timestamped JSON backup of the whole T4:AA<last> block before touching it.
  The 8-column block (Pre Lead · Pre Asg% · Post Lead · Post Asg% · Pre Tech · Pre Inst% ·
  Post Tech · Post Inst%) is FOUND BY ITS HEADERS in row 3 each run -- it was T:AA until
  14-Sep, U:AB after 'M1 offered' was inserted at L. Aborts if it is not found exactly once or
  if B / D / J (CSP ID, date, Soft Winback) have moved. Data from row 4.
"""
import glob
import json
import os
import re
import sys
import datetime as dt

import gspread

from winback_common import SHEET_ID, PRE_START, PRE_END, CONN, AGED, mb, gclient

SRC_GID = 0
# Backups sit next to the script locally. In GitHub Actions the runner is discarded, so the
# workflow uploads this directory as a run artifact instead.
BACKUP_DIR = os.environ.get("WINBACK_BACKUP_DIR") or os.path.dirname(os.path.abspath(__file__))

FIRST_DATA_ROW = 4
# Pre/Post block columns are resolved from header row 3 at run time (they move).

GREEN = {"red": 0.85, "green": 0.94, "blue": 0.85}
RED = {"red": 0.98, "green": 0.85, "blue": 0.85}


def parse_ddmm(s):
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
    # whole numbers only -- decimals made the block hard to scan
    return "%d%%" % round(100.0 * n / d) if d else "-"


def cnt(v):
    # real integers so the column right-aligns and sorts; "-" when there is nothing
    return int(v) if v else "-"


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_pre(csp_ids):
    out = {}
    for chunk in chunked(sorted(csp_ids), 60):     # Metabase truncates ~2000 rows; chunk the IN list
        sql = """
        WITH conn AS (%s
          WHERE f.ETL_CURRENT AND f.CSP_ID IN ('%s')
            AND TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata',f.CREATED_AT)) BETWEEN '%s' AND '%s'
          GROUP BY 1,2 %s)
        SELECT CSP_ID, COUNT(*), SUM(assigned), SUM(installed) FROM conn GROUP BY 1
        """ % (CONN, "','".join(chunk), PRE_START, PRE_END, AGED)
        for r in mb(sql):
            out[r[0]] = (r[1] or 0, r[2] or 0, r[3] or 0)
    return out


def fetch_post(dated):
    if not dated:
        return {}
    out = {}
    for chunk in chunked(sorted(dated), 60):
        union = " UNION ALL ".join(
            "SELECT '%s' AS csp_id, DATE '%s' AS start_d" % (c, d.isoformat()) for c, d in chunk
        )
        sql = """
        WITH w AS (%s),
        conn AS (%s
          JOIN w ON w.csp_id = f.CSP_ID
          WHERE f.ETL_CURRENT
            AND TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata',f.CREATED_AT)) >= w.start_d
          GROUP BY 1,2 %s)
        SELECT CSP_ID, COUNT(*), SUM(assigned), SUM(installed) FROM conn GROUP BY 1
        """ % (union, CONN, AGED)
        for r in mb(sql):
            out[r[0]] = (r[1] or 0, r[2] or 0, r[3] or 0)
    return out


def main():
    gc = gclient()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.get_worksheet_by_id(SRC_GID)

    # Locate the 8-column Pre/Post block by its headers -- columns get inserted to the left of
    # it (14-Sep: 'M1 offered' at L moved it from T:AA to U:AB). Soft Winback and CSP ID must
    # still be where the row reads below expect them.
    row3 = [h.strip() for h in ws.get_values("A3:BZ3")[0]]
    expect = ["Pre Lead count", "Pre", "Post Lead count", "Post",
              "Pre Tech assigned count", "Pre", "Post Tech assigned count", "Post"]
    starts = [i for i in range(len(row3)) if row3[i:i + 8] == expect]
    if len(starts) != 1 or row3[1] != "CSP ID" or row3[3] != "Date of calling / visit" \
            or row3[9] != "Soft Winback (Y/N)":
        print("ABORT: header row 3 is %r -- Pre/Post block not found exactly once, or B/D/J moved."
              % row3)
        return 1
    col_t = starts[0] + 1                                  # 1-based first column of the block
    col_w, col_aa = col_t + 3, col_t + 7                   # the two coloured % columns
    first_l = gspread.utils.rowcol_to_a1(1, col_t).rstrip("1")
    last_l = gspread.utils.rowcol_to_a1(1, col_t + 7).rstrip("1")

    grid = ws.get_values("B4:AB2000")
    n = len(grid)

    def g(r, i):
        return r[i].strip() if len(r) > i else ""

    targets, soft_idx, all_csps = [], [], set()
    for idx, r in enumerate(grid):
        csp = g(r, 0)
        if not csp:
            continue
        if g(r, 8).upper().startswith("Y"):
            # soft winback -> Pre/Post lives on its own tab now, clear it from here
            soft_idx.append(idx)
            continue
        targets.append(dict(idx=idx, row=idx + FIRST_DATA_ROW, csp=csp,
                            called=parse_ddmm(g(r, 2))))
        all_csps.add(csp)

    if not targets:
        print("no non-soft rows found; aborting without writing")
        return 1

    pre = fetch_pre(all_csps)
    post = fetch_post([(t["csp"], t["called"]) for t in targets if t["called"]])

    # current block, so untouched rows are rewritten byte-identical
    block = ws.get_values("%s4:%s%d" % (first_l, last_l, FIRST_DATA_ROW + n - 1))
    block = [(row + [""] * 8)[:8] for row in block] + [[""] * 8] * (n - len(block))

    backup = {"taken": dt.datetime.now().isoformat(), "range": "%s4:%s%d" % (first_l, last_l, FIRST_DATA_ROW + n - 1),
              "values": block}
    bpath = os.path.join(BACKUP_DIR, "maintab_TAA_backup_%s.json"
                         % dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(bpath, "w", encoding="utf-8") as fh:
        json.dump(backup, fh, indent=1)

    # runs 12x/day -- keep the most recent 30 backups, drop the rest
    old = sorted(glob.glob(os.path.join(BACKUP_DIR, "maintab_TAA_backup_*.json")))
    for stale in old[:-30]:
        try:
            os.remove(stale)
        except OSError:
            pass

    colours, changed = [], 0
    for t in targets:
        pl, pa, pi = pre.get(t["csp"], (0, 0, 0))
        pre_asg, pre_ins = pct(pa, pl), pct(pi, pa)
        if t["called"]:
            ql, qa, qi = post.get(t["csp"], (0, 0, 0))
            post_asg, post_ins = pct(qa, ql), pct(qi, qa)
            post_l, post_t = cnt(ql), cnt(qa)
        else:
            post_asg = post_ins = post_l = post_t = "-"

        new = [cnt(pl), pre_asg, post_l, post_asg,
               cnt(pa), pre_ins, post_t, post_ins]
        if block[t["idx"]] != new:
            changed += 1
        block[t["idx"]] = new

        def band(a_s, b_s, col):
            if a_s == "-" or b_s == "-":
                return
            a, b = float(a_s.rstrip("%")), float(b_s.rstrip("%"))
            if b > a:
                colours.append((t["row"], col, GREEN))
            elif b < a:
                colours.append((t["row"], col, RED))

        band(pre_asg, post_asg, col_w)
        band(pre_ins, post_ins, col_aa)

    # soft-winback rows: Pre/Post now lives on the 'Pre/Post Winback' tab, so clear it here
    for idx in soft_idx:
        block[idx] = [""] * 8

    last = FIRST_DATA_ROW + n - 1
    ws.update(values=block, range_name="%s4:%s%d" % (first_l, last_l, last), value_input_option="RAW")

    # one uniform look for the whole block: centred, so ints and "-" line up
    reqs = [{"repeatCell": {
        "range": {"sheetId": ws.id, "startRowIndex": FIRST_DATA_ROW - 1, "endRowIndex": last,
                  "startColumnIndex": col_t - 1, "endColumnIndex": col_t + 7},
        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER",
                                       "backgroundColor": {"red": 1, "green": 1, "blue": 1}}},
        "fields": "userEnteredFormat(horizontalAlignment,backgroundColor)"}}]
    for r, c, bg in colours:
        reqs.append({"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": r - 1, "endRowIndex": r,
                      "startColumnIndex": c - 1, "endColumnIndex": c},
            "cell": {"userEnteredFormat": {"backgroundColor": bg}},
            "fields": "userEnteredFormat.backgroundColor"}})
    sh.batch_update({"requests": reqs})

    dated = sum(1 for t in targets if t["called"])
    print("main tab: %d non-soft rows rebuilt (%d changed), %d dated, %d cells coloured; "
          "%d soft-winback rows CLEARED (T:AA). backup -> %s"
          % (len(targets), changed, dated, len(colours), len(soft_idx), bpath))
    return 0


if __name__ == "__main__":
    sys.exit(main())
