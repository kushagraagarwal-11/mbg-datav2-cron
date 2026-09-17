"""
Fill the 'Daily Dashboard' tab (gid 1292308429) of the P1/P2 Winbacks sheet.

LAYOUT (reviewer, 14-Sep): column B = bucket, column C = KF / Field, then the numbers.
Rows are found by the (bucket, KF/Field) PAIR, never by row number -- the reviewer adds and
moves rows. B is merged down a KF/Field pair, so a blank B inherits the bucket above.
  89 Winback KF/Field · Exit Field · Non_compliant - · P6_Fallouts KF/Field · 750 Opt out Field ·
  Growth Team KF/Field · Wrong enforcement KF · P1 KF/Field · P2 KF/Field · Total

BUCKETS  (tracker = 'Kushagra/Fahad Winback' tab)
  KF vs Field  -> tracker column F 'Winback and Visiting': Visiting = Field, else KF.
                  Split this way: 89 Winback (Category 'Winback _ 89 CSPs' or blank),
                  P6_Fallouts, Growth Team, P1, P2.
  Fixed mode   -> Exit = Field (visited), 750 Opt out = Field, Wrong enforcement = KF
                  (whatever column F says).
  Non_compliant-> the 'Non_compliant list' tab minus CSPs any tracker category already owns,
                  all on BULK_DATE (8 Sep).
  PRECEDENCE: the tracker Category wins, then Non_compliant. No CSP is counted twice.
  Categories 'Previous Good installers', 'Non-compliant blocked' and 'Delivery receipt pending'
  do NOT own a CSP: a
  Non_compliant-list CSP under them stays in the Non_compliant row (same rule in the sheet formulas).
  'Wrong enforcement' was 'Ghost Install' until 14-Sep; the old name is still accepted.

TABLE 1  per bucket: D = total, E.. = per date      *** FORMULA-DRIVEN IN THE SHEET ***
  CSPs unlocked, counted on their date of calling / visit, where Soft Winback = Y.
  Non_compliant: all on BULK_DATE. The sheet computes this itself from the tracker and the
  'Non_compliant list' tab; this script never writes it and only prints a cross-check.

TABLE 2  per bucket, pooled (sum numerators / sum denominators)
  D/E  B2A % pre/post     bookings offered -> technician assigned
  F/G  A2I % pre/post     assigned -> installed
  H/I  B2I % pre/post     bookings offered -> installed, end to end (user, 16-Sep)
  (order applied / delivered, netbox and applied-CSP columns moved to the Netbox Order
   Funnel tab on 15-Sep -- see netbox_funnel.py)
  Pre  = 2026-08-25 .. 2026-08-31 (last week of August)
  Post = each CSP's date of calling / visit -> now (Non_compliant: 8 Sep).
  Leads are aged 48h on both sides (winback_common.AGED). Orders are not aged.
  Grain = DISTINCT CONNECTION_ID per CSP per window, matching the Pre/Post Winback tab.

Colours: green where post > pre, red where post < pre, on every pre/post pair.

TABLE 3  field visits by agent (user, 16-Sep) -- below Table 2, found by its title in column B
  Source: 'Willing to Exit CSPs' sheet; agent = Owner Name, date = Date of Visit (read with the
  same parser as sync_exit_visits.py, so planned/future visits are not counted).
  Columns: one pair per day from 14 Sep to today -- Visits | Soft winback -- then a Total pair.
  A visit counts only once its "Soft Winback (Y/N)" is filled in (user, 16-Sep: a date with a
  blank outcome is a planned / unreported visit). Soft winback = that cell is Yes.
  Colour (user, 16-Sep): each agent's per-day Visits cell is green when > 1, red otherwise
  (Total row / Total column uncoloured).

TABLE 4  contacted x soft winback, 2x2 (user, 16-Sep) -- between Table 1 and Table 2 (user moved it
  there): found by its title in column B; if the title is missing it goes in the T4_ROWS rows that end
  two rows above Table 2's header, only if those cells are empty. Never grows: always T4_ROWS rows.
  Cohort: Table 1's TOF without Non_compliant = every CSP a tracker bucket owns (distinct CSP).
  Rows    Contacted: Yes = tracker 'Date of calling / visit' filled (called or visited), No = blank
  Columns Soft winback: Yes = tracker 'Soft Winback (Y/N)' = Y, No = anything else
  Each box: CSP count, and B2A / A2I / B2I pre -> post, pooled over the box's CSPs.
  Every CSP count carries its active base in brackets (user, 17-Sep) = sum of the latest Quality OS
  ACTIVE_CONNECTION_COUNT, the Pre/Post Winback tab's "Active base". Column headers and row labels
  carry their totals (soft winback Yes / No, contacted Yes / No).
  Pre  = 2026-09-01 .. 2026-09-07 (first week of September -- NOT Table 2's August week)
  Post = contact date -> now; a CSP never contacted has no contact date, so his post starts on
         8 Sep (campaign start, the day after the pre week). Leads aged 48h, as in Table 2.
"""
import re
import sys
import datetime as dt

