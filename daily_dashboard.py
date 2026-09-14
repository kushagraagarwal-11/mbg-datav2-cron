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
  'Wrong enforcement' was 'Ghost Install' until 14-Sep; the old name is still accepted.

TABLE 1  per bucket: D = total, E.. = per date      *** FORMULA-DRIVEN IN THE SHEET ***
  CSPs unlocked, counted on their date of calling / visit, where Soft Winback = Y.
  Non_compliant: all on BULK_DATE. The sheet computes this itself from the tracker and the
  'Non_compliant list' tab; this script never writes it and only prints a cross-check.

TABLE 2  per bucket, pooled (sum numerators / sum denominators)
  D/E  B2A % pre/post     bookings offered -> technician assigned
  F/G  A2I % pre/post     assigned -> installed
  H    Order applied   (post)   % of called CSPs that placed a device order since the call
  I    Order delivered (post)   % of called CSPs with such an order FULFILLED
  J    % with 0 netbox          netboxes in hand right now (a snapshot)
  K/L  B2A % pre/post  } for ONLY the CSPs that applied for devices since the call
  M/N  A2I % pre/post  }   (user, 14-Sep: did ordering devices go with a funnel move?)
  Pre  = 2026-08-25 .. 2026-08-31 (last week of August)
  Post = each CSP's date of calling / visit -> now (Non_compliant: 8 Sep).
  Leads are aged 48h on both sides (winback_common.AGED). Orders are not aged.
  Order %s: denominator = CSPs with a post window (called / visited). Post only (user, 14-Sep).
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
              "Previous Good installers": None}
# buckets whose KF / Field is fixed; every other bucket is split by tracker column F
FIXED_MODE = {"Exit": "Field", "750 Opt out": "Field", "Wrong enforcement": "KF"}
T2_COLS = "D%d:N%d"
NC_TAB = "Non_compliant list"
GREEN = {"red": 0.80, "green": 0.92, "blue": 0.82}
RED = {"red": 0.98, "green": 0.83, "blue": 0.83}
WHITE = {"red": 1, "green": 1, "blue": 1}


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

    # (bucket, KF/Field) per sheet row; B is merged down a pair, so blank B inherits
    keys, prev = [], ""
    for r in ws.get_values("B1:C60"):
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
        if t["cat"] != "Previous Good installers":
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
    ord_post = fetch_orders_post(pairs)
    netbox = fetch_netbox(set(member))

    def row_for(ids):
        pl = pa = pi = ql = qa = qi = 0
        for c in ids:
            a, s, i2 = pre.get(c, (0, 0, 0)); pl += a; pa += s; pi += i2
            a, s, i2 = post.get(c, (0, 0, 0)); ql += a; qa += s; qi += i2
        # order denominators = CSPs with a post window (called / visited)
        called = [c for c in ids if c in post_start]
        n = len(called)
        applied = [c for c in called if ord_post.get(c, (0, 0))[0] > 0]
        dq = sum(1 for c in called if ord_post.get(c, (0, 0))[1] > 0)
        z = sum(1 for c in called if netbox.get(c, 0) == 0)
        # the CSPs that applied for devices since the call: did their funnel move?
        opl = opa = opi = oql = oqa = oqi = 0
        for c in applied:
            a, s, i2 = pre.get(c, (0, 0, 0)); opl += a; opa += s; opi += i2
            a, s, i2 = post.get(c, (0, 0, 0)); oql += a; oqa += s; oqi += i2
        return [pct(pa, pl), pct(qa, ql), pct(pi, pa), pct(qi, qa),
                pct(len(applied), n), pct(dq, n), pct(z, n),
                pct(opa, opl), pct(oqa, oql), pct(opi, opa), pct(oqi, oqa)]

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

    # the block grew from D:L to D:N -- clear it first so no stale Pre order column survives
    ws.batch_clear(["D%d:N%d" % (t2_hdr - 1, t2_total)])
    data = [{"range": T2_COLS % (t2[b], t2[b]), "values": [row]} for b, row in zip(ORDER, t2_body)]
    data.append({"range": T2_COLS % (t2_total, t2_total), "values": [t2_tot]})
    data.append({"range": "D%d:N%d" % (t2_hdr - 1, t2_hdr - 1),
                 "values": [["B2A %", "", "A2I %", "", "Order applied (% of CSPs called)",
                             "Order delivered (% of CSPs called)", "Netbox now",
                             "CSPs that applied for devices: B2A %", "",
                             "CSPs that applied for devices: A2I %", ""]]})
    data.append({"range": "D%d:N%d" % (t2_hdr, t2_hdr),
                 "values": [["Pre", "Post", "Pre", "Post", "Post", "Post", "% with 0 netbox",
                             "Pre", "Post", "Pre", "Post"]]})
    ws.batch_update(data, value_input_option="RAW")

    sid = ws.id
    reqs = [
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": min(t1.values()) - 1, "endRowIndex": t2_total,
                      "startColumnIndex": 2, "endColumnIndex": max(4 + len(dates), 14)},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": t2_hdr, "endRowIndex": t2_total,
                      "startColumnIndex": 3, "endColumnIndex": 14},
            "cell": {"userEnteredFormat": {"backgroundColor": WHITE}},
            "fields": "userEnteredFormat.backgroundColor"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": t2_hdr - 2, "endRowIndex": t2_hdr,
                      "startColumnIndex": 3, "endColumnIndex": 14},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "wrapStrategy": "WRAP",
                                           "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(textFormat.bold,wrapStrategy,horizontalAlignment)"}},
    ]
    # post vs pre on each pair: E vs D, G vs F, L vs K, N vs M (0-based col of the POST cell)
    for i, row in enumerate(t2_body + [t2_tot]):
        rr = (t2[ORDER[i]] if i < len(ORDER) else t2_total) - 1
        for k, col in ((0, 4), (2, 6), (7, 11), (9, 13)):
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
        print("   MISMATCH %-22s sheet %s  script %s" % (name(b) if isinstance(b, tuple) else b, got, want))
    print("  Table 2  (B2A pre/post, A2I pre/post, applied, delivered, 0 netbox, applied-CSPs B2A pre/post, A2I pre/post)")
    for b, row in zip(ORDER + ["Total"], t2_body + [t2_tot]):
        print("   %-22s %s" % (name(b) if isinstance(b, tuple) else b, row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
