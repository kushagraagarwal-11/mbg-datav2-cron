import os
# Daily 08:00 IST Slack snapshot of the soft-winback CSPs: one image per BD owner, one for
# their leader. This runs in GitHub Actions because that is where the Slack token lives - the
# laptop has never held it, and nobody should have to paste a token into a shell to send a
# daily report.
#
# Everything is rebuilt from source here; there is no console payload in CI:
#   who was won back, when, and whose he is  <- "BD Console Log" tab of the BD workbook
#   what leads he got each day               <- TAS_INSTALL_EXECUTION_CANDIDATES via Metabase
#
# Columns are real calendar dates, not "D-1 since HIS winback". Winback dates differ per CSP,
# so an offset grid would put 23-Sep beside 26-Sep in one column and invite a reader to compare
# them. Days before a CSP's own winback are hatched, so an empty cell always means "no leads
# that day" and never "not started yet".
#
# Env (all already secrets in this repo):
#   PAYOUT750_SLACK_TOKEN, GOOGLE_SA_JSON, MB_KEY
#   SLACK_CHANNEL_ID  post to a channel   |   SNAP_DM  DM this email instead   |   DRY_RUN=1
import datetime
import io
import json
import sys
import tempfile

import gspread
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
OUT = os.environ.get("SNAP_OUT", "out")
MAXCOLS = 14
BOOK = os.environ.get("BD_BOOK", "1WsADMo2slH0VZhCdBbAg2ortl6_AfTs-hSBvTBEoRto")
TAB = os.environ.get("BD_LOG_TAB", "BD Console Log")
IEC = "PROD_DB.DBT_CSP.TAS_INSTALL_EXECUTION_CANDIDATES"
DAILY_DAYS = 45
CHUNK = 60

# Owner -> Slack USER id. Not a D... id: those belong to a specific pair of people, and the
# ones on the profile panes are Palak's conversations - the bot is not in them and gets
# channel_not_found. The bot opens its own DM with these instead, which its im:write allows.
# It has no users:read.email, so the id cannot be derived from the address at run time; the
# address is recorded beside each one so the mapping can be checked by eye.
OWNER_USER = {
    "Hammad": "U08C0SBJR9Q",     # hammad.siddiqui@wiom.in
    "Manvendra": "U0A2FTGCPLK",  # manvendra@wiom.in
    "Shahrukh": "U055PSVNA85",   # shahrukh.khan@wiom.in
    "Shoib": "U06719JQVJ4",      # shoib.akhtar@wiom.in
    "Sanoj": "U054T8UU3PY",      # sanoj.singh@i2e1.com
}
_DM_CACHE = {}


def dm_channel(uid):
    """The bot's own DM channel with a user id, opened on demand and remembered."""
    if not uid:
        return None
    if uid in _DM_CACHE:
        return _DM_CACHE[uid]
    tok = token()
    if not tok:
        return None
    r = requests.post("https://slack.com/api/conversations.open",
                      headers={"Authorization": "Bearer " + tok},
                      data={"users": uid}).json()
    if not r.get("ok"):
        print("  could not open a DM with %s: %s" % (uid, r.get("error")))
        return None
    _DM_CACHE[uid] = r["channel"]["id"]
    return _DM_CACHE[uid]


def dm_for(owner):
    return (os.environ.get("SNAP_DM_" + owner.upper())
            or dm_channel(OWNER_USER.get(owner)))

INK = "#1a1d23"
MUTED = "#8a8f98"
LINE = "#dfe3e8"
PAPER = "#ffffff"
BAND = "#f4f6f8"


def sa_file():
    """GOOGLE_SA_JSON holds raw JSON in CI and a path locally. Accept either."""
    v = os.environ.get("GOOGLE_SA_JSON", "")
    if v.strip().startswith("{"):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        f.write(v)
        f.close()
        return f.name
    return v or os.path.expanduser(os.path.join("~", ".config", "google",
                                                "service-account.json"))