import gspread

from winback_common import (SHEET_ID, PRE_START, PRE_END, CONN, AGED, mb, gclient,
                            load_noncompliant, stamp)

OUT_GID = 1292308429
BULK_DATE = dt.date(2026, 9, 8)                 # Non_compliant: done in one go, first date

NC = ("Non_compliant", "-")
ORDER = [("89 Winback", "KF"), ("89 Winback", "Field"), ("Exit", "Field"), NC,
         ("P6_Fallouts", "KF"), ("P6_Fallouts", "Field"), ("750 Opt out", "Field"),
         ("Growth Team", "KF"), ("Growth Team", "Field"), ("Wrong enforcement", "KF"),
         ("P1", "KF"), ("P1", "Field"), ("P2", "KF"), ("P2", "Field")]
CAT2BUCKET = {"Winback _ 89 CSPs": "89 Winback",
              "": "89 Winback",                # undated-category rows in the 89 block
              "P1": "P1", "P2": "P2",          # the P1/P2 cohort, tier from Sheet5 'priority'
              "Exit": "Exit", "P6_Fallouts": "P6_Fallouts",
              "750 opt out": "750 Opt out", "Growth Team": "Growth Team",
              "Wrong enforcement": "Wrong enforcement",
              "Ghost Install": "Wrong enforcement",      # old name
              "Previous Good installers": None,
              # a Non_compliant-list CSP the team works from the tracker (16-Sep: Giganet, hoarding
              # blocked) -- stays in the Non_compliant row, so it must not "own" the CSP
              "Non-compliant blocked": None,
              # auto-added by netbox_funnel.py: courier delivered, app receipt not accepted (17-Sep)
              "Delivery receipt pending": None}
NOT_OWNING = {"Previous Good installers", "Non-compliant blocked", "Delivery receipt pending"}
# buckets whose KF / Field is fixed; every other bucket is split by tracker column F
FIXED_MODE = {"Exit": "Field", "750 Opt out": "Field", "Wrong enforcement": "KF"}
T2_COLS = "D%d:I%d"
NC_TAB = "Non_compliant list"
GREEN = {"red": 0.80, "green": 0.92, "blue": 0.82}
RED = {"red": 0.98, "green": 0.83, "blue": 0.83}
WHITE = {"red": 1, "green": 1, "blue": 1}


VISIT_SHEET = "1WsADMo2slH0VZhCdBbAg2ortl6_AfTs-hSBvTBEoRto"
VISITS_FROM = dt.date(2026, 9, 14)
T3_TITLE = "Field visits by agent (Willing to Exit sheet)"
# region owners, not field agents -- left out of Table 3 entirely (user, 16-Sep)
T3_EXCLUDE = {"mumbai", "up east", "up west"}
# visits logged under one owner that belong to another agent (user, 16-Sep: Hammad -> Shoib)
T3_MERGE = {"hammad": "Shoib"}


