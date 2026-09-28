# -*- coding: utf-8 -*-
"""Delhi devices at CSP office on 22-Sep-2026, frozen, then followed live.

  all active CSPs -> Delhi -> devices at a CSP office on 22-Sep
      |  frozen at 22-Sep
      +-- Idle / Custodied / Retrieval pending
             |  live from here
             +-- idle & custodied : carry fee applying | not applying |
                                    retrieval pending | returned+deployed+other
             +-- retrieval pending: now idle | now custodied | still pending |
                                    returned+deployed+other
                 (now idle / now custodied split again by carry fee)

PENDING_CSP_RECEIPT counts inside Custodied, per the earlier ruling on this project.
Reads tree22.json / tree22_meta.json from the companion query.
"""
import os, json, datetime

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

PINK, INK, MUTED, LINE = "#d9008d", "#2b2b2b", "#7a7a7a", "#c9c2c6"
NEUT = "#f2efec"
CF_BG, CF_FG = "#fdefe1", "#a85b12"       # carry fee applying
OK_BG, OK_FG = "#e4f3e8", "#1d7a3a"       # gone / returned
HOLD_BG, HOLD_FG = "#f3eef1", "#8a6d7d"   # still sitting
RP_BG, RP_FG = "#ece9f7", "#5b4a9e"       # retrieval pending
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")
GONE = "No longer\nat CSP office"


def box(ax, x, y, w, h, title, val, sub=None, fill="white", edge=LINE, fg=INK,
        title_size=8.5, val_size=12, lw=1.2):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle="round,pad=0.3,rounding_size=1.0",
                                linewidth=lw, edgecolor=edge, facecolor=fill, zorder=2))
    ax.text(x, y + h * 0.23, title, ha="center", va="center", fontsize=title_size,
            color=fg, fontweight="bold", zorder=3, linespacing=1.3)
    ax.text(x, y - h * 0.20, "{:,}".format(val), ha="center", va="center",
            fontsize=val_size, color=fg, fontweight="bold", zorder=3)
    if sub:
        ax.text(x, y - h * 0.43, sub, ha="center", va="center", fontsize=6.6,
                color=MUTED, zorder=3)


def elbow(ax, x0, y0, x1, y1, color=LINE):
    mid = (y0 + y1) / 2
    ax.plot([x0, x0], [y0, mid], color=color, lw=1.1, zorder=1)
    ax.plot([x0, x1], [mid, mid], color=color, lw=1.1, zorder=1)
    ax.plot([x1, x1], [mid, y1], color=color, lw=1.1, zorder=1)