def mb(sql, attempts=4):
    """Metabase, retrying the transport only - a bad query fails the same way every time."""
    key = os.environ["MB_KEY"]
    import time
    for i in range(attempts):
        try:
            r = requests.post("https://metabase.wiom.in/api/dataset",
                              headers={"x-api-key": key, "Content-Type": "application/json"},
                              json={"database": 113, "type": "native",
                                    "native": {"query": sql}}, timeout=300)
            if r.status_code >= 500:
                raise requests.HTTPError("HTTP %d" % r.status_code)
            d = r.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError, ValueError):
            if i == attempts - 1:
                raise
            time.sleep(8 * (i + 1))
            continue
        if isinstance(d, dict) and d.get("error"):
            raise SystemExit("Metabase: %s" % str(d["error"])[:400])
        return d["data"]["rows"]


def winbacks():
    """{csp: (name, owner, first winback date)} from the console's own visit log.

    The EARLIEST yes wins. A CSP won back on the 23rd and visited again on the 26th was won
    back on the 23rd; dating him later would quietly shorten the window he is measured over."""
    gc = gspread.service_account(filename=sa_file())
    v = gc.open_by_key(BOOK).worksheet(TAB).get_all_values()
    h = v[1]
    iC, iN, iO, iW, iD = (h.index("CSP Id"), h.index("Partner Name"), h.index("Owner"),
                          h.index("Winback"), h.index("Date"))
    out = {}
    for r in v[2:]:
        if len(r) <= iW or (r[iW] or "").strip().lower() != "yes":
            continue
        cid, d = (r[iC] or "").strip(), (r[iD] or "").strip()
        if not cid or d.count("/") != 2:
            continue
        dd, mm, yy = d.split("/")
        iso = "%s-%s-%s" % (yy, mm, dd)
        if cid not in out or iso < out[cid][2]:
            out[cid] = ((r[iN] or "").strip() or cid, (r[iO] or "").strip() or "-", iso)
    return out


DAILY_SQL = """
  WITH c AS (
    SELECT CSP_ID, CONNECTION_ID,
           TO_DATE(CONVERT_TIMEZONE('Asia/Kolkata', MIN(CREATED_AT))) AS d,
           MAX(IFF(EXECUTOR_ID IS NOT NULL,1,0)) AS asg,
           MAX(IFF(OTP_VERIFIED OR INSTALLATION_COMPLETED_AT IS NOT NULL
                   OR COMPLETED_STEP>=7,1,0)) AS ins,
           MAX(IFF(CURRENT_STATE='DECLINED',1,0)) AS dec,
           MAX(IFF(CURRENT_STATE='CANCELLED_BY_UPSTREAM'
                   AND COALESCE(REASON_CODE,'') LIKE 'TIMEOUT_P41%'
                   AND EXECUTOR_ID IS NULL,1,0)) AS nores
    FROM {iec}
    WHERE ETL_CURRENT AND CANDIDATE_TYPE = 'PRE_INSTALL'
      AND CSP_ID IN ({ids})
      AND CONVERT_TIMEZONE('Asia/Kolkata', CREATED_AT)
          >= DATEADD(day, -{days}, CURRENT_DATE())
    GROUP BY 1,2)
  SELECT CSP_ID, TO_CHAR(d,'YYYY-MM-DD'), COUNT(*), SUM(asg), SUM(dec), SUM(ins), SUM(nores)
  FROM c GROUP BY 1,2 ORDER BY 1,2"""


def daily(ids):
    """{csp: [[day, offered, assigned, declined, installed, no_response], ...]} ascending.

    One row per connection, dated by its FIRST offer to that CSP, so a retried allocation does
    not make the same customer count twice."""
    out = {}
    ids = sorted(ids)
    for i in range(0, len(ids), CHUNK):
        part = ids[i:i + CHUNK]
        inl = ",".join("'%s'" % c for c in part)
        for r in mb(DAILY_SQL.format(iec=IEC, ids=inl, days=DAILY_DAYS)):
            out.setdefault(r[0], []).append([r[1], int(r[2] or 0), int(r[3] or 0),
                                             int(r[4] or 0), int(r[5] or 0), int(r[6] or 0)])
    return out