def visits_table(gc, sh, ws, t2_total):
    """Rewrite Table 3 under Table 2. Returns printable summary."""
    from sync_exit_visits import parse_visit
    from winback_common import IST
    today = dt.datetime.now(IST).date()
    src = gc.open_by_key(VISIT_SHEET).get_worksheet_by_id(0).get_values("A1:Z2000")
    hdr = [c.strip().lower() for c in src[0]]

    def col(name):
        hits = [i for i, h in enumerate(hdr) if h == name]
        if len(hits) != 1:
            raise RuntimeError("visit sheet header %r found %d times" % (name, len(hits)))
        return hits[0]
    # CSP ID is always column A (its header cell gets pasted over -- 17-Sep); the rest by header
    i_id, i_own, i_dt, i_sw = 0, col("owner name"), col("date of visit"), col("soft winback (y/n)")
    days = [VISITS_FROM + dt.timedelta(days=k) for k in range((today - VISITS_FROM).days + 1)]
    agents, cnt = [], {}
    for r in src[1:]:
        g = lambda i: r[i].strip() if len(r) > i else ""
        if not g(i_id):
            continue
        a = g(i_own) or "(no owner)"
        if a.lower() in T3_EXCLUDE:
            continue
        a = T3_MERGE.get(a.lower(), a)
        if a not in agents:
            agents.append(a)
        d, _ = parse_visit(g(i_dt), today)
        if d is None or d < VISITS_FROM or not g(i_sw):
            continue
        v = cnt.setdefault((a, d), [0, 0])
        v[0] += 1
        v[1] += 1 if g(i_sw).upper().startswith("Y") else 0
    agents.sort(key=lambda a: (-sum(cnt.get((a, d), [0, 0])[0] for d in days), a))

    colb = [c[0].strip() if c else "" for c in ws.get_values("B1:B200")]
    start = next((i + 1 for i, v in enumerate(colb) if v == T3_TITLE), t2_total + 3)
    ncols = 1 + 1 + 2 * (len(days) + 1)                 # B agent, C blank, pairs from D
    nrows = 3 + len(agents) + 1
    need_rows, need_cols = start + nrows + 2, 1 + ncols + 1
    if ws.row_count < need_rows or ws.col_count < need_cols:
        ws.resize(rows=max(ws.row_count, need_rows), cols=max(ws.col_count, need_cols))
    last_col = gspread.utils.rowcol_to_a1(1, 1 + ncols).rstrip("1")
    ws.batch_clear(["B%d:%s%d" % (start, last_col, start + nrows + 30)])

    title = [T3_TITLE] + [""] * (ncols - 1)
    top = ["Agent", ""]
    sub = ["", ""]
    for d in days:
        top += [d.strftime("%d %b"), ""]
        sub += ["Visits", "Soft winback"]
    top += ["Total", ""]
    sub += ["Visits", "Soft winback"]
    body, tot = [], [0] * (2 * (len(days) + 1))
    for a in agents:
        row = [a, ""]
        tv = ts_ = 0
        for k, d in enumerate(days):
            v, w = cnt.get((a, d), [0, 0])
            row += [v, w]
            tv += v; ts_ += w
            tot[2 * k] += v; tot[2 * k + 1] += w
        row += [tv, ts_]
        tot[-2] += tv; tot[-1] += ts_
        body.append(row)
    total = ["Total", ""] + tot
    ws.update(values=[title, top, sub] + body + [total], range_name="B%d" % start,
              value_input_option="RAW")

    sid = ws.id
    r0, r1 = start - 1, start - 1 + nrows                # 0-based rows of the block
    grid = {"sheetId": sid, "startRowIndex": r0 + 1, "endRowIndex": r1,
            "startColumnIndex": 1, "endColumnIndex": 1 + ncols}
    solid = {"style": "SOLID"}
    reqs = [
        {"unmergeCells": {"range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1 + 30,
                                    "startColumnIndex": 1, "endColumnIndex": 1 + ncols}}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1 + 30,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + ncols},
                        "cell": {"userEnteredFormat": {"backgroundColor": WHITE,
                                                       "textFormat": {"bold": False}}},
                        "fields": "userEnteredFormat(backgroundColor,textFormat.bold)"}},
        {"updateBorders": {"range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1 + 30,
                                     "startColumnIndex": 1, "endColumnIndex": 1 + ncols},
                           "top": {"style": "NONE"}, "bottom": {"style": "NONE"},
                           "left": {"style": "NONE"}, "right": {"style": "NONE"},
                           "innerHorizontal": {"style": "NONE"}, "innerVertical": {"style": "NONE"}}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0 + 3,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + ncols},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r1 - 1, "endRowIndex": r1,
                                  "startColumnIndex": 1, "endColumnIndex": 1 + ncols},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                 "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93}}},
                        "fields": "userEnteredFormat(textFormat.bold,backgroundColor)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r0 + 1, "endRowIndex": r1,
                                  "startColumnIndex": 3, "endColumnIndex": 1 + ncols},
                        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER",
                                                       "wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat(horizontalAlignment,wrapStrategy)"}},
        {"updateBorders": {"range": grid, "top": solid, "bottom": solid, "left": solid,
                           "right": solid, "innerHorizontal": solid, "innerVertical": solid}},
    ]
    for ai in range(len(agents)):                       # per-day visits: >1 green, else red
        for k in range(len(days)):
            v = body[ai][2 + 2 * k]
            reqs.append({"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": r0 + 3 + ai, "endRowIndex": r0 + 4 + ai,
                          "startColumnIndex": 3 + 2 * k, "endColumnIndex": 4 + 2 * k},
                "cell": {"userEnteredFormat": {"backgroundColor": GREEN if v > 1 else RED}},
                "fields": "userEnteredFormat.backgroundColor"}})
    for c0 in range(3, 1 + ncols, 2):                  # each date / total header over its pair
        reqs.append({"mergeCells": {"range": {"sheetId": sid, "startRowIndex": r0 + 1,
                                              "endRowIndex": r0 + 2, "startColumnIndex": c0,
                                              "endColumnIndex": c0 + 2}, "mergeType": "MERGE_ALL"}})
    for rr in range(r0 + 1, r1):                        # agent name spans B:C
        reqs.append({"mergeCells": {"range": {"sheetId": sid, "startRowIndex": rr, "endRowIndex": rr + 1,
                                              "startColumnIndex": 1, "endColumnIndex": 3},
                                    "mergeType": "MERGE_ALL"}})
    sh.batch_update({"requests": reqs})
    return ("Table 3 at row %d: %d agents x %d days | visits %s | soft %s" % (
        start, len(agents), len(days), tot[0::2], tot[1::2]), start + nrows - 1)


T4_TITLE = "Contacted x Soft winback  (TOF without Non_compliant)"
T4_PRE = ("2026-09-01", "2026-09-07")
T4_ROWS = 10                                            # title, note, 2 headers, 2 x 3 box rows


