# -*- coding: utf-8 -*-
"""Device funnel, 22-Sep-2026 cohort, split CONSENTED vs NON-CONSENTED.

Top box is every device sitting at a CSP office on 22-Sep-2026. That splits by
whether the CSP has signed the consent form (the list supplied on 28-Sep-2026:
1,076 CSPs, 750 consented). Both branches then get the SAME "where are they now"
breakdown, because consent says nothing about device state - unlike the carry-fee
chart, where the two sides legitimately differ.

Scope is the 1,076 CSPs on the consent list. Devices at CSPs outside it cannot be
classified either way and are left out; the count is printed at run time.

Reads consent_tree.json / consent_counts.json built by the companion query.
"""
import os, json, datetime, collections

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

WIOM_PINK, LIGHT_PINK, BAR_COLOR = "#d9008d", "#fae8f0", "#bb7d99"
INK, MUTED = "#2b2b2b", "#7a7a7a"
GOOD_BG, GOOD_FG = "#e4f3e8", "#1d7a3a"
WARN_BG, WARN_FG = "#fdefe1", "#a85b12"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")


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
    mid = (y0 + y1) / 2
    ax.plot([x0, x0], [y0, mid], color=color, lw=1.3, zorder=1)
    ax.plot([x0, x1], [mid, mid], color=color, lw=1.3, zorder=1)
    ax.plot([x1, x1], [mid, y1], color=color, lw=1.3, zorder=1)


def pct(n, d):
    return "%.0f%% of parent" % (100.0 * n / d) if d else ""


