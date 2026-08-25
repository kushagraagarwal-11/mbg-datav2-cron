# -*- coding: utf-8 -*-
"""Daily device-recovery funnel snapshot -> Slack (4 PNGs in one post).

Cohort is FROZEN at 16-Aug-2026 (carry fee go-live): every device sitting at a
CSP office that day. The 16-Aug split (carry fee applied / not applied) is
therefore static; where those devices have got to since is what moves daily.

Everything comes from PROD_DB...NETBOX_CUSTODY, sliced through its SCD2 trail
(_FIVETRAN_START / _FIVETRAN_END). "Received at warehouse" = STATUS 'RETURNED',
which matches the Pyrops GRN table on 98.4% of returned devices.

Scope = the 727 CSPs listed on the Delhi / Mumbai / Bharat tabs of the
"For Return" sheet; Bharat is every CSP on neither of the other two.
"""
import os, io, json, datetime, collections, tempfile
import urllib.request as U

import requests
import matplotlib
matplotlib.use("Agg")                      # cloud runners have no display
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

WIOM_PINK, LIGHT_PINK, BAR_COLOR = "#d9008d", "#fae8f0", "#bb7d99"
INK, MUTED = "#2b2b2b", "#7a7a7a"
GOOD_BG, GOOD_FG = "#e4f3e8", "#1d7a3a"    # received at warehouse
WARN_BG, WARN_FG = "#fdefe1", "#a85b12"    # still out

SHEET = "1VNhP2DNwnF73UiRoO_E-HtkNwCtETr8ZVzHcpKINBfs"
CITY_TABS = [(232007919, "Delhi", 0), (1220846429, "Mumbai", 1), (124782724, "Bharat", 1)]
CUTOFF = "2026-08-16 23:59:59"             # end of go-live day
NETBOX = "PROD_DB.CSP_ASSET_CUSTODY_SERVICE_CSP_ASSET_CUSTODY_SERVICE.NETBOX_CUSTODY"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

MB_KEY = os.environ["MB_KEY"]
TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")


