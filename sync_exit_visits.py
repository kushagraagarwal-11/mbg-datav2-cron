"""
Sync the 'Willing to Exit CSPs' visit sheet into the main 'Kushagra/Fahad Winback' tab.
Runs FIRST in winback-refresh.yml, so the dashboards below it use the fresh visit dates.

SCOPE -- only tracker rows that are VISITED, not called:
    Category = Exit            (any mode)
    Winback and Visiting (F) = Visiting   (the 89/Field CSPs)
  Rows the K/F team CALLED keep their calling date even if the CSP also appears in the visit
  sheet (reviewer, 14-Sep: "visiting and exit u have to take dates from this sheet").

Writes, per matching row:
    D  Date of calling / visit   <- Date of Visit      (overwritten -- the visit sheet owns it)
    J  Soft Winback (Y/N)        <- Soft Winback (Y/N) (normalised to Y / N)
    K  CI(m1)                    <- CI(m1)             (only when the tracker cell is blank)
    L  750                       <- 750                (only when the tracker cell is blank)

DATES
  The visit sheet mixes formats: '09th Sep', '14 Sep', '10-09-26' (day first) and '09-13-26'
  (month first), plus typos like '14 seo'. A dd-dd-yy value is read both ways and the
  interpretation that lands on or before today wins; if both do, day-first wins (the older
  entries are day-first). A visit dated AFTER today is a planned visit -- it is skipped and
  picked up automatically on the first run on/after that date.

SAFETY
  * Never blanks a tracker cell: an empty source value writes nothing.
  * Only touches rows whose CSP ID is already in the tracker; never adds rows.
  * Never touches column G (reviewer's XLOOKUP formulas) -- writes D and J:L only.
  * If the visit sheet cannot be read, aborts without writing (no stale fallback).
  * Backs up D/J/K/L to JSON before writing; reports every change.
"""
import glob
import json
import os
import re
import sys
import datetime as dt

from winback_common import SHEET_ID, IST, gclient