def draw(cty, d, stamp, ncsp):
    fig = plt.figure(figsize=(16, 8.2), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(14.5, 100)
    ax.axis("off")

    ax.text(50, 96.5, cty.upper(), ha="center", fontsize=22,
            color=WIOM_PINK, fontweight="bold")
    ax.text(50, 93.2, "%s CSPs  ·  cohort frozen at 22-Sep-2026  ·  status as of %s"
            % ("{:,}".format(ncsp), stamp), ha="center", fontsize=10.5, color=MUTED)

    box(ax, 50, 85, 30, 8, "Devices at CSP office", d["total"],
        sub="22 Sep cohort", fill=WIOM_PINK, edge=WIOM_PINK, fg="white",
        title_size=11.5, val_size=21, bold_edge=0, sub_color="#ffd6ef")

    # ---- level 1: consent ----
    for x, t, key, n in ((25, "CONSENTED", "Consented", d["Consented"]["total"]),
                         (75, "NOT CONSENTED", "Non-consented", d["Non-consented"]["total"])):
        box(ax, x, 71, 28, 7.6, t, n, sub=pct(n, d["total"]),
            fill=LIGHT_PINK, edge=WIOM_PINK, title_size=11, val_size=18, bold_edge=1.8)
        elbow(ax, 50, 81, x, 74.8)

    ax.plot([1, 99], [65.2, 65.2], color="#d0d0d0", lw=1, ls=(0, (6, 4)), zorder=0)
    ax.text(1.5, 66.6, "FIXED  ·  22 Sep cohort", fontsize=8.5, color=MUTED,
            fontweight="bold", va="center")
    ax.text(1.5, 63.7, "UPDATES DAILY  ·  where they are now", fontsize=8.5,
            color=WIOM_PINK, fontweight="bold", va="center")

    # ---- level 2 + 3, identical shape on both branches ----
    for x0, key in ((25, "Consented"), (75, "Non-consented")):
        s = d[key]
        applied = s["wh"] + s["not_recv"]
        kids = ((x0 - 13, "Applied for return", applied),
                (x0, "No return marked", s["no_mark"]),
                (x0 + 13, "Redeployed / lost", s["other"]))
        for x, t, v in kids:
            box(ax, x, 55, 12.5, 7.4, t, v, sub=pct(v, s["total"]), title_size=9)
            elbow(ax, x0, 67.2, x, 58.7)
        for x, t, v, bg, fg in ((x0 - 19, "Received at WH", s["wh"], GOOD_BG, GOOD_FG),
                                (x0 - 7, "NOT received", s["not_recv"], WARN_BG, WARN_FG)):
            box(ax, x, 38, 11, 7.4, t, v, sub=pct(v, applied),
                fill=bg, edge=fg, fg=fg)
            elbow(ax, x0 - 13, 51.5, x, 41.7)

    # ---- footer ----
    rec = d["Consented"]["wh"] + d["Non-consented"]["wh"]
    still = sum(d[k]["no_mark"] + d[k]["not_recv"] for k in ("Consented", "Non-consented"))
    ax.add_patch(FancyBboxPatch((6, 22.5), 42, 8,
                                boxstyle="round,pad=0.4,rounding_size=1.2",
                                linewidth=1.6, edgecolor=WIOM_PINK, facecolor="white", zorder=2))
    ax.text(27, 28.4, "Recovered to warehouse since 22 Sep", ha="center",
            fontsize=11, color=WIOM_PINK, fontweight="bold", zorder=3)
    ax.text(27, 25.0, "{:,}  of  {:,}   ({:.0f}%)".format(rec, d["total"],
            100.0 * rec / d["total"] if d["total"] else 0),
            ha="center", fontsize=17, color=INK, fontweight="bold", zorder=3)

    ax.add_patch(FancyBboxPatch((52, 22.5), 42, 8,
                                boxstyle="round,pad=0.4,rounding_size=1.2",
                                linewidth=1.8, edgecolor=WARN_FG, facecolor="white", zorder=2))
    ax.text(73, 28.4, "Still at CSP  (22 Sep cohort)", ha="center",
            fontsize=10.5, color=WARN_FG, fontweight="bold", zorder=3)
    ax.text(73, 25.9, "{:,}".format(still), ha="center",
            fontsize=18, color=INK, fontweight="bold", zorder=3)
    ax.text(73, 23.4, "%s consented  +  %s not consented" % (
                "{:,}".format(d["Consented"]["no_mark"] + d["Consented"]["not_recv"]),
                "{:,}".format(d["Non-consented"]["no_mark"] + d["Non-consented"]["not_recv"])),
            ha="center", fontsize=8.5, color=WARN_FG, zorder=3)

    ax.text(50, 18.5, "Source: NETBOX_CUSTODY (SCD2 point-in-time)  ·  'Received at WH' = status "
                      "RETURNED  ·  consent per the list supplied 28-Sep-2026 (1,076 CSPs, 750 consented)",
            ha="center", fontsize=8.5, color=MUTED)

    path = "funnelc_%s.png" % cty.lower()
    fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


def post(paths, stamp):
    H = {"Authorization": "Bearer %s" % TOKEN}
    ids = []
    for p in paths:
        j = requests.post("https://slack.com/api/files.getUploadURLExternal",
                          headers=H, data={"filename": p,
                                           "length": os.path.getsize(p)}).json()
        if not j.get("ok"):
            raise SystemExit("getUploadURL failed: %s" % j)
        with open(p, "rb") as fh:
            requests.post(j["upload_url"], data=fh.read())
        ids.append({"id": j["file_id"],
                    "title": p.replace("funnelc_", "").replace(".png", "").title()})
    r = requests.post("https://slack.com/api/files.completeUploadExternal", headers=H,
                      data={"files": json.dumps(ids), "channel_id": CHANNEL_ID,
                            "initial_comment":
                                "*Devices at CSP office — 22-Sep cohort — %s*\n"
                                "Split by CSP consent. Overall, then Delhi / Mumbai / Bharat." % stamp}).json()
    if not r.get("ok"):
        raise SystemExit("completeUpload failed: %s" % r)
    print("posted %d images to %s" % (len(ids), CHANNEL_ID), flush=True)


if __name__ == "__main__":
    stamp = datetime.datetime.now(IST).strftime("%d %b %Y")
    raw = json.load(open("consent_tree.json"))
    T = collections.Counter()
    for k, v in raw.items():
        cty, grp, b = k.split("|")
        T[(cty, grp, b)] = v
    ncsp = json.load(open("consent_counts.json"))

    paths = []
    for cty in ("Overall", "Delhi", "Mumbai", "Bharat"):
        d = {"total": 0}
        for grp in ("Consented", "Non-consented"):
            s = {b: T[(cty, grp, b)] for b in ("wh", "not_recv", "no_mark", "other")}
            s["total"] = sum(s.values())
            d[grp] = s
            d["total"] += s["total"]
        assert d["total"] == d["Consented"]["total"] + d["Non-consented"]["total"]
        print("%-8s total=%d  consented=%d  non=%d" % (cty, d["total"],
              d["Consented"]["total"], d["Non-consented"]["total"]), flush=True)
        paths.append(draw(cty, d, stamp, ncsp[cty]))
    if CHANNEL_ID:
        post(paths, stamp)
    else:
        print("SLACK_CHANNEL_ID unset - rendered locally only: %s" % ", ".join(paths))
