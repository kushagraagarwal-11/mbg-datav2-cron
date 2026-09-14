"""
Fill the 'Daily Dashboard' tab (gid 1292308429) of the P1/P2 Winbacks sheet.

Rows are found by their LABEL in column B, never by fixed row number -- the reviewer adds
and moves rows. Expected labels, top table then bottom table:
  89/KF · 89/Field · Exit · Non_compliant · P6_Fallouts/KF · P6_Fallouts/Field · 750 Opt out ·
  Growth Team/KF · Growth Team/Field · Ghost Install · P1/P2/KF · P1/P2/Field · Total

BUCKETS  (tracker = 'Kushagra/Fahad Winback' tab)
  /KF vs /Field    -> split by column F 'Winback and Visiting': Visiting = field visit ->
                      /Field, anything else = the K/F calling team -> /KF (reviewer, 12 + 14-Sep)
                      Split: 89 (Category 'Winback _ 89 CSPs', P2, blank), P6_Fallouts,
                      Growth Team, P1/P2 (142-CSP cohort appended 14-Sep).
                      Ghost Install has no Visiting rows, so it is not split.
  Exit, 750 Opt out, Ghost Install -> tracker Category
  Non_compliant   -> the reviewer's 115-CSP list, all on BULK_DATE (8 Sep)
  PRECEDENCE: the tracker Category wins, then Non_compliant. No CSP is counted twice.
  750 Opt out is tracker-only now (the 3 CSPs marked there); the older opt750.csv is retired.

TABLE 1  per bucket: C = total, D.. = per date      *** FORMULA-DRIVEN IN THE SHEET ***
  CSPs unlocked, counted on their date of calling / visit, where Soft Winback = Y.
  Non_compliant: all on BULK_DATE. The sheet computes this itself from the tracker and the
  'Non_compliant list' tab; this script never writes it and only prints a cross-check.

TABLE 2  per bucket, pooled (sum numerators / sum denominators)
  C/D  B2A % pre/post     bookings offered -> technician assigned
  E/F  A2I % pre/post     assigned -> installed
  G/H  Order applied  pre/post   % of CSPs that placed a device order in the window
  I/J  Order delivered pre/post  % of CSPs with an order FULFILLED, placed in the window
  K    % with 0 netbox           netboxes in hand right now (no pre/post: a snapshot)
  Pre  = 2026-08-25 .. 2026-08-31 (last week of August)
  Post = each CSP's date of calling / visit -> now (Non_compliant: 8 Sep).
  Leads are aged 48h on both sides (winback_common.AGED). Orders are not aged.
  Order %s use the SAME denominator on both sides -- CSPs with a post window -- so pre and
  post describe the same set of CSPs (reviewer, 11-Sep: "add the respective pre post").
  Grain = DISTINCT CONNECTION_ID per CSP per window, matching the Pre/Post Winback tab.

Colours: green where post > pre, red where post < pre, on every pre/post pair.
"""
import re
import sys
import datetime as dt

from winback_common import (SHEET_ID, PRE_START, PRE_END, CONN, AGED, mb, gclient,
                            load_noncompliant)

OUT_GID = 1292308429
BULK_DATE = dt.date(2026, 9, 8)                 # Non_compliant: done in one go, first date

ORDER = ["89/KF", "89/Field", "Exit", "Non_compliant", "P6_Fallouts/KF", "P6_Fallouts/Field",
         "750 Opt out", "Growth Team/KF", "Growth Team/Field", "Ghost Install",
         "P1/P2/KF", "P1/P2/Field"]
CAT2BUCKET = {"Winback _ 89 CSPs": "89", "P2": "89",
              "": "89",                        # undated-category rows in the 89 block
              "P1/P2": "P1/P2",                # 142-CSP cohort appended 14-Sep, its own rows
              "Exit": "Exit", "P6_Fallouts": "P6_Fallouts",
              "750 opt out": "750 Opt out", "Growth Team": "Growth Team",
              "Ghost Install": "Ghost Install",
              "Previous Good installers": None}
# categories split by tracker col F 'Winback and Visiting': Visiting -> /Field, else -> /KF
SPLIT = {"89", "P6_Fallouts", "Growth Team", "P1/P2"}
T2_COLS = "C%d:K%d"
NC_TAB = "Non_compliant list"
GREEN = {"red": 0.80, "green": 0.92, "blue": 0.82}
RED = {"red": 0.98, "green": 0.83, "blue": 0.83}
WHITE = {"red": 1, "green": 1, "blue": 1}


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