SOURCE_SHEET = "1WsADMo2slH0VZhCdBbAg2ortl6_AfTs-hSBvTBEoRto"   # Willing to Exit CSPs
BACKUP_DIR = os.environ.get("WINBACK_BACKUP_DIR") or os.path.dirname(os.path.abspath(__file__))
COLS = {"visit": "D", "soft": "J", "ci": "K", "opt750": "L"}
FIRST_DATA_ROW = 4
YEAR = 2026
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_visit(s, today):
    """-> (date or None, note). note is '' on success, else why it was not used."""
    s = (s or "").strip().lower()
    if not s:
        return None, ""
    # '09th Sep', '14 Sep', '14 seo'
    m = re.match(r"^(\d{1,2})\s*(?:st|nd|rd|th)?[\s\-/]*([a-z]{2,})\.?$", s)
    if m:
        tok = m.group(2)
        mon = next((i + 1 for i, n in enumerate(MONTHS) if tok[:3] == n), None)
        if mon is None:                      # typo: unique match on the first two letters
            hits = [i + 1 for i, n in enumerate(MONTHS) if n[:2] == tok[:2]]
            mon = hits[0] if len(hits) == 1 else None
        if mon is None:
            return None, "unreadable month %r" % s
        try:
            d = dt.date(YEAR, mon, int(m.group(1)))
        except ValueError:
            return None, "invalid date %r" % s
        return (d, "") if d <= today else (None, "future %s" % d.isoformat())
    # dd-dd-yy / dd/dd/yyyy -- ambiguous order
    m = re.match(r"^(\d{1,2})[\-/.](\d{1,2})[\-/.](\d{2,4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        cands = []
        for day, mon in ((a, b), (b, a)):    # day-first first, so it wins a tie
            try:
                cands.append(dt.date(y, mon, day))
            except ValueError:
                pass
        past = [d for d in cands if d <= today]
        if past:
            return past[0], ""
        if cands:
            return None, "future %s" % min(cands).isoformat()
        return None, "invalid date %r" % s
    return None, "unreadable %r" % s


def main():
    today = dt.datetime.now(IST).date()
    gc = gclient()

    rows = gc.open_by_key(SOURCE_SHEET).get_worksheet_by_id(0).get_values("A1:H400")
    hdr = [c.strip().lower() for c in rows[0]]
    ix = {k: next((i for i, h in enumerate(hdr) if k in h), None)
          for k in ("csp id", "date of visit", "soft winback", "ci(m1)", "750")}
    if ix["csp id"] is None or ix["date of visit"] is None:
        print("ABORT: visit sheet headers moved: %r" % rows[0])
        return 1

    src, skipped = {}, []
    for r in rows[1:]:
        def g(key):
            i = ix[key]
            return r[i].strip() if i is not None and len(r) > i else ""
        csp = g("csp id")
        if not csp:
            continue
        d, note = parse_visit(g("date of visit"), today)
        if note:
            skipped.append((csp, note))
        soft = g("soft winback").upper()
        src[csp] = {"visit": d.strftime("%d/%m/%Y") if d else "",
                    "soft": "Y" if soft.startswith("Y") else ("N" if soft.startswith("N") else ""),
                    "ci": g("ci(m1)"), "opt750": g("750")}
    print("  visit sheet: %d CSPs" % len(src))

    ws = gc.open_by_key(SHEET_ID).get_worksheet_by_id(0)
    h = ws.get_values("D3:L3")[0]
    if (h[0].strip() != "Date of calling / visit" or h[6].strip() != "Soft Winback (Y/N)"
            or h[7].strip() != "CI(m1)" or h[8].strip() != "750"):
        print("ABORT: tracker D3:L3 is %r -- columns moved." % h)
        return 1
    if ws.get_values("E3:F3")[0] != ["Category", "Winback and Visiting"]:
        print("ABORT: tracker E3:F3 is %r -- columns moved." % ws.get_values("E3:F3")[0])
        return 1

    grid = ws.get_values("B4:L400")
    n = len(grid)

    def g(r, i):
        return r[i].strip() if len(r) > i else ""

    cur = {"visit": [[g(r, 2)] for r in grid], "soft": [[g(r, 8)] for r in grid],
           "ci": [[g(r, 9)] for r in grid], "opt750": [[g(r, 10)] for r in grid]}

    changes, in_scope = [], 0
    for idx, r in enumerate(grid):
        csp = g(r, 0)
        if not csp or csp not in src:
            continue
        if not (g(r, 3) == "Exit" or g(r, 4).lower().startswith("visit")):
            continue                          # called by K/F -- keep the calling date
        in_scope += 1
        s = src[csp]
        for key in ("visit", "soft", "ci", "opt750"):
            new, was = s[key], cur[key][idx][0]
            if not new or new == was:
                continue
            if key in ("ci", "opt750") and was:
                continue                      # tracker vocabulary is richer -- fill blanks only
            changes.append((idx + FIRST_DATA_ROW, csp, key, was or "(blank)", new))
            cur[key][idx] = [new]

    for csp, note in skipped:
        print("   skipped %-8s %s" % (csp, note))
    if not changes:
        print("  %d visited rows in scope, nothing to change" % in_scope)
        return 0
    if os.environ.get("DRY_RUN"):
        for row, csp, key, was, now in changes:
            print("   DRY r%-4s %-8s %-7s %-12s -> %s" % (row, csp, key, was[:12], now[:24]))
        print("  DRY RUN: %d visited rows in scope, %d cells would change" % (in_scope, len(changes)))
        return 0

    bpath = os.path.join(BACKUP_DIR, "exitsync_backup_%s.json" % dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(bpath, "w", encoding="utf-8") as fh:
        json.dump({"taken": dt.datetime.now().isoformat(), "rows": n,
                   "values": {k: [[g(r, i)] for r in grid] for k, i in
                              (("visit", 2), ("soft", 8), ("ci", 9), ("opt750", 10))}}, fh, indent=1)
    for stale in sorted(glob.glob(os.path.join(BACKUP_DIR, "exitsync_backup_*.json")))[:-30]:
        try:
            os.remove(stale)
        except OSError:
            pass

    # write only the cells that changed -- other columns and rows are left untouched
    ws.batch_update([{"range": "%s%d" % (COLS[key], row), "values": [[now]]}
                     for row, csp, key, was, now in changes], value_input_option="RAW")

    for row, csp, key, was, now in changes:
        print("   r%-4s %-8s %-7s %-12s -> %s" % (row, csp, key, was[:12], now[:24]))
    print("  %d visited rows in scope, %d cells updated | backup -> %s" % (in_scope, len(changes), bpath))
    return 0


if __name__ == "__main__":
    sys.exit(main())