def contact_table(sh, ws, start, member, trk, post):
    """Rewrite Table 4 (2x2 contacted x soft winback) with its title on row `start`."""
    ids = sorted(c for c, b in member.items() if b != NC)
    pre = fetch_pre(ids, *T4_PRE)
    post = dict(post)
    post.update(fetch_post([(c, BULK_DATE) for c in ids if not trk[c]["date"]]))
    box = {}
    for c in ids:
        box.setdefault((bool(trk[c]["date"]), trk[c]["soft"].upper().startswith("Y")), []).append(c)
    from winback_prepost import fetch_active
    active = fetch_active(ids)

    def cnt(cs):
        return len(cs), sum(active.get(c, 0) for c in cs)

    def group(want_contacted=None, want_soft=None):
        return [c for (ct, sf), cs in box.items() for c in cs
                if (want_contacted is None or ct == want_contacted)
                and (want_soft is None or sf == want_soft)]

    def funnel(cs):
        pl = pa = pi = ql = qa = qi = 0
        for c in cs:
            a, s_, i2 = pre.get(c, (0, 0, 0)); pl += a; pa += s_; pi += i2
            a, s_, i2 = post.get(c, (0, 0, 0)); ql += a; qa += s_; qi += i2
        return [("B2A", pct(pa, pl), pct(qa, ql)), ("A2I", pct(pi, pa), pct(qi, qa)),
                ("B2I", pct(pi, pl), pct(qi, ql))]

    # B:C row label, D:G soft winback Yes, H:K soft winback No
    n_all, a_all = cnt(ids)
    rich = []                                          # (0-based row, 0-based col, text, bold chars)
    top = ["Contacted  (visited / called)", "", "", "", "", "", "", "", "", ""]
    for j, soft in enumerate((True, False)):
        head = "Soft winback: %s" % ("Yes" if soft else "No")
        n_, a_ = cnt(group(want_soft=soft))
        top[2 + 4 * j] = "%s\n%d CSPs  (active base %s)" % (head, n_, "{:,}".format(a_))
        rich.append((start + 1, 1 + 2 + 4 * j, top[2 + 4 * j], len(head)))
    vals = [[T4_TITLE] + [""] * 9,
            ["%d CSPs (active base %s) · Contacted = date of calling / visit filled · Pre = 1-7 Sep · "
             "Post = contact date -> now (not contacted: 8 Sep -> now) · leads aged 48h"
             % (n_all, "{:,}".format(a_all))] + [""] * 9,
            top,
            ["", "", "CSPs", "Metric", "Pre", "Post", "CSPs", "Metric", "Pre", "Post"]]
    colour = []                                         # (0-based row, 0-based col, pre, post)
    for k, contacted in enumerate((True, False)):
        n_, a_ = cnt(group(want_contacted=contacted))
        lab = "Yes" if contacted else "No"
        lab_txt = "%s\n%d CSPs\n(active base %s)" % (lab, n_, "{:,}".format(a_))
        rich.append((start - 1 + 4 + 3 * k, 1, lab_txt, len(lab)))
        rows = [[lab_txt, ""] + [""] * 8 for _ in range(3)]
        rows[1][0] = rows[2][0] = ""
        for j, soft in enumerate((True, False)):
            cs = box.get((contacted, soft), [])
            c0 = 2 + 4 * j
            n_, a_ = cnt(cs)
            rows[0][c0] = "%d\n(active base %s)" % (n_, "{:,}".format(a_))
            rich.append((start - 1 + 4 + 3 * k, 1 + c0, rows[0][c0], len(str(n_))))
            for m, (lab, a, b_) in enumerate(funnel(cs)):
                rows[m][c0 + 1:c0 + 4] = [lab, a, b_]
                colour.append((start - 1 + 4 + 3 * k + m, 1 + c0 + 3, a, b_))
        vals += rows

    sid = ws.id
    r0 = start - 1
    if ws.row_count < r0 + T4_ROWS + 6:
        ws.resize(rows=r0 + T4_ROWS + 6)
    whole = {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0 + T4_ROWS,
             "startColumnIndex": 1, "endColumnIndex": 11}
    none = {"style": "NONE"}
    sh.batch_update({"requests": [
        {"unmergeCells": {"range": whole}},
        {"updateCells": {"range": whole, "fields": "userEnteredValue,userEnteredFormat"}},
        {"updateBorders": {"range": whole, "top": none, "bottom": none, "left": none,
                           "right": none, "innerHorizontal": none, "innerVertical": none}},
    ]})
    ws.update(values=vals, range_name="B%d" % start, value_input_option="RAW")

    def rng(ra, rb, ca, cb):
        return {"sheetId": sid, "startRowIndex": ra, "endRowIndex": rb,
                "startColumnIndex": ca, "endColumnIndex": cb}
    solid, medium = {"style": "SOLID"}, {"style": "SOLID_MEDIUM"}
    grey = {"red": 0.93, "green": 0.93, "blue": 0.93}
    body0 = r0 + 2                                     # first header row
    reqs = [
        {"repeatCell": {"range": rng(r0, r0 + 1, 1, 11),
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold"}},
        {"repeatCell": {"range": rng(r0 + 1, r0 + 2, 1, 11),
                        "cell": {"userEnteredFormat": {"textFormat": {"italic": True, "foregroundColor":
                                 {"red": 0.4, "green": 0.4, "blue": 0.4}}}},
                        "fields": "userEnteredFormat.textFormat(italic,foregroundColor)"}},
        {"repeatCell": {"range": rng(body0, body0 + 2, 1, 11),
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": grey,
                                                       "horizontalAlignment": "CENTER",
                                                       "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat(textFormat.bold,backgroundColor,horizontalAlignment,"
                                  "verticalAlignment,wrapStrategy)"}},
        {"repeatCell": {"range": rng(body0 + 2, body0 + 8, 1, 11),
                        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER",
                                                       "verticalAlignment": "MIDDLE"}},
                        "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment)"}},
        {"repeatCell": {"range": rng(body0 + 2, body0 + 8, 1, 3),
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": grey}},
                        "fields": "userEnteredFormat(textFormat.bold,backgroundColor)"}},
        {"updateBorders": {"range": rng(body0, body0 + 8, 1, 11), "top": medium, "bottom": medium,
                           "left": medium, "right": medium, "innerHorizontal": solid,
                           "innerVertical": solid}},
    ]
    merges = [rng(body0, body0 + 2, 1, 3),             # corner label
              rng(body0, body0 + 1, 3, 7), rng(body0, body0 + 1, 7, 11)]
    for k in range(2):
        ra = body0 + 2 + 3 * k
        merges += [rng(ra, ra + 3, 1, 3), rng(ra, ra + 3, 3, 4), rng(ra, ra + 3, 7, 8)]
        for ca in (1, 3, 7):                           # medium outline round each box
            cb = 3 if ca == 1 else ca + 4
            reqs.append({"updateBorders": {"range": rng(ra, ra + 3, ca, cb), "top": medium,
                                           "bottom": medium, "left": medium, "right": medium}})
    for ca in (3, 7):
        reqs.append({"updateBorders": {"range": rng(body0, body0 + 8, ca, ca + 4),
                                       "left": medium, "right": medium}})
    for m in merges:
        reqs.append({"mergeCells": {"range": m, "mergeType": "MERGE_ALL"}})
    for m in merges[3:]:                               # box CSP counts stand out
        if m["startColumnIndex"] in (3, 7):
            reqs.append({"repeatCell": {"range": m, "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True, "fontSize": 12}}},
                "fields": "userEnteredFormat.textFormat(bold,fontSize)"}})
    for rr, cc, txt, nb in rich:                       # first line bold, the rest plain + small
        reqs.append({"updateCells": {
            "range": rng(rr, rr + 1, cc, cc + 1),
            "rows": [{"values": [{"userEnteredValue": {"stringValue": txt},
                                  "textFormatRuns": [{"startIndex": 0, "format": {"bold": True}},
                                                     {"startIndex": nb, "format": {"bold": False,
                                                                                    "fontSize": 9}}]}]}],
            "fields": "userEnteredValue,textFormatRuns"}})
    for rr, cc, a, b_ in colour:
        if a == "-" or b_ == "-" or a == b_:
            continue
        up = float(b_.rstrip("%")) > float(a.rstrip("%"))
        reqs.append({"repeatCell": {"range": rng(rr, rr + 1, cc, cc + 1),
                                    "cell": {"userEnteredFormat": {"backgroundColor": GREEN if up else RED}},
                                    "fields": "userEnteredFormat.backgroundColor"}})
    sh.batch_update({"requests": reqs})

    dated = [trk[c]["date"] for c in ids if trk[c]["date"]]
    lines = ["Table 4 at row %d: %d CSPs, active base %d (earliest contact %s)"
             % (start, n_all, a_all, min(dated) if dated else "-")]
    for contacted in (True, False):
        for soft in (True, False):
            cs = box.get((contacted, soft), [])
            lines.append("   contacted %-3s soft %-3s %4d CSPs (active %6d)  %s" % (
                "Yes" if contacted else "No", "Yes" if soft else "No", len(cs), cnt(cs)[1],
                "  ".join("%s %s->%s" % f for f in funnel(cs))))
    return "\n".join(lines)