def build(wb, dl):
    """Produces exactly the shape the laptop version does, so draw() is untouched."""
    today = datetime.datetime.now(IST).date()
    yest = today - datetime.timedelta(days=1)
    if not wb:
        return None, [], []
    first = min(v[2] for v in wb.values())
    start = max(datetime.date.fromisoformat(first),
                yest - datetime.timedelta(days=MAXCOLS - 1))
    days, d = [], yest
    while d >= start:
        days.append(d.isoformat())
        d -= datetime.timedelta(days=1)
    yday = days[0] if days else None
    rows = []
    for c, (nm, owner, since) in wb.items():
        by = {r[0]: r for r in dl.get(c, [])}
        cells, tot = [], [0, 0, 0, 0]
        for day in days:
            pre = day < since
            r = by.get(day)
            off = r[1] if r else 0
            no = r[5] if r else 0
            cells.append({"pre": pre, "off": off,
                          "pct": (round(100.0 * no / off) if off else None)})
            if not pre and r:
                tot[0] += off
                tot[1] += r[2] + r[3]
                tot[2] += r[4]
                tot[3] += no
        # None, not zero, when yesterday predates his winback: "nothing to report" and "he
        # ignored nothing" are different statements and must not share a glyph.
        live = bool(yday) and yday >= since
        yr = by.get(yday) if live else None
        y = ([yr[1], yr[2] + yr[3], yr[4], yr[5]] if yr else ([0, 0, 0, 0] if live else None))
        rows.append({"cid": c, "name": nm, "owner": owner, "since": since, "cells": cells,
                     "tot": tot, "y": y,
                     "score": (100.0 * tot[3] / tot[0]) if tot[0] else -1, "lost": tot[3]})
    quiet = [r for r in rows if r["tot"][0] == 0]
    rows = [r for r in rows if r["tot"][0] > 0]
    rows.sort(key=lambda r: (-((r["y"] or [0, 0, 0, 0])[3]), -r["score"], -r["lost"],
                             r["name"]))
    return days, rows, quiet


def heat(pct):
    """Not-responded share -> colour. Fixed bands, not relative: this image is read at 8am by
    somebody deciding who to ring, and a CSP at 80% is a bad morning whether or not he was at
    90% yesterday."""
    if pct is None:
        return "#f2f4f6", MUTED
    if pct >= 75:
        return "#c0392b", "#ffffff"
    if pct >= 50:
        return "#e07b39", "#ffffff"
    if pct >= 25:
        return "#f0c419", INK
    if pct > 0:
        return "#d6e9c6", INK
    return "#4a9d5f", "#ffffff"


