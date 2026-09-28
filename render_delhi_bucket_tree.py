# -*- coding: utf-8 -*-
"""Delhi device working-list tree, extended with what has happened since.

Reproduces the bucket tree from the 'Device working list' sheet exactly
(15,165 rows -> Consented / Not consented / orphaned, pickup raised or not,
then the leaf buckets), and adds the layer that tree stops short of: where
every device in each leaf actually IS now.

Note on the tree's own wording: its "Retrieval pending" branch is the sheet's
Pickup Status = 'Pickup request raised' (8,476), NOT the NETBOX
RETRIEVAL_PENDING status - only 2,634 carried that status on 22-Sep. Most
raised requests have since resolved, which is what the outcome row shows.

Reads outcome_tree.json built by the companion query.
"""
import os, json, datetime, collections

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

PINK, INK, MUTED, LINE = "#d9008d", "#2b2b2b", "#7a7a7a", "#c9c2c6"
CON_BG, CON_FG = "#e4f3e8", "#1d7a3a"          # consented
NON_BG, NON_FG = "#fdefe1", "#a85b12"          # not consented
ORP_BG, ORP_FG = "#ece9f7", "#5b4a9e"          # orphaned
NEUT_BG = "#f2efec"
GOOD, WARN, HOLD, DIM = "#1d7a3a", "#a85b12", "#8a6d7d", "#9a9a9a"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")

# terminal leaves, in drawing order: (group, leaf label, parent key)
LEAVES = [
    ("Consented",    "Aligned for\ncarry fees",   "RS_C"),
    ("Consented",    "Due for\npickup",           "RS_C"),
    ("Consented",    "Raised by\nsystem",         "PR_C"),
    ("Consented",    "Carry fee\napplying",       "NR_C"),
    ("Consented",    "Custodied",                 "CFN_C"),
    ("Consented",    "Due date\nnot reached",     "CFN_C"),
    ("NotConsented", "Raised self",               "PR_N"),
    ("NotConsented", "Raised by\nsystem",         "PR_N"),
    ("NotConsented", "Carry fee\napplying",       "NR_N"),
    ("NotConsented", "Custodied",                 "CFN_N"),
    ("NotConsented", "Due date\nnot reached",     "CFN_N"),
    ("Orphaned8",    "Raised by\nsystem",         "PR_O"),
]
# sheet leaf-bucket name for each drawn leaf (labels above are wrapped for display)
SHEET = ["Aligned for carry fees", "Due for pickup", "Raised by system", "Carry fee applying",
         "Custodied", "Due date not reached", "Raised self", "Raised by system",
         "Carry fee applying", "Custodied", "Due date not reached", "Raised by system"]

OUTS = [("returned", "returned to WH", GOOD),
        ("pending", "still retrieval-pending", WARN),
        ("at_csp", "still at CSP", HOLD),
        ("other", "redeployed / other", DIM),
        ("lost", "lost", DIM)]


def box(ax, x, y, w, h, title, val, sub=None, fill="white", edge=LINE, fg=INK,
        title_size=8.5, val_size=12, lw=1.2):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle="round,pad=0.3,rounding_size=1.0",
                                linewidth=lw, edgecolor=edge, facecolor=fill, zorder=2))
    ax.text(x, y + h * 0.22, title, ha="center", va="center", fontsize=title_size,
            color=fg, fontweight="bold", zorder=3, linespacing=1.3)
    ax.text(x, y - h * 0.20, "{:,}".format(val), ha="center", va="center",
            fontsize=val_size, color=fg, fontweight="bold", zorder=3)
    if sub:
        ax.text(x, y - h * 0.42, sub, ha="center", va="center", fontsize=7,
                color=MUTED, zorder=3)


def elbow(ax, x0, y0, x1, y1, color=LINE):
    mid = (y0 + y1) / 2
    ax.plot([x0, x0], [y0, mid], color=color, lw=1.1, zorder=1)
    ax.plot([x0, x1], [mid, mid], color=color, lw=1.1, zorder=1)
    ax.plot([x1, x1], [mid, y1], color=color, lw=1.1, zorder=1)