def name(pair):
    return "%s %s" % pair


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
    return "%d%%" % round(100.0 * n / d) if d else "-"


def fetch_pre(ids, start=PRE_START, end=PRE_END):
    out = {}
    ids = sorted(ids)
    for i in range(0, len(ids), 60):
        sql = """
        WITH conn AS (%s
          WHERE f.ETL_CURRENT AND f.CSP_ID IN ('%s')
            AND TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata',f.CREATED_AT)) BETWEEN '%s' AND '%s'
          GROUP BY 1,2 %s)
        SELECT CSP_ID, COUNT(*), SUM(assigned), SUM(installed) FROM conn GROUP BY 1
        """ % (CONN, "','".join(ids[i:i + 60]), start, end, AGED)
        for r in mb(sql):
            out[r[0]] = (r[1] or 0, r[2] or 0, r[3] or 0)
    return out


def fetch_post(pairs):
    out = {}
    pairs = sorted(pairs)
    for i in range(0, len(pairs), 60):
        union = " UNION ALL ".join(
            "SELECT '%s' AS csp_id, DATE '%s' AS start_d" % (c, d.isoformat())
            for c, d in pairs[i:i + 60])
        sql = """
        WITH w AS (%s),
        conn AS (%s
          JOIN w ON w.csp_id=f.CSP_ID
          WHERE f.ETL_CURRENT
            AND TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata',f.CREATED_AT)) >= w.start_d
          GROUP BY 1,2 %s)
        SELECT CSP_ID, COUNT(*), SUM(assigned), SUM(installed) FROM conn GROUP BY 1
        """ % (union, CONN, AGED)
        for r in mb(sql):
            out[r[0]] = (r[1] or 0, r[2] or 0, r[3] or 0)
    return out