def draw(days, rows, title, sub, path, show_owner=False, quiet=0):
    nrow = len(rows)
    left = 5.6 if show_owner else 4.6
    cw, rh = 0.62, 0.42
    W = left + len(days) * cw + 2.9
    # the footer runs to two lines when quiet CSPs were dropped, and the second was clipping
    H = 1.55 + nrow * rh + (0.98 if quiet else 0.75)
    fig = plt.figure(figsize=(W, H), dpi=190)
    fig.patch.set_facecolor(PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    ax.text(0.34, H - 0.42, title, fontsize=14.5, fontweight="bold", color=INK, va="center")
    ax.text(0.34, H - 0.78, sub, fontsize=8.6, color=MUTED, va="center")

    top = H - 1.18
    ax.text(0.34, top, "CSP", fontsize=8.2, fontweight="bold", color=MUTED, va="center")
    if show_owner:
        ax.text(3.35, top, "Owner", fontsize=8.2, fontweight="bold", color=MUTED, va="center")
    ax.text(left - 0.75, top, "won back", fontsize=8.2, fontweight="bold", color=MUTED,
            va="center", ha="center")
    for i, day in enumerate(days):
        dt = datetime.date.fromisoformat(day)
        x = left + i * cw + cw / 2
        ax.text(x, top + 0.13, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()],
                fontsize=6.4, color=MUTED, ha="center", va="center")
        ax.text(x, top - 0.09, dt.strftime("%d/%m"), fontsize=7.0, color=INK, ha="center",
                va="center", fontweight="bold")
    rx = left + len(days) * cw + 0.16
    if days:
        dt = datetime.date.fromisoformat(days[0])
        ax.text(rx + 1.36, top + 0.32, "YESTERDAY \u00b7 " +
                ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()] + " " +
                dt.strftime("%d/%m"), fontsize=8.0, fontweight="bold", color=INK,
                ha="center", va="center")
        ax.plot([rx + 0.08, rx + 2.64], [top + 0.21, top + 0.21], color=INK, lw=.8)
    for j, lab in enumerate(["offered", "assigned", "installed", "no-resp"]):
        ax.text(rx + j * 0.68 + 0.34, top, lab, fontsize=7.4, fontweight="bold", color=MUTED,
                ha="center", va="center")

    y = top - 0.36
    for n, r in enumerate(rows):
        if n % 2:
            ax.add_patch(Rectangle((0.2, y - rh + 0.06), W - 0.4, rh - 0.04,
                                   facecolor=BAND, edgecolor="none", zorder=0))
        nm = r["name"]
        ax.text(0.34, y - rh / 2 + 0.04, nm[:30] + ("…" if len(nm) > 30 else ""),
                fontsize=8.4, color=INK, va="center", fontweight="bold", zorder=2)
        if show_owner:
            ax.text(3.35, y - rh / 2 + 0.04, r["owner"][:11], fontsize=8.0, color=MUTED,
                    va="center", zorder=2)
        ax.text(left - 0.75, y - rh / 2 + 0.04, r["since"][8:] + "/" + r["since"][5:7],
                fontsize=7.6, color=MUTED, va="center", ha="center", zorder=2)
        for i, cell in enumerate(r["cells"]):
            x = left + i * cw
            if cell["pre"]:
                ax.add_patch(Rectangle((x + 0.03, y - rh + 0.09), cw - 0.06, rh - 0.1,
                                       facecolor="#ffffff", edgecolor=LINE, lw=.5,
                                       hatch="///", zorder=1))
                continue
            bg, fg = heat(cell["pct"])
            ax.add_patch(Rectangle((x + 0.03, y - rh + 0.09), cw - 0.06, rh - 0.1,
                                   facecolor=bg, edgecolor="none", zorder=1))
            if cell["pct"] is None:
                ax.text(x + cw / 2, y - rh / 2 + 0.04, "·", fontsize=9, color=MUTED,
                        ha="center", va="center", zorder=2)
            else:
                ax.text(x + cw / 2, y - rh / 2 + 0.10, "%d%%" % cell["pct"], fontsize=7.3,
                        color=fg, ha="center", va="center", fontweight="bold", zorder=2)
                ax.text(x + cw / 2, y - rh / 2 - 0.08, "of %d" % cell["off"], fontsize=5.4,
                        color=fg, ha="center", va="center", zorder=2)
        t = r["y"]
        for j in range(4):
            v = "\u2014" if t is None else str(t[j])
            col = "#c0392b" if (t and j == 3 and t[j]) else (MUTED if t is None else INK)
            ax.text(rx + j * 0.68 + 0.34, y - rh / 2 + 0.04, v, fontsize=8.2,
                    color=col, ha="center", va="center",
                    fontweight="bold" if j == 3 else "normal", zorder=2)
        y -= rh

    ax.plot([0.2, W - 0.2], [y + 0.06, y + 0.06], color=LINE, lw=.8)
    Y = [sum((r["y"] or [0, 0, 0, 0])[i] for r in rows) for i in range(4)]
    S = [sum(r["tot"][i] for r in rows) for i in range(4)]
    ax.text(0.34, y - 0.2, "TOTAL  %d CSPs" % len(rows), fontsize=8.6, fontweight="bold",
            color=INK, va="center")
    for j, v in enumerate(Y):
        ax.text(rx + j * 0.68 + 0.34, y - 0.2, str(v), fontsize=8.6, fontweight="bold",
                color="#c0392b" if j == 3 else INK, ha="center", va="center")
    pct = round(100.0 * S[3] / S[0]) if S[0] else 0
    ax.text(0.34, y - 0.52,
            "Since winback: %d offered \u00b7 %d answered \u00b7 %d installed \u00b7 "
            "%d ignored (%d%%)      cell = share of that day's leads he never answered   "
            "\u00b7   hatched = before he was won back   \u00b7   \u00b7 = no leads that day"
            % (S[0], S[1], S[2], S[3], pct), fontsize=7.0, color=MUTED, va="center")
    if quiet:
        ax.text(0.34, y - 0.74, "%d more won-back CSP%s had no leads at all in this window, "
                "not shown." % (quiet, "" if quiet == 1 else "s"),
                fontsize=7.0, color=MUTED, va="center")
    fig.savefig(path, facecolor=PAPER)
    plt.close(fig)
    return path