def draw(T, stamp, path):
    leaf_tot = [sum(T[(g, s, o[0])] for o in OUTS) for (g, _, _), s in zip(LEAVES, SHEET)]
    n = len(LEAVES)
    # right-hand groups carry wide parent boxes, so stop well short of x=100
    xs = [4 + i * (84.0 / (n - 1)) for i in range(n)]

    kids = collections.defaultdict(list)
    for i, (g, lab, par) in enumerate(LEAVES):
        kids[par].append(i)
    inter = {"RS_C": ("Raised self", "PR_C"), "CFN_C": ("Carry fee\nnot applying", "NR_C"),
             "CFN_N": ("Carry fee\nnot applying", "NR_N")}
    lvl2 = {"PR_C": ("Pickup request raised", "Consented"),
            "NR_C": ("Pickup not raised", "Consented"),
            "PR_N": ("Pickup request raised", "NotConsented"),
            "NR_N": ("Pickup not raised", "NotConsented"),
            "PR_O": ("Pickup request raised", "Orphaned8")}
    grp = {"Consented": ("Consented", CON_BG, CON_FG),
           "NotConsented": ("Not consented", NON_BG, NON_FG),
           "Orphaned8": ("8 orphaned CSPs", ORP_BG, ORP_FG)}

    val, X = {}, {}
    for k, ch in kids.items():
        val[k] = sum(leaf_tot[i] for i in ch)
        X[k] = sum(xs[i] for i in ch) / len(ch)
    for k, (_, par) in inter.items():
        val.setdefault(par, 0)
    for k in ("NR_C", "NR_N"):
        pass
    # level-2 values roll up their own children (intermediate or leaf)
    def rollup(key):
        tot, xsum, cnt = 0, 0.0, 0
        for ik, (_, par) in inter.items():
            if par == key:
                tot += val[ik]; xsum += X[ik]; cnt += 1
        for i, (g, lab, par) in enumerate(LEAVES):
            if par == key:
                tot += leaf_tot[i]; xsum += xs[i]; cnt += 1
        return tot, xsum / cnt
    for k in lvl2:
        val[k], X[k] = rollup(k)
    gval, gx = {}, {}
    for gkey in grp:
        tot, xsum, cnt = 0, 0.0, 0
        for k, (_, gk) in lvl2.items():
            if gk == gkey:
                tot += val[k]; xsum += X[k]; cnt += 1
        gval[gkey], gx[gkey] = tot, xsum / cnt
    total = sum(gval.values())

    fig = plt.figure(figsize=(26, 12), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(2, 100); ax.axis("off")

    ax.text(50, 96.5, "DELHI DEVICES — working list, and where they are now",
            ha="center", fontsize=19, color=PINK, fontweight="bold")
    ax.text(50, 93.4, "%s rows on the sheet (%s unique devices)  ·  buckets as on 22-Sep-2026  "
                      "·  outcome as of %s" % ("{:,}".format(total), "{:,}".format(total - 367), stamp),
            ha="center", fontsize=10, color=MUTED)

    box(ax, 50, 87, 22, 6.4, "Delhi devices", total, fill=NEUT_BG,
        title_size=10.5, val_size=15)

    for gkey, (lab, bg, fg) in grp.items():
        box(ax, gx[gkey], 76, 16, 6.2, lab, gval[gkey],
            sub="%.0f%% of total" % (100.0 * gval[gkey] / total), fill=bg, edge=fg,
            fg=fg, title_size=10, val_size=14, lw=1.6)
        elbow(ax, 50, 83.8, gx[gkey], 79.1)

    for k, (lab, gkey) in lvl2.items():
        box(ax, X[k], 64, 14, 6.0, lab, val[k], fill=NEUT_BG, title_size=8.5, val_size=12)
        elbow(ax, gx[gkey], 72.9, X[k], 67)

    for k, (lab, par) in inter.items():
        box(ax, X[k], 52, 13, 6.0, lab, val[k], fill=NEUT_BG, title_size=8.5, val_size=12)
        elbow(ax, X[k if False else k], 0, 0, 0) if False else None
        elbow(ax, X[par], 61, X[k], 55)

    for i, (g, lab, par) in enumerate(LEAVES):
        bg, fg = (CON_BG, CON_FG) if g == "Consented" else \
                 (NON_BG, NON_FG) if g == "NotConsented" else (ORP_BG, ORP_FG)
        box(ax, xs[i], 40, 7.0, 6.4, lab, leaf_tot[i], fill=bg, edge=fg, fg=fg,
            title_size=7.8, val_size=11)
        py = 49 if par in inter else 61
        elbow(ax, X[par], py, xs[i], 43.2)

        # ---- the layer their tree stops short of ----
        ax.plot([xs[i], xs[i]], [36.8, 34.4], color=LINE, lw=1.1, zorder=1)
        y = 32.6
        for okey, olab, ocol in OUTS:
            v = T[(g, SHEET[i], okey)]
            if not v:
                continue
            ax.text(xs[i] - 3.2, y, "{:,}".format(v), ha="left", va="center",
                    fontsize=8.6, color=ocol, fontweight="bold")
            ax.text(xs[i] + 3.3, y, olab, ha="right", va="center",
                    fontsize=6.4, color=MUTED)
            y -= 2.3

    tot = collections.Counter()
    for (g, s, o), v in T.items():
        tot[o] += v
    ax.add_patch(FancyBboxPatch((5, 6.5), 90, 9, boxstyle="round,pad=0.4,rounding_size=1.2",
                                linewidth=1.8, edgecolor=PINK, facecolor="white", zorder=2))
    ax.text(50, 13.2, "WHERE ALL %s ARE NOW" % "{:,}".format(total), ha="center",
            fontsize=10.5, color=PINK, fontweight="bold", zorder=3)
    span = [(k, l, c) for k, l, c in OUTS]
    for j, (okey, olab, ocol) in enumerate(span):
        x = 13 + j * 18.5
        ax.text(x, 10.2, "{:,}".format(tot[okey]), ha="center", va="center",
                fontsize=15, color=ocol, fontweight="bold", zorder=3)
        ax.text(x, 7.9, "%s  (%.0f%%)" % (olab, 100.0 * tot[okey] / total), ha="center",
                va="center", fontsize=7.8, color=MUTED, zorder=3)

    ax.text(50, 3.4, "Source: 'Device working list' sheet (buckets, 22-Sep) + NETBOX_CUSTODY live status.  "
                     "The tree's 'pickup request raised' is the sheet's own flag, not the NETBOX "
                     "RETRIEVAL_PENDING status.  367 devices appear twice (once as Not consented, once as orphaned).",
            ha="center", fontsize=7.6, color=MUTED)

    fig.savefig(path, dpi=120, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


if __name__ == "__main__":
    stamp = datetime.datetime.now(IST).strftime("%d %b %Y")
    raw = json.load(open("outcome_tree.json"))
    T = collections.Counter()
    for k, v in raw.items():
        g, leaf, o = k.split("|")
        T[(g, leaf, o)] = v
    p = draw(T, stamp, "delhi_bucket_tree.png")
    print("wrote", p, flush=True)
    if CHANNEL_ID:
        H = {"Authorization": "Bearer %s" % TOKEN}
        j = requests.post("https://slack.com/api/files.getUploadURLExternal", headers=H,
                          data={"filename": p, "length": os.path.getsize(p)}).json()
        with open(p, "rb") as fh:
            requests.post(j["upload_url"], data=fh.read())
        r = requests.post("https://slack.com/api/files.completeUploadExternal", headers=H,
                          data={"files": json.dumps([{"id": j["file_id"], "title": "Delhi bucket tree"}]),
                                "channel_id": CHANNEL_ID,
                                "initial_comment": "*Delhi working list — bucket tree + where they are now (%s)*" % stamp}).json()
        print("posted ok=%s %s" % (r.get("ok"), r.get("error") or ""), flush=True)
