# -*- coding: utf-8 -*-
"""The Delhi working-list funnel shape, rebuilt from the warehouse.

Same tree as the 'delhi_initiator_correction' HTML, but every node is a NETBOX
field rather than a sheet column, so it reconciles to my 22-Sep cohort:

  Delhi devices      = at a CSP office on 22-Sep (IDLE / CUSTODIED /
                       RETRIEVAL_PENDING / PENDING_CSP_RECEIPT at the slice)
  Consented          = CSP on the 28-Sep consent list with the form signed
  Retrieval pending  = NETBOX STATUS on 22-Sep (not a separate pickup flag)
  Raised self/system = RETRIEVAL_INITIATOR ('CSP' vs 'SYSTEM'/none)
  Carry fee applying = CARRY_FEE_ACTIVE on 22-Sep
  Custodied / Idle   = the 22-Sep status; PENDING_CSP_RECEIPT counts as Custodied

Reads funnel22.json from the companion query.
"""
import os, json, datetime, collections

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

PINK, INK, MUTED = "#d9008d", "#2b2b2b", "#7a7a7a"
NEUT_BG, NEUT_ED = "#efece8", "#b9b2aa"
CON_BG, CON_ED = "#e8f3ec", "#4a8f6b"
NON_BG, NON_ED = "#fbeadd", "#c07a45"
UNK_BG, UNK_ED = "#eceaf5", "#6f63a8"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")


def box(ax, x, y, w, h, title, val, sub=None, fill=NEUT_BG, edge=NEUT_ED, fg=INK,
        title_size=8.2, val_size=11, lw=1.1):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle="round,pad=0.3,rounding_size=0.9",
                                linewidth=lw, edgecolor=edge, facecolor=fill, zorder=2))
    ax.text(x, y + h * 0.20, title, ha="center", va="center", fontsize=title_size,
            color=fg, fontweight="bold", zorder=3, linespacing=1.3)
    ax.text(x, y - h * 0.22, "{:,}".format(val), ha="center", va="center",
            fontsize=val_size, color=fg, zorder=3)
    if sub:
        ax.text(x, y - h * 0.46, sub, ha="center", va="center", fontsize=6.4,
                color=MUTED, zorder=3)


def elbow(ax, x0, y0, x1, y1, color=NEUT_ED):
    mid = (y0 + y1) / 2
    ax.plot([x0, x0], [y0, mid], color=color, lw=1.0, zorder=1)
    ax.plot([x0, x1], [mid, mid], color=color, lw=1.0, zorder=1)
    ax.plot([x1, x1], [mid, y1], color=color, lw=1.0, zorder=1)