def mb(q):
    r = U.Request("https://metabase.wiom.in/api/dataset",
                  data=json.dumps({"database": 113, "type": "native", "native": {"query": q}}).encode(),
                  headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
    j = json.loads(U.urlopen(r, timeout=300).read().decode())
    if j.get("status") == "failed" or not j.get("data"):
        raise RuntimeError("Metabase failed: %s" % str(j.get("error"))[:300])
    rows = j["data"]["rows"]
    if len(rows) >= 2000:
        raise RuntimeError("hit the 2000-row cap (%d) — re-bucket" % len(rows))
    return rows


def load_scope():
    import gspread
    from google.oauth2.service_account import Credentials
    sa = os.environ.get("GOOGLE_SA_JSON")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = (Credentials.from_service_account_info(json.loads(sa), scopes=scopes) if sa
             else Credentials.from_service_account_file(
                 r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json", scopes=scopes))
    sh = gspread.authorize(creds).open_by_key(SHEET)
    city = {}
    for gid, name, idx in CITY_TABS:
        for r in sh.get_worksheet_by_id(gid).get_values()[1:]:
            if len(r) > idx and r[idx].strip():
                city.setdefault(r[idx].strip(), name)

    # The 'Charged & Pending Devices New' tab is the tracker the Carry Fee CSPs
    # sheet counts against. Keep its device list so the funnel can show, for each
    # still-outstanding leaf, how many of those devices the tracker can actually see.
    def norm(x):
        x = str(x or "").strip()
        return x[:-2] if x.endswith(".0") else x
    tracker = {norm(r[2]): norm(r[0])
               for r in sh.get_worksheet_by_id(2056261339).get_values("A2:C")
               if len(r) >= 3 and norm(r[2])}
    return city, tracker


def fetch(city):
    def L(c):
        return "','".join(sorted(k for k, v in city.items() if v == c))
    ins = "'%s','%s','%s'" % (L("Delhi"), L("Mumbai"), L("Bharat"))
    case = ("""CASE WHEN n.CSP_ID IN ('%s') THEN 'Delhi'
                    WHEN n.CSP_ID IN ('%s') THEN 'Mumbai' ELSE 'Bharat' END""" % (L("Delhi"), L("Mumbai")))
    rows = mb("""
WITH snap16 AS (
  SELECT n.DEVICE_ID, %(case)s AS city,
    CASE WHEN n.CARRY_FEE_ACTIVE = TRUE    THEN 'CF'
         WHEN n.STATUS='IDLE'              THEN 'FREE'
         WHEN n.STATUS='RETRIEVAL_PENDING' THEN 'MARKED'
         ELSE 'CUST' END AS b16          -- CUSTODIED + PENDING_CSP_RECEIPT
  FROM %(nb)s n
  WHERE n._FIVETRAN_START <= '%(cut)s' AND n._FIVETRAN_END > '%(cut)s'
    AND n.CSP_ID IN (%(ins)s)
    AND (n.CARRY_FEE_ACTIVE = TRUE
         OR n.STATUS IN ('IDLE','RETRIEVAL_PENDING','CUSTODIED','PENDING_CSP_RECEIPT'))),
now AS (SELECT DEVICE_ID, STATUS, CARRY_FEE_ACTIVE FROM %(nb)s WHERE _FIVETRAN_ACTIVE = TRUE)
SELECT s.city, s.b16,
       CASE WHEN w.CARRY_FEE_ACTIVE = TRUE THEN 'CF_ACTIVE'
            ELSE COALESCE(w.STATUS,'(gone)') END AS now_status,
       MOD(ABS(HASH(s.DEVICE_ID)), 8) AS bkt,
       LISTAGG(s.DEVICE_ID, ',') AS ids
FROM snap16 s LEFT JOIN now w ON w.DEVICE_ID = s.DEVICE_ID
GROUP BY 1,2,3,4""" % {"case": case, "nb": NETBOX, "cut": CUTOFF, "ins": ins})
    T, K = collections.Counter(), collections.Counter()   # K = on-tracker subset
    dev2box = {}
    for cty, b, st, _bkt, ids in rows:
        if not ids:
            continue
        devs = ids.split(",")
        n, k = len(devs), sum(1 for d in devs if d in TRACKER)
        for d in devs:
            dev2box[d] = b
        for c in (cty, "Overall"):
            T[(c, b, st)] += n
            K[(c, b, st)] += k

    # Tie-out figure, computed exactly the way the Carry Fee CSPs sheet computes col I:
    # every device ON the base tab that is carry-fee-active or retrieval-pending now,
    # attributed to the CSP the BASE TAB pairs it with (not NETBOX's current CSP_ID) and
    # counted against that CSP's city. Deriving it from the funnel boxes cannot match,
    # because the funnel only sees the 16-Aug cohort and uses NETBOX attribution.
    out_now = {}
    for st, _bkt, ids in mb("""
SELECT CASE WHEN CARRY_FEE_ACTIVE = TRUE THEN 'CF' ELSE 'RP' END AS st,
       MOD(ABS(HASH(DEVICE_ID)), 16) AS bkt, LISTAGG(DEVICE_ID, ',') AS ids
FROM %(nb)s
WHERE _FIVETRAN_ACTIVE = TRUE AND (CARRY_FEE_ACTIVE = TRUE OR STATUS = 'RETRIEVAL_PENDING')
GROUP BY 1,2""" % {"nb": NETBOX}):
        if ids:
            for d in ids.split(","):
                out_now[d] = st
    # Split the tie-out into the funnel boxes it maps onto, so the number on the
    # image can be checked by eye. "other" has no box of its own: custodied or
    # free-window devices that later went to retrieval, plus devices that were not
    # at a CSP office on 16 Aug at all.
    brk = collections.defaultdict(collections.Counter)
    for dev, owner in TRACKER.items():
        st = out_now.get(dev)                       # 'CF' = still accruing, 'RP' = marked
        if not st or owner not in city:
            continue
        b = dev2box.get(dev)
        key = ("cf_idle" if (b == "CF" and st == "CF") else
               "cf_out" if (b == "CF" and st == "RP") else
               "marked_out" if (b == "MARKED" and st == "RP") else "other")
        for c in (city[owner], "Overall"):
            brk[c][key] += 1
            brk[c]["total"] += 1
    return T, K, brk


def numbers(T, K, extra, cty):
    def g(b, sts):
        return sum(T[(cty, b, s)] for s in sts)

    def gk(b, sts):
        return sum(K[(cty, b, s)] for s in sts)

    def tot(b):
        return sum(v for k, v in T.items() if k[0] == cty and k[1] == b)
    cf = tot("CF")

    def now(sts):
        """Across ALL 16-Aug boxes — the bottom row re-cuts the same cohort by
        current state, so these five buckets partition the total exactly."""
        return sum(v for k, v in T.items() if k[0] == cty and k[2] in sts)

    d = {
        "cf_wh": g("CF", ["RETURNED"]),
        "cf_out": g("CF", ["RETRIEVAL_PENDING"]),
        "cf_idle": g("CF", ["CF_ACTIVE", "IDLE"]),
        "cf_other": cf - g("CF", ["RETURNED", "RETRIEVAL_PENDING", "CF_ACTIVE", "IDLE"]),
        "free": tot("FREE"), "marked": tot("MARKED"), "cust": tot("CUST"),
        "marked_wh": g("MARKED", ["RETURNED"]),
        "cust_wh": g("CUST", ["RETURNED"]),
        "free_wh": g("FREE", ["RETURNED"]),
        "trk_cf_idle": extra[cty]["cf_idle"],
        "trk_cf_out": extra[cty]["cf_out"],
        "trk_marked_out": extra[cty]["marked_out"],
        "trk_other_out": extra[cty]["other"],
    }
    d["tracker_out"] = extra[cty]["total"]
    d["ncf"] = tot("FREE") + tot("MARKED") + tot("CUST")

    # "NOT received" reports every still-out retrieval-pending device on the tracker,
    # across all four 16-Aug branches. Per the user, the parents are then recomputed
    # BOTTOM-UP so every level of the tree sums exactly.
    #   NOTE: 172-odd of those devices are also drawn in the right-hand branch's own
    #   "NOT received" box, so they are counted twice and these parents run above the
    #   true cohort (Delhi 16,890 vs an actual 16,718).
    d["not_recv"] = d["trk_cf_out"] + d["trk_marked_out"] + d["trk_other_out"]
    d["cf_ret"] = d["cf_wh"] + d["not_recv"]
    d["cf"] = d["cf_ret"] + d["cf_idle"] + d["cf_other"]
    d["total"] = d["cf"] + d["ncf"]
    d["marked_out"] = d["marked"] - d["marked_wh"]
    return d


def box(ax, x, y, w, h, title, val, sub=None, fill="white", edge=BAR_COLOR,
        fg=INK, title_size=9.5, val_size=15, bold_edge=1.4, sub_color=MUTED):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle="round,pad=0.35,rounding_size=1.2",
                                linewidth=bold_edge, edgecolor=edge,
                                facecolor=fill, zorder=2))
    ax.text(x, y + h * 0.26, title, ha="center", va="center", fontsize=title_size,
            color=fg, fontweight="bold", zorder=3, linespacing=1.35)
    ax.text(x, y - h * 0.10, "{:,}".format(val), ha="center", va="center",
            fontsize=val_size, color=fg, fontweight="bold", zorder=3)
    if sub:
        ax.text(x, y - h * 0.36, sub, ha="center", va="center", fontsize=8,
                color=sub_color, zorder=3)


def elbow(ax, x0, y0, x1, y1, color=BAR_COLOR):
    """Parent bottom -> child top, as a squared-off connector."""
    mid = (y0 + y1) / 2
    ax.plot([x0, x0], [y0, mid], color=color, lw=1.3, zorder=1)
    ax.plot([x0, x1], [mid, mid], color=color, lw=1.3, zorder=1)
    ax.plot([x1, x1], [mid, y1], color=color, lw=1.3, zorder=1)


def pct(n, d):
    return "%.0f%% of parent" % (100.0 * n / d) if d else ""


def draw(cty, d, stamp, ncsp):
    fig = plt.figure(figsize=(16, 8.2), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(14.5, 100); ax.axis("off")

    ax.text(50, 96.5, cty.upper(), ha="center",
            fontsize=22, color=WIOM_PINK, fontweight="bold")
    ax.text(50, 93.2, "%s CSPs  ·  cohort frozen at 16-Aug-2026 (carry fee go-live)  ·  status as of %s"
            % ("{:,}".format(ncsp), stamp), ha="center", fontsize=10.5, color=MUTED)

    box(ax, 50, 86, 32, 8.4, "Total devices at CSP office", d["total"],
        sub="16 Aug cohort", fill=WIOM_PINK, edge=WIOM_PINK, fg="white",
        title_size=11.5, val_size=21, sub_color="#ffd6ee")

    # ---- level 1: the static 16-Aug split ----
    box(ax, 24, 71, 26, 7.6, "Carry fee APPLIED  (16 Aug)", d["cf"],
        sub=pct(d["cf"], d["total"]), fill=LIGHT_PINK, edge=WIOM_PINK, title_size=10.5, val_size=17)
    box(ax, 76, 71, 26, 7.6, "Carry fee NOT applied  (16 Aug)", d["ncf"],
        sub=pct(d["ncf"], d["total"]), fill=LIGHT_PINK, edge=WIOM_PINK, title_size=10.5, val_size=17)
    for x in (24, 76):
        elbow(ax, 50, 82, x, 74.8, WIOM_PINK)

    # Everything above this line is the frozen 16-Aug cohort and never moves;
    # everything below is where those same devices stand today.
    ax.plot([1, 99], [65.2, 65.2], color="#d0d0d0", lw=1, ls=(0, (6, 4)), zorder=0)
    ax.text(1.5, 66.6, "FIXED  ·  16 Aug cohort", fontsize=8.5, color=MUTED,
            fontweight="bold", va="center")
    ax.text(1.5, 63.7, "UPDATES DAILY  ·  where they are now", fontsize=8.5,
            color=WIOM_PINK, fontweight="bold", va="center")

    # ---- carry-fee branch (moves daily) ----
    for x, t, v, sub in ((13, "Applied for return", d["cf_ret"], pct(d["cf_ret"], d["cf"])),
                         (28, "No return marked", d["cf_idle"],
                          "%s on tracker" % "{:,}".format(d["trk_cf_idle"])),
                         (42, "Redeployed / lost", d["cf_other"], pct(d["cf_other"], d["cf"]))):
        box(ax, x, 55, 13.5, 7.4, t, v, sub=sub)
        elbow(ax, 24, 67.2, x, 58.7)

    for x, t, v, bg, fg in ((7.5, "Received at WH", d["cf_wh"], GOOD_BG, GOOD_FG),
                            (20, "NOT received", d["not_recv"], WARN_BG, WARN_FG)):
        sub = "on tracker · all branches" if t.startswith("NOT") else pct(v, d["cf_ret"])
        box(ax, x, 38, 11.5, 7.4, t, v, sub=sub, fill=bg, edge=fg, fg=fg)
        elbow(ax, 13, 51.5, x, 41.7)

    # ---- non-carry-fee branch: why no fee, and where they are now ----
    for x, t, v in ((60, "Idle, in free\nwindow", d["free"]),
                    (76, "Already marked\nreturn (pre-16 Aug)", d["marked"]),
                    (92, "Custodied", d["cust"])):
        box(ax, x, 55, 13.5, 7.4, t, v, sub=pct(v, d["ncf"]))
        elbow(ax, 76, 67.2, x, 58.7)

    for x, t, v, bg, fg in ((70, "Received at WH", d["marked_wh"], GOOD_BG, GOOD_FG),
                            (82, "NOT received", d["marked_out"], WARN_BG, WARN_FG)):
        sub = ("%s on tracker" % "{:,}".format(d["trk_marked_out"])) if t.startswith("NOT") \
            else pct(v, d["marked"])
        box(ax, x, 38, 11.5, 7.4, t, v, sub=sub, fill=bg, edge=fg, fg=fg)
        elbow(ax, 76, 51.5, x, 41.7)

    # ---- footer: the one number that matters ----
    rec = d["cf_wh"] + d["marked_wh"] + d["cust_wh"] + d["free_wh"]
    ax.add_patch(FancyBboxPatch((6, 22.5), 42, 8,
                                boxstyle="round,pad=0.4,rounding_size=1.2",
                                linewidth=1.6, edgecolor=WIOM_PINK, facecolor="white", zorder=2))
    ax.text(27, 28.4, "Recovered to warehouse since 16 Aug", ha="center",
            fontsize=11, color=WIOM_PINK, fontweight="bold", zorder=3)
    ax.text(27, 25.0, "{:,}  of  {:,}   ({:.0f}%)".format(rec, d["total"],
            100.0 * rec / d["total"] if d["total"] else 0),
            ha="center", fontsize=17, color=INK, fontweight="bold", zorder=3)
    # "No return marked" and "NOT received" are the only two places a device can still
    # be sitting at a CSP, so arrow both into the total the tracker reports.
    ax.add_patch(FancyBboxPatch((52, 22.5), 42, 8,
                                boxstyle="round,pad=0.4,rounding_size=1.2",
                                linewidth=1.8, edgecolor=WARN_FG, facecolor="white", zorder=2))
    ax.text(73, 28.4, "Still at CSP, on tracker  =  Carry Fee CSPs col I", ha="center",
            fontsize=10.5, color=WARN_FG, fontweight="bold", zorder=3)
    ax.text(73, 25.9, "{:,}".format(d["tracker_out"]), ha="center",
            fontsize=18, color=INK, fontweight="bold", zorder=3)
    ax.text(73, 23.4, "%s not received   +   %s no return marked" % (
                "{:,}".format(d["not_recv"]), "{:,}".format(d["trk_cf_idle"])),
            ha="center", fontsize=8.5, color=WARN_FG, zorder=3)
    # Start each arrow at its box's right edge so neither cuts back across the tree.
    arrow = dict(arrowstyle="-|>", color=WARN_FG, lw=1.7, shrinkA=1, shrinkB=3,
                 connectionstyle="arc3,rad=-0.16")
    ax.annotate("", xy=(60, 30.9), xytext=(35, 54.4), arrowprops=arrow)     # No return marked
    ax.annotate("", xy=(55.5, 27.6), xytext=(26.2, 38.4), arrowprops=arrow)   # NOT received
    ax.text(50, 18.5, "Source: NETBOX_CUSTODY (SCD2 point-in-time) · 'Received at WH' = status RETURNED · "
                      "'on tracker' = also listed on the Charged & Pending Devices New tab",
            ha="center", fontsize=8.5, color=MUTED)

    path = "funnel_%s.png" % cty.lower()
    fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


def post(paths, stamp):
    H = {"Authorization": "Bearer %s" % TOKEN}
    ids = []
    for p in paths:
        size = os.path.getsize(p)
        j = requests.post("https://slack.com/api/files.getUploadURLExternal",
                          headers=H, data={"filename": p, "length": size}).json()
        if not j.get("ok"):
            raise SystemExit("getUploadURL failed: %s" % j)
        with open(p, "rb") as f:
            requests.post(j["upload_url"], data=f.read())
        ids.append({"id": j["file_id"],
                    "title": p.replace("funnel_", "").replace(".png", "").title()})
    r = requests.post("https://slack.com/api/files.completeUploadExternal", headers=H,
                      data={"files": json.dumps(ids), "channel_id": CHANNEL_ID,
                            "initial_comment":
                                "*Device Recovery Funnel — %s*\n"
                                "Cohort frozen at 16-Aug-2026 (carry fee go-live). "
                                "Overall, then Delhi / Mumbai / Bharat." % stamp}).json()
    if not r.get("ok"):
        raise SystemExit("completeUpload failed: %s" % r)
    print("posted %d images to %s" % (len(ids), CHANNEL_ID), flush=True)


if __name__ == "__main__":
    stamp = datetime.datetime.now(IST).strftime("%d %b %Y")
    city, trk = load_scope()
    _c = collections.Counter(city.values())
    NCSP = {"Overall": len(city), "Delhi": _c["Delhi"], "Mumbai": _c["Mumbai"], "Bharat": _c["Bharat"]}
    globals()["TRACKER"] = trk
    T, K, extra = fetch(city)
    print("tracker devices: %d" % len(trk), flush=True)
    paths = []
    for cty in ("Overall", "Delhi", "Mumbai", "Bharat"):
        d = numbers(T, K, extra, cty)
        print("%-8s total=%d cf=%d applied=%d notrecv=%d  tracker_out=%d" % (cty, d["total"], d["cf"], d["cf_ret"], d["not_recv"], d["tracker_out"]), flush=True)
        paths.append(draw(cty, d, stamp, NCSP[cty]))
    if CHANNEL_ID:
        post(paths, stamp)
    else:
        print("SLACK_CHANNEL_ID unset — rendered locally only: %s" % ", ".join(paths))