def draw(d, meta, OTHER, stamp, path):
    fig = plt.figure(figsize=(26, 13), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(2, 100); ax.axis("off")

    grand = sum(d[b]["total"] for b in d)
    ax.text(50, 96.5, "DELHI — devices at CSP office on 22-Sep-2026, and where they went",
            ha="center", fontsize=19, color=PINK, fontweight="bold")
    ax.text(50, 93.6, "cohort frozen at 22-Sep  ·  everything below the second line is live as of %s"
            % stamp, ha="center", fontsize=10, color=MUTED)

    box(ax, 50, 88, 26, 5.4, "Active CSPs in system", meta["active"],
        sub="Delhi %s  ·  Mumbai %s  ·  Bharat %s  ·  no city on record %s"
            % tuple("{:,}".format(meta[k]) for k in ("Delhi", "Mumbai", "Bharat", "nocity")),
        fill=NEUT, title_size=9.5, val_size=13)
    box(ax, 50, 79, 20, 5.2, "Delhi CSPs", meta["Delhi"], fill=NEUT,
        title_size=9.5, val_size=13)
    elbow(ax, 50, 85.3, 50, 81.6)
    box(ax, 50, 70, 26, 6.0, "Devices at CSP office  (22 Sep)", grand,
        fill=PINK, edge=PINK, fg="white", title_size=10.5, val_size=16, lw=0)
    elbow(ax, 50, 76.4, 50, 73)

    # 12 child boxes share one row of equal slots; each branch sits over its own four
    N = 12
    XS = [3 + i * (94.0 / (N - 1)) for i in range(N)]
    SLOT = {"Idle": XS[0:4], "Custodied": XS[4:8], "Retrieval pending": XS[8:12]}
    CEN = {k: sum(v) / 4.0 for k, v in SLOT.items()}
    STYLE = {"Idle": (HOLD_BG, HOLD_FG), "Custodied": (HOLD_BG, HOLD_FG),
             "Retrieval pending": (RP_BG, RP_FG)}
    for name in ("Idle", "Custodied", "Retrieval pending"):
        bg, fg = STYLE[name]
        box(ax, CEN[name], 57, 26, 6.0, name.upper(), d[name]["total"],
            sub="%.0f%% of cohort" % (100.0 * d[name]["total"] / grand),
            fill=bg, edge=fg, fg=fg, title_size=10, val_size=14, lw=1.7)
        elbow(ax, 50, 67, CEN[name], 60)

    # the freeze covers the 22-Sep status row above; everything under this line is live
    ax.plot([1, 99], [50.5, 50.5], color="#d0d0d0", lw=1, ls=(0, (6, 4)), zorder=0)
    ax.text(1.5, 52.6, "FROZEN  ·  status on 22 Sep", fontsize=8.5, color=MUTED,
            fontweight="bold", va="center")
    ax.text(1.5, 48.4, "LIVE  ·  where they are now", fontsize=8.5, color=PINK,
            fontweight="bold", va="center")

    NICE = {"DEPLOYED": "redeployed to a customer", "RETURNED": "returned to WH",
            "LOST": "lost", "WRITTEN_OFF": "written off",
            "CUSTOMER_RECOVERY_PENDING": "customer recovery pending"}

    def gone_detail(x, comp):
        """Spell out the 'other' box instead of leaving it a catch-all."""
        y = 37.0
        for st, n in sorted(comp.items(), key=lambda kv: -kv[1]):
            ax.text(x - 3.6, y, "{:,}".format(n), ha="left", va="center",
                    fontsize=6.8, color=OK_FG, fontweight="bold")
            ax.text(x + 3.7, y, NICE.get(st, st.title()), ha="right", va="center",
                    fontsize=5.6, color=MUTED)
            y -= 1.9

    def kid(x, title, val, bg, fg, sub=None):
        box(ax, x, 42.5, 8.0, 6.6, title, val, sub=sub, fill=bg, edge=fg, fg=fg,
            title_size=7.0, val_size=10.5)

    for name in ("Idle", "Custodied"):
        s2 = d[name]
        xs4 = SLOT[name]
        kids = ((xs4[0], "Carry fee\napplying", s2.get("cf", 0), CF_BG, CF_FG, None),
                (xs4[1], "Carry fee\nNOT applying", s2.get("nocf", 0), HOLD_BG, HOLD_FG, None),
                (xs4[2], "Retrieval\npending now", s2.get("rp", 0), RP_BG, RP_FG, None),
                (xs4[3], GONE, s2.get("gone", 0), OK_BG, OK_FG, None))
        for x, t, v, b2, f2, sb in kids:
            kid(x, t, v, b2, f2, sb)
            elbow(ax, CEN[name], 54, x, 45.8)
        gone_detail(xs4[3], OTHER[name])

    s = d["Retrieval pending"]
    ni = s.get("idle_cf", 0) + s.get("idle_nocf", 0)
    nc = s.get("cust_cf", 0) + s.get("cust_nocf", 0)
    xs4 = SLOT["Retrieval pending"]
    rkids = ((xs4[0], "Now idle", ni, HOLD_BG, HOLD_FG, None),
             (xs4[1], "Now custodied", nc, HOLD_BG, HOLD_FG, None),
             (xs4[2], "Still retrieval\npending", s.get("still", 0), RP_BG, RP_FG, None),
             (xs4[3], GONE, s.get("gone", 0), OK_BG, OK_FG, None))
    for x, t, v, b2, f2, sb in rkids:
        kid(x, t, v, b2, f2, sb)
        elbow(ax, CEN["Retrieval pending"], 54, x, 45.8)
    gone_detail(xs4[3], OTHER["Retrieval pending"])
    for px, pre in ((xs4[0], "idle"), (xs4[1], "cust")):
        for dx, lab, key in ((-2.1, "Carry fee\napplying", pre + "_cf"),
                             (2.1, "Carry fee\nNOT applying", pre + "_nocf")):
            hot = key.endswith("_cf")
            box(ax, px + dx, 31, 4.0, 5.6, lab, s.get(key, 0),
                fill=CF_BG if hot else HOLD_BG, edge=CF_FG if hot else HOLD_FG,
                fg=CF_FG if hot else HOLD_FG, title_size=5.4, val_size=8.5)
            elbow(ax, px, 39.2, px + dx, 33.8)

    # ---- footer ----
    cf = (d["Idle"].get("cf", 0) + d["Custodied"].get("cf", 0)
          + s.get("idle_cf", 0) + s.get("cust_cf", 0))
    rp = d["Idle"].get("rp", 0) + d["Custodied"].get("rp", 0) + s.get("still", 0)
    gone = sum(d[b].get("gone", 0) for b in d)
    ret = sum(d[b].get("gone_ret", 0) for b in d)
    stay = grand - rp - gone
    ax.add_patch(FancyBboxPatch((6, 6.5), 88, 9, boxstyle="round,pad=0.4,rounding_size=1.2",
                                linewidth=1.8, edgecolor=PINK, facecolor="white", zorder=2))
    ax.text(50, 13.2, "WHERE ALL %s ARE NOW" % "{:,}".format(grand), ha="center",
            fontsize=10.5, color=PINK, fontweight="bold", zorder=3)
    for j, (v, lab, col) in enumerate((
            (stay, "still at CSP (idle / custodied)", HOLD_FG),
            (cf, "of those, carry fee applying", CF_FG),
            (rp, "retrieval pending", RP_FG),
            (gone, "no longer at CSP office", OK_FG),
            (ret, "of those, returned to WH", OK_FG))):
        x = 14 + j * 18
        ax.text(x, 10.2, "{:,}".format(v), ha="center", va="center", fontsize=14,
                color=col, fontweight="bold", zorder=3)
        ax.text(x, 7.9, "%s  (%.0f%%)" % (lab, 100.0 * v / grand), ha="center",
                va="center", fontsize=7.4, color=MUTED, zorder=3)

    ax.text(50, 3.4, "Source: NETBOX_CUSTODY, SCD2 point-in-time slice at 22-Sep-2026 23:59 IST vs the live row.  "
                     "Active CSPs = CSP_ACCOUNT STATUS='ACTIVE'; city from PARTNER_JANAM_KUNDLI.  "
                     "PENDING_CSP_RECEIPT is counted inside Custodied.",
            ha="center", fontsize=7.6, color=MUTED)
    fig.savefig(path, dpi=120, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


if __name__ == "__main__":
    stamp = datetime.datetime.now(IST).strftime("%d %b %Y")
    d = json.load(open("tree22.json"))
    meta = json.load(open("tree22_meta.json"))
    OTHER = json.load(open("other_split.json"))
    p = draw(d, meta, OTHER, stamp, "delhi_22sep_tree.png")
    print("wrote", p, flush=True)
    if CHANNEL_ID:
        H = {"Authorization": "Bearer %s" % TOKEN}
        j = requests.post("https://slack.com/api/files.getUploadURLExternal", headers=H,
                          data={"filename": p, "length": os.path.getsize(p)}).json()
        with open(p, "rb") as fh:
            requests.post(j["upload_url"], data=fh.read())
        r = requests.post("https://slack.com/api/files.completeUploadExternal", headers=H,
                          data={"files": json.dumps([{"id": j["file_id"],
                                                      "title": "Delhi 22-Sep tree"}]),
                                "channel_id": CHANNEL_ID,
                                "initial_comment":
                                    "*Delhi — devices at CSP office 22-Sep, frozen, then followed live (%s)*"
                                    % stamp}).json()
        print("posted ok=%s %s" % (r.get("ok"), r.get("error") or ""), flush=True)