def draw(T, stamp, path):
    def q(g, kind, key=None):
        return sum(v for k, v in T.items()
                   if k[0] == g and k[1] == kind and (key is None or k[2] == key))

    G = (("Consented", CON_BG, CON_ED), ("Not consented", NON_BG, NON_ED),
         ("Consent unknown", UNK_BG, UNK_ED))
    # level-3 slots: 4 for each of the two main groups, 2 for the unknown group
    XS = [3 + i * (89.0 / 9) for i in range(10)]
    L3 = {"Consented": XS[0:4], "Not consented": XS[4:8], "Consent unknown": XS[8:10]}

    fig = plt.figure(figsize=(24, 11), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(8, 100); ax.axis("off")

    grand = sum(T.values())
    ax.text(50, 96.5, "DELHI DEVICES AT CSP OFFICE — 22-Sep-2026", ha="center",
            fontsize=18, color=PINK, fontweight="bold")
    ax.text(50, 93.6, "rebuilt from NETBOX_CUSTODY · 767 active Delhi CSPs · "
                      "consent per the list supplied 28-Sep · built %s" % stamp,
            ha="center", fontsize=9.5, color=MUTED)
    box(ax, 50, 87, 22, 6.0, "Delhi devices", grand, title_size=10, val_size=14)

    for gname, bg, ed in G:
        tot = sum(v for k, v in T.items() if k[0] == gname)
        if not tot:
            continue
        rp, nr = q(gname, "rp"), q(gname, "not")
        cf = q(gname, "not", "cf")
        cust = q(gname, "not", "CUSTODIED") + q(gname, "not", "PENDING_CSP_RECEIPT")
        idle = q(gname, "not", "IDLE")
        nocf = cust + idle
        slots = L3[gname]

        if gname == "Consent unknown":
            gx = sum(slots) / 2.0
            nrx = gx
        else:
            rpx = (slots[0] + slots[1]) / 2.0
            nrx = (slots[2] + slots[3]) / 2.0
            gx = (rpx + nrx) / 2.0

        box(ax, gx, 76, 22 if gname != "Consent unknown" else 16, 6.0, gname, tot,
            sub="%.0f%% of Delhi" % (100.0 * tot / grand),
            fill=bg, edge=ed, fg=ed, title_size=9.5, val_size=13, lw=1.5)
        elbow(ax, 50, 84, gx, 79)

        if gname != "Consent unknown":
            box(ax, rpx, 63, 17, 5.8, "Retrieval pending", rp, title_size=8.5)
            elbow(ax, gx, 73, rpx, 65.9)
            for x, lab, v in ((slots[0], "Raised self\n(by the CSP)", q(gname, "rp", "CSP")),
                              (slots[1], "Raised by system", q(gname, "rp", "System"))):
                box(ax, x, 49, 9.0, 6.0, lab, v, fill=bg, edge=ed, fg=ed)
                elbow(ax, rpx, 60.1, x, 52)
        box(ax, nrx, 63, 17 if gname != "Consent unknown" else 16, 5.8,
            "No retrieval raised", nr, title_size=8.5)
        elbow(ax, gx, 73, nrx, 65.9)

        a, b = (slots[2], slots[3]) if gname != "Consent unknown" else (slots[0], slots[1])
        box(ax, a, 49, 9.0, 6.0, "Carry fee\napplying", cf, fill=bg, edge=ed, fg=ed)
        elbow(ax, nrx, 60.1, a, 52)
        box(ax, b, 49, 9.0, 6.0, "Carry fee\nNOT applying", nocf)
        elbow(ax, nrx, 60.1, b, 52)
        for dx, lab, v in ((-3.0, "Custodied", cust), (3.0, "Idle", idle)):
            box(ax, b + dx, 33, 5.4, 5.6, lab, v, fill=bg, edge=ed, fg=ed,
                title_size=7.2, val_size=9.5)
            elbow(ax, b, 46, b + dx, 35.8)

    tot_cf = sum(q(g[0], "not", "cf") for g in G)
    tot_rp = sum(q(g[0], "rp") for g in G)
    tot_cust = sum(q(g[0], "not", "CUSTODIED") + q(g[0], "not", "PENDING_CSP_RECEIPT") for g in G)
    tot_idle = sum(q(g[0], "not", "IDLE") for g in G)
    ax.add_patch(FancyBboxPatch((8, 11), 84, 8, boxstyle="round,pad=0.4,rounding_size=1.2",
                                linewidth=1.6, edgecolor=PINK, facecolor="white", zorder=2))
    ax.text(50, 17.2, "ALL %s, BY STATE ON 22 SEP" % "{:,}".format(grand), ha="center",
            fontsize=9.5, color=PINK, fontweight="bold", zorder=3)
    for j, (v, lab) in enumerate(((tot_cf, "carry fee applying"), (tot_rp, "retrieval pending"),
                                  (tot_cust, "custodied"), (tot_idle, "idle, no fee"))):
        x = 18 + j * 21.5
        ax.text(x, 14.4, "{:,}".format(v), ha="center", va="center", fontsize=13,
                color=INK, fontweight="bold", zorder=3)
        ax.text(x, 12.3, "%s  (%.0f%%)" % (lab, 100.0 * v / grand), ha="center",
                va="center", fontsize=7.4, color=MUTED, zorder=3)

    ax.text(50, 9.2, "Node definitions: 'Retrieval pending' = NETBOX STATUS on 22-Sep (not a pickup flag)  ·  "
                     "raised self/system = RETRIEVAL_INITIATOR  ·  carry fee = CARRY_FEE_ACTIVE on 22-Sep  ·  "
                     "PENDING_CSP_RECEIPT counted as Custodied.  No retrieval-pending device carried a carry fee, "
                     "so that branch is not split further.",
            ha="center", fontsize=7.2, color=MUTED)
    fig.savefig(path, dpi=125, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


if __name__ == "__main__":
    stamp = datetime.datetime.now(IST).strftime("%d %b %Y")
    T = collections.Counter()
    for k, v in json.load(open("funnel22.json")).items():
        p = k.split("|")
        T[(p[0], p[1], p[2], p[3] if len(p) > 3 else "")] = v
    T2 = collections.Counter()
    for (g, kind, key, cf), v in T.items():
        T2[(g, kind, key)] += v
    p = draw(T2, stamp, "delhi_consent_funnel.png")
    print("wrote", p, flush=True)
    if CHANNEL_ID:
        H = {"Authorization": "Bearer %s" % TOKEN}
        j = requests.post("https://slack.com/api/files.getUploadURLExternal", headers=H,
                          data={"filename": p, "length": os.path.getsize(p)}).json()
        with open(p, "rb") as fh:
            requests.post(j["upload_url"], data=fh.read())
        r = requests.post("https://slack.com/api/files.completeUploadExternal", headers=H,
                          data={"files": json.dumps([{"id": j["file_id"],
                                                      "title": "Delhi consent funnel"}]),
                                "channel_id": CHANNEL_ID,
                                "initial_comment":
                                    "*Delhi devices at CSP office 22-Sep — same funnel, warehouse numbers (%s)*"
                                    % stamp}).json()
        print("posted ok=%s %s" % (r.get("ok"), r.get("error") or ""), flush=True)