ORDERS = "PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.DEVICE_ORDERS"


def fetch_orders_post(pairs):
    """Device orders placed on/after each CSP's post-window start.
    reviewer: leading indicator -- a CSP ordering netboxes after the call is re-engaging
    before any install shows up in B2A/A2I. applied = orders placed, delivered = FULFILLED.
    Statuses REQUESTED/APPROVED/DISPATCHED/FULFILLED/REJECTED."""
    out = {}
    pairs = sorted(pairs)
    for i in range(0, len(pairs), 60):
        union = " UNION ALL ".join(
            "SELECT '%s' c, DATE '%s' d" % (c, d.isoformat()) for c, d in pairs[i:i + 60])
        for r in mb("""
        WITH w AS (%s)
        SELECT o.CSP_ID, COUNT(*), SUM(IFF(o.STATUS='FULFILLED',1,0))
        FROM %s o
        JOIN w ON w.c = o.CSP_ID
        WHERE o._FIVETRAN_ACTIVE
          AND TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata', o.CREATED_AT)) >= w.d
        GROUP BY 1
        """ % (union, ORDERS)):
            out[r[0]] = (r[1] or 0, r[2] or 0)
    return out


def fetch_orders_pre(csp_ids):
    """Same measure over the Pre window (25-31 Aug)."""
    out = {}
    ids = sorted(csp_ids)
    for i in range(0, len(ids), 60):
        for r in mb("""
        SELECT o.CSP_ID, COUNT(*), SUM(IFF(o.STATUS='FULFILLED',1,0))
        FROM %s o
        WHERE o._FIVETRAN_ACTIVE AND o.CSP_ID IN ('%s')
          AND TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata', o.CREATED_AT)) BETWEEN '%s' AND '%s'
        GROUP BY 1
        """ % (ORDERS, "','".join(ids[i:i + 60]), PRE_START, PRE_END)):
            out[r[0]] = (r[1] or 0, r[2] or 0)
    return out


def fetch_netbox(csp_ids):
    """Netboxes actually in the CSP's hands. DEPLOYED sits in customers' homes and
    WRITTEN_OFF / LOST / RETURNED are gone, so only these three count as stock he could
    install with today."""
    out = {}
    ids = sorted(csp_ids)
    for i in range(0, len(ids), 60):
        for r in mb("""
        SELECT CSP_ID, COUNT(*)
        FROM PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.NETBOX_CUSTODY
        WHERE _FIVETRAN_ACTIVE AND CSP_ID IN ('%s')
          AND STATUS IN ('CUSTODIED','IDLE','RETRIEVAL_PENDING')
        GROUP BY 1
        """ % "','".join(ids[i:i + 60])):
            out[r[0]] = int(r[1] or 0)
    return out


def fetch_netbox_before(pairs):
    """Netboxes in the CSP's hands at 00:00 IST on his call / visit date -- i.e. what he held
    going into the call. NETBOX_CUSTODY is a Fivetran history-mode table (versions since
    22-Apr-2026), so the state at a moment T is the version with _FIVETRAN_START <= T <
    _FIVETRAN_END."""
    out = {}
    pairs = sorted(pairs)
    for i in range(0, len(pairs), 60):
        union = " UNION ALL ".join(
            "SELECT '%s' AS c, '%s 00:00:00 +05:30'::TIMESTAMP_TZ AS t" % (c, d.isoformat())
            for c, d in pairs[i:i + 60])
        for r in mb("""
        WITH w AS (%s)
        SELECT n.CSP_ID, COUNT(DISTINCT n.DEVICE_ID)
        FROM PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.NETBOX_CUSTODY n
        JOIN w ON w.c = n.CSP_ID
        WHERE n.STATUS IN ('CUSTODIED','IDLE','RETRIEVAL_PENDING')
          AND n._FIVETRAN_START <= w.t AND n._FIVETRAN_END > w.t
        GROUP BY 1
        """ % union):
            out[r[0]] = int(r[1] or 0)
    return out


