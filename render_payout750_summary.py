"""Render the Payout750 Summary (19 Aug) tab as TWO PNGs and post to Slack:
  1) New design (19-Aug new run)      — FUNNEL + DECISION
  2) Both campaigns total (18+19)     — FUNNEL + DECISION
Wiom-pink tables, native Slack file attachments.

Env: SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, GOOGLE_SA_JSON.
"""
import os, re, json, tempfile, datetime, requests, gspread
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams
from google.oauth2.service_account import Credentials

WIOM_PINK = "#d9008d"; LIGHT_PINK = "#fae8f0"
SHEET_ID = "1ap0K6GB6RijeLHRWPf9N84U0cl1XEJs67DSQBql7sGQ"
TAB = "Summary (19 Aug)"
POSTER = os.environ.get("P750_POSTER", "Palak")
rcParams["font.family"] = "DejaVu Sans"

SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]; CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]


def gc():
    sa = os.environ.get("GOOGLE_SA_JSON", "")
    if sa:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(sa); t.close(); path = t.name
    else:
        path = r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json"
    creds = Credentials.from_service_account_file(path, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds)


def clean(s):
    return (s or "").replace("✅", "").replace("⚡", "").strip()


def block_data(v, marker):
    """Extract (backend, funnel_rows[5-col], decision_rows[4-col]) for a block."""
    start = next(i for i, r in enumerate(v) if r and marker in (r[0] or ""))
    backend = ""
    if len(v[start]) > 1:
        m = re.search(r"=\s*(\d+)", v[start][1] or "")
        backend = m.group(1) if m else ""
    fi = next(i for i in range(start + 1, len(v)) if v[i] and (v[i][0] or "").startswith("FUNNEL"))
    funnel = [[clean(c) for c in (v[fi] + [""] * 5)[:5]]] + [[clean(c) for c in (v[fi + 1 + k] + [""] * 5)[:5]] for k in range(4)]
    di = next(i for i in range(fi + 1, len(v)) if v[i] and (v[i][0] or "").startswith("DECISION"))
    decision = [[clean(c) for c in (v[di] + [""] * 4)[:4]]] + [[clean(c) for c in (v[di + 1 + k] + [""] * 4)[:4]] for k in range(2)]
    return backend, funnel, decision


def style(tbl, rows, ncol):
    tbl.auto_set_font_size(False); tbl.set_fontsize(11); tbl.scale(1, 2.1)
    for j in range(ncol):                       # header row
        c = tbl[(0, j)]; c.set_facecolor(WIOM_PINK); c.set_text_props(color="white", weight="bold"); c.set_edgecolor("white")
    for i in range(1, len(rows)):
        c = tbl[(i, 0)]; c.set_facecolor(LIGHT_PINK); c.set_text_props(weight="bold", ha="left")
        for j in range(1, ncol):
            tbl[(i, j)].set_edgecolor("#dddddd")


def render(title, backend, funnel, decision, out):
    fig = plt.figure(figsize=(9.2, 5.4)); fig.patch.set_facecolor("white")
    fig.suptitle(title, color=WIOM_PINK, fontsize=16, fontweight="bold", y=0.985)
    sub = f"Opted in (real-time) = {backend}" if backend else ""
    fig.text(0.5, 0.9, sub, ha="center", color="#443152", fontsize=11, fontweight="bold")
    ax1 = fig.add_axes([0.04, 0.42, 0.92, 0.42]); ax1.axis("off")
    t1 = ax1.table(cellText=funnel, loc="center", cellLoc="center", colWidths=[0.40, 0.15, 0.15, 0.15, 0.15]); style(t1, funnel, 5)
    ax2 = fig.add_axes([0.04, 0.06, 0.72, 0.24]); ax2.axis("off")
    t2 = ax2.table(cellText=decision, loc="center", cellLoc="center", colWidths=[0.42, 0.19, 0.20, 0.19]); style(t2, decision, 4)
    fig.text(0.02, 0.005, datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("Updated %d-%b %H:%M IST · Payout750 CSP opt-in tracker"), fontsize=8, color="#999")
    plt.savefig(out, dpi=180, bbox_inches="tight", facecolor="white", pad_inches=0.25); plt.close()


def post(files, comment):
    H = {"Authorization": f"Bearer {SLACK_TOKEN}"}
    for idx, (fname, title) in enumerate(files):
        size = os.path.getsize(fname)
        j = requests.post("https://slack.com/api/files.getUploadURLExternal", headers=H, data={"filename": fname, "length": size}).json()
        if not j.get("ok"): raise SystemExit(f"getUploadURL failed: {j}")
        requests.post(j["upload_url"], data=open(fname, "rb").read())
        body = {"files": json.dumps([{"id": j["file_id"], "title": title}]), "channel_id": CHANNEL_ID}
        if idx == 0: body["initial_comment"] = comment
        r = requests.post("https://slack.com/api/files.completeUploadExternal", headers=H, data=body).json()
        if not r.get("ok"): raise SystemExit(f"complete upload failed for {fname}: {r}")
        print(f"  {fname}: ok")


if __name__ == "__main__":
    v = gc().open_by_key(SHEET_ID).worksheet(TAB).get_all_values()
    b19, f19, d19 = block_data(v, "19-AUG NEW RUN")
    bC, fC, dC = block_data(v, "COMBINED")
    render("Payout750 — New Design (19-Aug run)", b19, f19, d19, "p750_new.png")
    render("Payout750 — Both Campaigns Total (18+19)", bC, fC, dC, "p750_combined.png")
    ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    comment = f"*Payout750 — CSP opt-in tracker*  ·  {ist:%d-%b %H:%M IST}  ·  _posted by {POSTER}_\n① New design (19-Aug run)   ② Both campaigns total (18+19)"
    print("Posting to Slack...")
    post([("p750_new.png", "New design (19-Aug run)"), ("p750_combined.png", "Both campaigns total (18+19)")], comment)
    print("Done.")