def fetch_pre(ids):
    out = {}
    ids = sorted(ids)
    for i in range(0, len(ids), 60):
        sql = """
        WITH conn AS (%s
          WHERE f.ETL_CURRENT AND f.CSP_ID IN ('%s')
            AND TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata',f.CREATED_AT)) BETWEEN '%s' AND '%s'
          GROUP BY 1,2 %s)
        SELECT CSP_ID, COUNT(*), SUM(assigned), SUM(installed) FROM conn GROUP BY 1
        """ % (CONN, "','".join(ids[i:i + 60]), PRE_START, PRE_END, AGED)
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

    colB = [c[0].strip() if c else "" for c in ws.get_values("B1:B60")]

    def find(label, after=0):
        for i in range(after, len(colB)):
            if colB[i] == label:
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
    for i, v in enumerate(ws.get_values("D%d:Z%d" % (hdr_row, hdr_row))[0]):
        v = v.strip()
        if not v:
            continue
        m = re.match(r"^(\d{1,2})\s+(\w+)$", v)
        if m:
            dates.append((4 + i, dt.datetime.strptime("%s %s 2026" % (m.group(1), m.group(2)),
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
        if t["cat"] != "Previous Good installers":
            owned.add(csp)
        b = CAT2BUCKET.get(t["cat"])
        if b in SPLIT:
            b += "/Field" if t["mode"].lower().startswith("visit") else "/KF"
        if b:
            member[csp] = b
    for csp in nc - owned:
        member.setdefault(csp, "Non_compliant")
    if unknown:
        print("  WARNING: tracker categories with no dashboard bucket: %r -- add them to "
              "CAT2BUCKET and the Table 1 formulas" % sorted(unknown))

    # --- table 1 ---------------------------------------------------------------
    counts = {b: {} for b in ORDER}
    for csp, b in member.items():
        if b == "Non_compliant":
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
        if b == "Non_compliant":
            pairs.append((csp, BULK_DATE))
        elif trk.get(csp, {}).get("date"):
            pairs.append((csp, trk[csp]["date"]))
    post = fetch_post(pairs)
    post_start = dict(pairs)
    ord_post = fetch_orders_post(pairs)
    ord_pre = fetch_orders_pre(set(post_start))
    netbox = fetch_netbox(set(member))

    def row_for(ids):
        pl = pa = pi = ql = qa = qi = 0
        for c in ids:
            a, s, i2 = pre.get(c, (0, 0, 0)); pl += a; pa += s; pi += i2
            a, s, i2 = post.get(c, (0, 0, 0)); ql += a; qa += s; qi += i2
        # order denominators = CSPs with a post window (called / visited), both sides
        called = [c for c in ids if c in post_start]
        n = len(called)
        ap = sum(1 for c in called if ord_pre.get(c, (0, 0))[0] > 0)
        aq = sum(1 for c in called if ord_post.get(c, (0, 0))[0] > 0)
        dp = sum(1 for c in called if ord_pre.get(c, (0, 0))[1] > 0)
        dq = sum(1 for c in called if ord_post.get(c, (0, 0))[1] > 0)
        z = sum(1 for c in called if netbox.get(c, 0) == 0)
        return [pct(pa, pl), pct(qa, ql), pct(pi, pa), pct(qi, qa),
                pct(ap, n), pct(aq, n), pct(dp, n), pct(dq, n), pct(z, n)]

    t2_body = [row_for([c for c, bb in member.items() if bb == b]) for b in ORDER]
    t2_tot = row_for(list(member))

    # Table 1 is FORMULA-DRIVEN in the sheet (reviewer, 14-Sep) -- never write it, or every
    # run would replace the formulas with static numbers. The counts computed above are only
    # a cross-check: a mismatch means the formulas and this script disagree on a rule.
    last_col = chr(ord("C") + len(dates))
    mismatch = []
    for b, row in zip(ORDER + ["Total"], t1_body + [t1_tot]):
        r = t1_total if b == "Total" else t1[b]
        got = (ws.get_values("C%d:%s%d" % (r, last_col, r)) or [[]])[0]
        got = [int(v) if v.strip().lstrip("-").isdigit() else v for v in got] + [""] * (len(row) - len(got))
        if got[:len(row)] != row:
            mismatch.append((b, got[:len(row)], row))

    data = [{"range": T2_COLS % (t2[b], t2[b]), "values": [row]} for b, row in zip(ORDER, t2_body)]
    data.append({"range": T2_COLS % (t2_total, t2_total), "values": [t2_tot]})
    data.append({"range": "C%d:K%d" % (t2_hdr - 1, t2_hdr - 1),
                 "values": [["B2A %", "", "A2I %", "", "Order applied (% of CSPs called)", "",
                             "Order delivered (% of CSPs called)", "", "Netbox now"]]})
    data.append({"range": "C%d:K%d" % (t2_hdr, t2_hdr),
                 "values": [["Pre", "Post", "Pre", "Post", "Pre", "Post", "Pre", "Post",
                             "% with 0 netbox"]]})
    ws.batch_update(data, value_input_option="RAW")

    sid = ws.id
    reqs = [
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": min(t1.values()) - 1, "endRowIndex": t2_total,
                      "startColumnIndex": 2, "endColumnIndex": max(3 + len(dates), 11)},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": t2_hdr, "endRowIndex": t2_total,
                      "startColumnIndex": 2, "endColumnIndex": 11},
            "cell": {"userEnteredFormat": {"backgroundColor": WHITE}},
            "fields": "userEnteredFormat.backgroundColor"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": t2_hdr - 2, "endRowIndex": t2_hdr,
                      "startColumnIndex": 2, "endColumnIndex": 11},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "wrapStrategy": "WRAP",
                                           "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(textFormat.bold,wrapStrategy,horizontalAlignment)"}},
    ]
    # post vs pre on each pair: D vs C, F vs E, H vs G, J vs I (0-based col of the POST cell)
    for i, row in enumerate(t2_body + [t2_tot]):
        rr = (t2[ORDER[i]] if i < len(ORDER) else t2_total) - 1
        for k, col in ((0, 3), (2, 5), (4, 7), (6, 9)):
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

    print("  Table 1  formula-driven, not written. cross-check vs script: %s"
          % ("all rows match" if not mismatch else "%d MISMATCH row(s)" % len(mismatch)))
    for b, got, want in mismatch:
        print("   MISMATCH %-14s sheet %s  script %s" % (b, got, want))
    print("  Table 2  (B2A pre/post, A2I pre/post, applied pre/post, delivered pre/post, 0 netbox)")
    for b, row in zip(ORDER + ["Total"], t2_body + [t2_tot]):
        print("   %-14s %s" % (b, row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