def token():
    return os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("PAYOUT750_SLACK_TOKEN")


def check(targets):
    """Read-only: can this bot see, and post into, each destination? Sends nothing.

    Exists because the alternative way to find out is to spam five people at 8am."""
    tok = token()
    if not tok:
        print("  no token - cannot check")
        return
    H = {"Authorization": "Bearer " + tok}
    a = requests.post("https://slack.com/api/auth.test", headers=H)
    j = a.json()
    print("  auth.test ok=%s bot=%s team=%s" % (j.get("ok"), j.get("user"), j.get("team")))
    print("  granted scopes: %s" % a.headers.get("x-oauth-scopes", "(not reported)"))
    for who, ch in targets:
        if not ch:
            print("  %-12s NO DESTINATION - nothing will be sent" % who)
            continue
        r = requests.get("https://slack.com/api/conversations.info", headers=H,
                         params={"channel": ch}).json()
        if r.get("ok"):
            c = r["channel"]
            print("  %-12s %-14s reachable  is_im=%s member=%s"
                  % (who, ch, c.get("is_im"), c.get("is_member")))
        elif r.get("error") == "missing_scope":
            # channels:read is not granted. That limits READING channel metadata; posting runs
            # on chat:write and files:write, which are. Not a reason to hold the send.
            print("  %-12s %-14s cannot read metadata (no channels:read) - posting is "
                  "unaffected" % (who, ch))
        else:
            print("  %-12s %-14s CANNOT REACH: %s" % (who, ch, r.get("error")))


def post(paths, note, ch):
    """Upload the given images into one conversation."""
    tok = token()
    if not tok:
        print("  no token - rendered only, nothing posted")
        return False
    if not ch:
        print("  no destination - nothing posted")
        return False
    H = {"Authorization": "Bearer " + tok}
    for k, p in enumerate(paths):
        j = requests.post("https://slack.com/api/files.getUploadURLExternal", headers=H,
                          data={"filename": os.path.basename(p),
                                "length": os.path.getsize(p)}).json()
        requests.post(j["upload_url"], data=io.open(p, "rb").read())
        r = requests.post("https://slack.com/api/files.completeUploadExternal", headers=H,
                          data={"files": json.dumps([{"id": j["file_id"],
                                                      "title": os.path.basename(p)}]),
                                "channel_id": ch,
                                "initial_comment": note if k == 0 else ""}).json()
        print("  -> %-28s %-14s ok=%s %s" % (os.path.basename(p), ch, r.get("ok"),
                                               r.get("error") or ""))
    return True