def main():
    gc = gclient()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.get_worksheet_by_id(OUT_GID)

    # Non_compliant cohort: the in-sheet list tab is the source of truth (Table 1's formulas
    # read it too, so the two tables cannot disagree). The secret is only a fallback.
    try:
        nc = {c[0].strip() for c in sh.worksheet(NC_TAB).get_values("A2:A1000") if c and c[0].strip()}
    except Exception:
        nc = set()
    if not nc:
        nc = load_noncompliant()

    # (bucket, KF/Field) per sheet row; B is merged down a pair, so blank B inherits
    keys, prev = [], ""
    for r in ws.get_values("B1:C100"):
        b = r[0].strip() if len(r) > 0 else ""
        c = r[1].strip() if len(r) > 1 else ""
        lab = b if b else (prev if c else "")
        prev = lab
        keys.append((lab, c))

    def find(pair, after=0):
        for i in range(after, len(keys)):
            if keys[i] == pair or (isinstance(pair, str) and keys[i][0] == pair):
                return i + 1                    # 1-based sheet row
        return None

    t1 = {b: find(b) for b in ORDER}
    t1_total = find("Total")
    t2_hdr = find("Unlock Buckets", t1_total or 0)
    t2 = {b: find(b, t2_hdr or 0) for b in ORDER}
    t2_total = find("Total", t2_hdr or 0)
    missing = [b for b in ORDER if not t1[b] or not t2[b]]
    if missing or not t1_total or not t2_total:
        print("ABORT: could not locate rows for %r (t1=%r t2=%r)" % (missing, t1, t2))
        return 1

    dates = []
    hdr_row = min(t1.values()) - 1
    for i, v in enumerate(ws.get_values("E%d:Z%d" % (hdr_row, hdr_row))[0]):
        v = v.strip()
        if not v:
            continue
        m = re.match(r"^(\d{1,2})\s+(\w+)$", v)
        if m:
            dates.append((5 + i, dt.datetime.strptime("%s %s 2026" % (m.group(1), m.group(2)),
                                                      "%d %b %Y").date()))
    if not dates:
        print("ABORT: no date headers parsed from row %d" % hdr_row)
        return 1

    trk = {}
    for r in sh.get_worksheet_by_id(0).get_values("B4:J2000"):
        def g(k):
            return r[k].strip() if len(r) > k else ""
        if g(0):
            # a CSP listed twice keeps its LOWER row: a0b5r9 is P6 at r178 and
            # 750 opt out at r197, and the reviewer counts it among the 3 opt-outs
            trk[g(0)] = {"cat": g(3), "mode": g(4), "date": parse_ddmm(g(2)), "soft": g(8)}

    # --- bucket membership: tracker Category wins, then Non_compliant ------------
    # Same rule as the sheet's Table 1 formula: ANY tracker category other than
    # 'Previous Good installers' owns the CSP, so it is never also Non_compliant -- even a
    # category this script does not know yet (it is then reported, not bucketed).
    member, owned, unknown = {}, set(), set()
    for csp, t in trk.items():
        if t["cat"] not in CAT2BUCKET:
            unknown.add(t["cat"])
        if t["cat"] not in NOT_OWNING:
            owned.add(csp)
        base = CAT2BUCKET.get(t["cat"])
        if base:
            mode = FIXED_MODE.get(base) or ("Field" if t["mode"].lower().startswith("visit") else "KF")
            member[csp] = (base, mode)
    for csp in nc - owned:
        member.setdefault(csp, NC)
    if unknown:
        print("  WARNING: tracker categories with no dashboard bucket: %r -- add them to "
              "CAT2BUCKET and the Table 1 formulas" % sorted(unknown))

    # --- table 1 ---------------------------------------------------------------
    counts = {b: {} for b in ORDER}
    for csp, b in member.items():
        if b == NC:
            counts[b][BULK_DATE] = counts[b].get(BULK_DATE, 0) + 1
        else:
            t = trk.get(csp, {})
            if t.get("soft", "").upper().startswith("Y") and t.get("date"):
                counts[b][t["date"]] = counts[b].get(t["date"], 0) + 1

    t1_body = [[sum(counts[b].values())] + [counts[b].get(d, 0) for _, d in dates] for b in ORDER]
    t1_tot = [sum(c[i] for c in t1_body) for i in range(len(t1_body[0]))]

    # --- table 2 ---------------------------------------------------------------
    pre = fetch_pre(set(member))
    pairs = []
    for csp, b in member.items():
        if b == NC:
            pairs.append((csp, BULK_DATE))
        elif trk.get(csp, {}).get("date"):
            pairs.append((csp, trk[csp]["date"]))
    post = fetch_post(pairs)
    post_start = dict(pairs)

    def row_for(ids):
        pl = pa = pi = ql = qa = qi = 0
        for c in ids:
            a, s, i2 = pre.get(c, (0, 0, 0)); pl += a; pa += s; pi += i2
            a, s, i2 = post.get(c, (0, 0, 0)); ql += a; qa += s; qi += i2
        return [pct(pa, pl), pct(qa, ql), pct(pi, pa), pct(qi, qa), pct(pi, pl), pct(qi, ql)]

    t2_body = [row_for([c for c, bb in member.items() if bb == b]) for b in ORDER]
    t2_tot = row_for(list(member))

    # Table 1 is FORMULA-DRIVEN in the sheet (reviewer, 14-Sep) -- never write it, or every
    # run would replace the formulas with static numbers. The counts computed above are only
    # a cross-check: a mismatch means the formulas and this script disagree on a rule.
    last_col = chr(ord("D") + len(dates))
    mismatch = []
    for b, row in zip(ORDER + ["Total"], t1_body + [t1_tot]):
        r = t1_total if b == "Total" else t1[b]
        got = (ws.get_values("D%d:%s%d" % (r, last_col, r)) or [[]])[0]
        got = [int(v) if v.strip().lstrip("-").isdigit() else v for v in got] + [""] * (len(row) - len(got))
        if got[:len(row)] != row:
            mismatch.append((b, got[:len(row)], row))

    # Table 2 is B2A / A2I / B2I (D:I) since 16-Sep; the order / netbox / applied-CSP block moved to
    # the 'Netbox Order Funnel' tab (user: "remove this summary"). Only D:G is written.
    ws.batch_clear(["D%d:I%d" % (t2_hdr - 1, t2_total)])
    data = [{"range": T2_COLS % (t2[b], t2[b]), "values": [row]} for b, row in zip(ORDER, t2_body)]
    data.append({"range": T2_COLS % (t2_total, t2_total), "values": [t2_tot]})
    data.append({"range": "D%d:I%d" % (t2_hdr - 1, t2_hdr - 1),
                 "values": [["B2A %", "", "A2I %", "", "B2I %", ""]]})
    data.append({"range": "D%d:I%d" % (t2_hdr, t2_hdr),
                 "values": [["Pre", "Post", "Pre", "Post", "Pre", "Post"]]})
    ws.batch_update(data, value_input_option="RAW")

    sid = ws.id
    reqs = [
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": min(t1.values()) - 1, "endRowIndex": t2_total,
                      "startColumnIndex": 2, "endColumnIndex": max(4 + len(dates), 9)},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": t2_hdr, "endRowIndex": t2_total,
                      "startColumnIndex": 3, "endColumnIndex": 9},
            "cell": {"userEnteredFormat": {"backgroundColor": WHITE}},
            "fields": "userEnteredFormat.backgroundColor"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": t2_hdr - 2, "endRowIndex": t2_hdr,
                      "startColumnIndex": 3, "endColumnIndex": 9},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "wrapStrategy": "WRAP",
                                           "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(textFormat.bold,wrapStrategy,horizontalAlignment)"}},
    ]
    # post vs pre on each pair: E vs D, G vs F, I vs H (0-based col of the POST cell)
    for i, row in enumerate(t2_body + [t2_tot]):
        rr = (t2[ORDER[i]] if i < len(ORDER) else t2_total) - 1
        for k, col in ((0, 4), (2, 6), (4, 8)):
            pre_s, post_s = row[k], row[k + 1]
            if pre_s == "-" or post_s == "-":
                continue
            a, b_ = float(pre_s.rstrip("%")), float(post_s.rstrip("%"))
            if a == b_:
                continue
            reqs.append({"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": rr, "endRowIndex": rr + 1,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "cell": {"userEnteredFormat": {"backgroundColor": GREEN if b_ > a else RED}},
                "fields": "userEnteredFormat.backgroundColor"}})
    sh.batch_update({"requests": reqs})

    t3_msg, _ = visits_table(gc, sh, ws, t2_total)
    print("  " + t3_msg)
    # Table 4 sits between Table 1 and Table 2 (above Table 2's two header rows + one gap row)
    t4 = next((i + 1 for i in range(t1_total, t2_hdr - 1) if keys[i][0] == T4_TITLE), None)
    if t4 is None:
        t4 = t2_hdr - 2 - T4_ROWS
        free = all(not v.strip() for r in ws.get_values("B%d:K%d" % (t4, t4 + T4_ROWS - 1)) for v in r)
        if not free:
            t4 = None
    if t4 is None or t4 <= t1_total + 1 or t4 + T4_ROWS - 1 > t2_hdr - 2:
        print("  WARNING: Table 4 skipped -- no room for %d rows between Table 1 (ends r%d) and Table 2 "
              "(header r%d); insert rows above Table 2" % (T4_ROWS, t1_total, t2_hdr - 1))
    else:
        print("  " + contact_table(sh, ws, t4, member, trk, post))
    print("  Table 1  formula-driven, not written. cross-check vs script: %s"
          % ("all rows match" if not mismatch else "%d MISMATCH row(s)" % len(mismatch)))
    for b, got, want in mismatch:
        print("   MISMATCH %-22s sheet %s  script %s" % (name(b) if isinstance(b, tuple) else b, got, want))
    print("  Table 2  (B2A pre/post, A2I pre/post, B2I pre/post)")
    for b, row in zip(ORDER + ["Total"], t2_body + [t2_tot]):
        print("   %-22s %s" % (name(b) if isinstance(b, tuple) else b, row))
    stamp(ws, "Table 1 is live formulas (updates instantly)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