def main():
    os.makedirs(OUT, exist_ok=True)
    wb = winbacks()
    print("won-back CSPs in the log: %d" % len(wb), flush=True)
    dl = daily(list(wb))
    print("daily rows: %d across %d CSPs" % (sum(len(v) for v in dl.values()), len(dl)),
          flush=True)
    days, rows, quiet = build(wb, dl)
    if not rows:
        print("nothing to render")
        return
    stamp = datetime.datetime.now(IST).strftime("%d %b %Y")
    paths = []
    p = os.path.join(OUT, "bd_winback_ALL.png")
    draw(days, rows, "Soft winback \u00b7 all owners",
         "%d CSPs \u00b7 day by day since each was won back \u00b7 %s \u00b7 worst first"
         % (len(rows), stamp), p, show_owner=True, quiet=len(quiet))
    paths.append(p)
    print("  %-26s %3d CSPs" % ("ALL", len(rows)), flush=True)
    by_owner = {}
    for owner in sorted({r["owner"] for r in rows}):
        mine = [r for r in rows if r["owner"] == owner]
        qn = len([q for q in quiet if q["owner"] == owner])
        q = os.path.join(OUT, "bd_winback_%s.png" % owner.replace(" ", "_"))
        draw(days, mine, "%s \u00b7 soft winback" % owner,
             "%d CSPs \u00b7 day by day since each was won back \u00b7 %s \u00b7 worst first"
             % (len(mine), stamp), q, quiet=qn)
        paths.append(q)
        by_owner[owner] = (q, len(mine), qn)
        print("  %-26s %3d CSPs  -> %s" % (owner, len(mine), dm_for(owner) or "NO DM SET"),
              flush=True)
    A = [sum((r["y"] or [0, 0, 0, 0])[i] for r in rows) for i in range(4)]
    head = ("*Soft winback \u2014 daily snapshot (%s)*\n"
            "Yesterday: %d offered \u00b7 %d assigned \u00b7 %d installed \u00b7 "
            "*%d ignored*. Worst non-responder first; each cell is the share of that day's "
            "leads the CSP never answered." % (stamp, A[0], A[1], A[2], A[3]))
    lead_ch = os.environ.get("SLACK_CHANNEL_ID")

    # PREVIEW: one person sees everything and nobody else is touched. Deliberately an
    # early return - a preview that also posted to the owners would not be a preview.
    prev = os.environ.get("PREVIEW_USER", "").strip()
    if prev:
        ch = dm_channel(prev)
        print("PREVIEW -> %s (%s) - owners and the channel are NOT posted to" % (prev, ch))
        post(paths, head + "\n\n_Preview only \u2014 the owners and #execution-leadership "
             "have not been sent anything. The summary is first, then one per owner._", ch)
        return

    if os.environ.get("CHECK_ONLY") == "1":
        print("CHECK_ONLY=1 - verifying access, sending nothing")
        check([(o, dm_for(o)) for o in sorted(by_owner)] + [("leadership", lead_ch)])
        return
    if os.environ.get("DRY_RUN") == "1":
        print("DRY_RUN=1 - rendered only, nothing posted")
        return

    # each owner gets only his own
    for owner, (img, n, qn) in sorted(by_owner.items()):
        y = [sum((r["y"] or [0, 0, 0, 0])[i] for r in rows if r["owner"] == owner)
             for i in range(4)]
        post([img], "*Your winback CSPs \u2014 %s*\nYesterday: %d offered \u00b7 %d assigned "
             "\u00b7 %d installed \u00b7 *%d ignored*. Worst first \u2014 start at the top."
             % (stamp, y[0], y[1], y[2], y[3]), dm_for(owner))
    # leadership gets the summary first, then every owner's
    post(paths, head, lead_ch)


if __name__ == "__main__":
    main()
