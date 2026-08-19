# -*- coding: utf-8 -*-
"""Payout750 FULL event tracker → Google Sheet "750 opt ins".

Pulls every Payout750 event (Viewed / Progress / Confirmed / Declined / Closed) from the
CleverTap Events Export and writes:
  - "Event Log"  : one row per event, all properties + flow_assigned (from the Design cohort map).
  - "Summary"    : live funnel counts + opt-in rate, overall and per assigned flow.

flow_assigned comes from the "Different flows for 750" Design tab (cspid -> Flow), so events are
attributable to Flow 1/2/3 even when the live HTML doesn't stamp the `flow` property.

Env: CT_PASS, GOOGLE_SA_JSON (or local SA file). Optional: P750_LOG_SHEET_ID, P750_DESIGN_SHEET_ID, P750_FROM.
"""
import os, sys, json, time, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account
from collections import Counter

CT_ACC  = os.environ.get("CLEVERTAP_ACCOUNT", "44Z-644-777Z")
CT_PASS = os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
REGION  = os.environ.get("CLEVERTAP_REGION", "eu1")
CT      = f"https://{REGION}.api.clevertap.com"
HP = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
HG = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS}
SHEET_ID = os.environ.get("P750_LOG_SHEET_ID", "1ap0K6GB6RijeLHRWPf9N84U0cl1XEJs67DSQBql7sGQ")
DESIGN_SHEET_ID = os.environ.get("P750_DESIGN_SHEET_ID", "1SfWil0SaN1lKPTqtTF86edoPk8lT1BxzNzB6vC0_4Yc")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
BLOCKS = ["hero", "timeline", "scenarios", "keypoints", "choice"]
EVENTS = ["Payout750_Viewed", "Payout750_Progress", "Payout750_Confirmed", "Payout750_Declined", "Payout750_Closed"]


def export(evname, frm, to):
    body = json.dumps({"event_name": evname, "from": frm, "to": to}).encode()
    for _ in range(3):
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?batch_size=1000", data=body, headers=HP), timeout=90).read().decode())
            cur = d.get("cursor"); recs = []; pages = 0
            while cur and pages < 300:
                dd = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?cursor=" + cur, headers=HG), timeout=90).read().decode())
                recs += dd.get("records", []); cur = dd.get("next_cursor"); pages += 1
                if not cur: break
            return recs
        except Exception as e:
            print("  retry", evname, str(e)[:70], flush=True); time.sleep(5)
    return []


def creds():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if raw:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name, scopes=scopes)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p):
            return service_account.Credentials.from_service_account_file(p, scopes=scopes)
    raise SystemExit("no SA creds")


def norm_flow(s):
    s = (s or "").strip().lower().replace("flow", "").strip()
    return s if s in ("1", "2", "3") else ""


def load_design(gc):
    """cspid -> assigned flow ('1'/'2'/'3') from the Design tab."""
    try:
        ws = gc.open_by_key(DESIGN_SHEET_ID).worksheet("Design")
        vals = ws.get_all_values()
    except Exception as e:
        print("  design load failed:", str(e)[:80], flush=True); return {}
    m = {}
    for r in vals[1:]:
        r = (r + [""] * 6)[:6]
        cid = r[0].strip()
        if cid: m[cid] = norm_flow(r[5])
    return m


def cid_of(r):
    pd = r.get("profile", {}).get("profileData", {}) or {}
    return (pd.get("cspid") or "").strip() or ("id:" + str(r.get("profile", {}).get("identity", "")))


def fmt_ts(ts):
    ts = str(ts)
    try: return datetime.datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except: return ts


def pctile(a, p):
    a = sorted(x for x in a if x is not None)
    if not a: return "-"
    k = (len(a) - 1) * p / 100.0; f = int(k); c = min(f + 1, len(a) - 1)
    return round(a[f] + (a[c] - a[f]) * (k - f), 1)


def main():
    gc = gspread.authorize(creds())
    ss = gc.open_by_key(SHEET_ID)
    design = load_design(gc)
    print(f"design map: {len(design)} CSPs", flush=True)

    frm = int(os.environ.get("P750_FROM", "20260817"))
    to = int((datetime.datetime.now(IST) + datetime.timedelta(days=1)).strftime("%Y%m%d"))
    rows = []; seen = set()
    STAGES = ["viewed", "opened", "reached_choice", "confirmed", "declined", "closed"]
    fun = {k: set() for k in STAGES}          # DISTINCT cspids per funnel stage
    fl = {}                                    # flow -> {viewed,confirmed,declined} cspid sets
    dwell_by_csp = {}                          # cspid -> [ts, sec_hero..sec_choice, seconds]  (latest Closed)
    declined_dwell = {}                        # cspid -> same, from latest Declined event (real cohort only)
    opted_dwell = {}                           # cspid -> same, from latest Confirmed event (real cohort only)
    csps = set()
    def flset(flow):
        return fl.setdefault(flow or "TEST", {"viewed": set(), "confirmed": set(), "declined": set()})
    for ev in EVENTS:
        short = ev.replace("Payout750_", "")
        for r in export(ev, frm, to):
            ep = r.get("event_props", {}) or {}
            c = cid_of(r); ts = str(r.get("ts", "")); flow = norm_flow(str(ep.get("flow", "")))
            mil = ep.get("milestone", ""); choice = ep.get("choice", "")
            key = (short, c, ts, mil, choice)
            if key in seen: continue
            seen.add(key); csps.add(c)
            fa = design.get(c, "") or flow or ("TEST" if design else "")   # Design flow; non-cohort/test → TEST
            if short == "Viewed": fun["viewed"].add(c); flset(fa)["viewed"].add(c)
            elif short == "Progress" and mil == "content_opened": fun["opened"].add(c)
            elif short == "Progress" and mil == "reached_choice": fun["reached_choice"].add(c)
            elif short == "Confirmed":
                fun["confirmed"].add(c); flset(fa)["confirmed"].add(c)
                if fa != "TEST":
                    prev = opted_dwell.get(c)
                    if prev is None or ts > prev[0]:
                        opted_dwell[c] = [ts] + [ep.get("sec_" + b) for b in BLOCKS] + [ep.get("seconds")]
            elif short == "Declined":
                fun["declined"].add(c); flset(fa)["declined"].add(c)
                if fa != "TEST":                      # real cohort only, for the percentile table
                    prev = declined_dwell.get(c)
                    if prev is None or ts > prev[0]:
                        declined_dwell[c] = [ts] + [ep.get("sec_" + b) for b in BLOCKS] + [ep.get("seconds")]
            elif short == "Closed":
                fun["closed"].add(c)
                prev = dwell_by_csp.get(c)
                if prev is None or ts > prev[0]:      # keep the latest Closed session per CSP
                    dwell_by_csp[c] = [ts] + [ep.get("sec_" + b) for b in BLOCKS] + [ep.get("seconds")]
            rows.append([fmt_ts(ts), c, short, fa, flow, mil, choice] +
                        [ep.get("sec_" + b, "") for b in BLOCKS] +
                        [ep.get("max_block", ""), ep.get("seconds", ""), ep.get("lang", ""),
                         ep.get("lang_toggles", ""), ep.get("selection_changes", ""),
                         ep.get("last_selected", ""), ep.get("exit", ""),
                         ep.get("api_status", ""), ep.get("api_error", "")])
    rows.sort(key=lambda x: x[0])
    funnel = {k: len(v) for k, v in fun.items()}     # unique-CSP counts per stage
    deciders_all = fun["confirmed"] | fun["declined"]
    optrate = round(100 * len(fun["confirmed"]) / len(deciders_all)) if deciders_all else 0
    print(f"events: {len(rows)}  unique CSPs: {len(csps)}  funnel(unique): {funnel}", flush=True)

    hdr = (["timestamp (IST)", "cspid", "event", "flow_assigned", "flow_tag", "milestone", "choice"] +
           ["sec_" + b for b in BLOCKS] +
           ["max_block", "seconds", "lang", "lang_toggles", "selection_changes", "last_selected", "exit", "api_status", "api_error"])
    now = datetime.datetime.now(IST)
    banner = [f"PAYOUT750 EVENT LOG — every tracked event from CleverTap. {len(rows)} events · {len(csps)} unique CSPs. "
              f"Funnel (UNIQUE CSPs): {funnel['viewed']} viewed → {funnel['opened']} opened → {funnel['reached_choice']} reached choice → "
              f"{funnel['confirmed']} opted-in / {funnel['declined']} declined. flow_assigned = from Design cohort map (cspid→Flow). "
              f"Last refresh {now:%Y-%m-%d %H:%M IST} (CleverTap export lags ~1 hr)."]

    # Event Log = first tab (gid 0) so the shared link opens straight to the data
    try:
        ws = ss.worksheet("Event Log")
    except gspread.WorksheetNotFound:
        try: ws = ss.sheet1; ws.update_title("Event Log")
        except Exception: ws = ss.add_worksheet("Event Log", rows=max(200, len(rows) + 10), cols=len(hdr))
    ws.clear()
    ws.update(values=[banner] + [[""] * len(hdr)] + [hdr] + rows, range_name="A1", value_input_option="RAW")
    ws.format(f"A3:{chr(64+len(hdr))}3", {"textFormat": {"bold": True}})

    # Summary — funnel drop-off with conversion %s (every count = UNIQUE CSPs)
    viewed = funnel["viewed"]; opened = funnel["opened"]; reached = funnel["reached_choice"]
    n_opted = funnel["confirmed"]; n_declined = funnel["declined"]; closed = funnel["closed"]
    decided = len(fun["confirmed"] | fun["declined"])
    def pv(x): return f"{round(100*x/viewed)}%" if viewed else "-"          # % of all who viewed
    def step(x, prev): return f"{round(100*x/prev)}%" if prev else "-"      # step-to-step conversion
    def drop(prev, x): return f"-{prev-x}" if prev >= x else "+" + str(x-prev)
    opened_csps = fun["opened"]
    def avgsec(idx):                             # avg seconds on a section, among CSPs who opened page 2
        vals = []
        for c, v in dwell_by_csp.items():
            if c not in opened_csps: continue
            x = v[1 + idx]
            if x in (None, ""): continue
            try: vals.append(float(x))
            except Exception: pass
        return round(sum(vals) / len(vals), 1) if vals else 0

    summ = []; hdr_rows = []
    def add(*row): summ.append(list(row))
    add("PAYOUT750 SUMMARY", f"updated {now:%Y-%m-%d %H:%M IST}"); add("")
    hdr_rows.append(len(summ)); add("FUNNEL (unique CSPs)", "CSPs", "% of viewed", "step conv.", "drop-off")
    add("1 · Viewed  (page 1 shown)",           viewed,  "100%",       "—",                    "—")
    add("2 · Opened content  (→ page 2)",  opened,  pv(opened),   step(opened, viewed),   drop(viewed, opened))
    add("3 · Reached choice  (scrolled down)",  reached, pv(reached),  step(reached, opened),  drop(opened, reached))
    add("4 · Made a decision",                  decided, pv(decided),  step(decided, reached), drop(reached, decided))
    add("")
    declined_only = decided - n_opted        # declined and did NOT end up opting in (so opted+declined = deciders)
    hdr_rows.append(len(summ)); add("DECISION", "CSPs", "% of deciders", "% of viewed")
    add("Opted in  ✅", n_opted,       f"{round(100*n_opted/decided)}%" if decided else "-",       pv(n_opted))
    add("Declined",     declined_only, f"{round(100*declined_only/decided)}%" if decided else "-", pv(declined_only))
    add("")
    add("Closed  (any exit, incl. abandon)", closed, pv(closed))
    add("Unique CSPs (any event)", len(csps))
    add("")
    hdr_rows.append(len(summ)); add("AVG TIME on page 2  (sec per section · CSPs who opened)", "avg sec")
    add("Hero — ₹750 intro",          avgsec(0))
    add("Timeline — the journey",     avgsec(1))
    add("Scenarios — stops early",    avgsec(2))
    add("Key points",                 avgsec(3))
    add("Choice — the decision",      avgsec(4))
    add("Whole session (incl. cover + confirm screen)", avgsec(5))
    add("")
    hdr_rows.append(len(summ)); add("BY FLOW (assigned) — unique CSPs", "viewed", "opted-in", "declined", "opt-in %")
    for f in ["1", "2", "3"] + [x for x in sorted(fl) if x not in ("1", "2", "3")]:
        if f not in fl: continue
        d = fl[f]; opted = d["confirmed"]; dec_only = d["declined"] - opted     # opting in isn't also 'declined'
        deciders = len(opted) + len(dec_only)
        add(("Flow " + f) if f in ("1", "2", "3") else f, len(d["viewed"]), len(opted), len(dec_only),
            f"{round(100*len(opted)/deciders)}%" if deciders else "-")

    # Time percentiles: seconds spent on each page-2 content block (active, no downtime)
    def pct_rows(dwell, ps):
        def col(idx):
            out = []
            for v in dwell.values():
                x = v[1 + idx]
                if x in (None, ""): continue
                try: out.append(float(x))
                except Exception: pass
            return out
        def total():
            out = []
            for v in dwell.values():
                s = 0.0; ok = False
                for i in range(5):
                    x = v[1 + i]
                    if x in (None, ""): continue
                    try: s += float(x); ok = True
                    except Exception: pass
                if ok: out.append(s)
            return out
        rows = []
        for i, lab in enumerate(["Hero (₹750 intro)", "Timeline (journey)", "Scenarios (stops early)", "Key points", "Choice (decision)"]):
            a = col(i); rows.append([lab] + [pctile(a, p) for p in ps])
        t = total(); rows.append(["Page 2 total (active)"] + [pctile(t, p) for p in ps])
        w = col(5); rows.append(["Whole session (wall-clock)"] + [pctile(w, p) for p in ps])
        return rows
    add("")
    hdr_rows.append(len(summ)); add(f"DECLINED CSPs — sec on each page-2 content block  (n={len(declined_dwell)})", "p25", "p50", "p95")
    for r in pct_rows(declined_dwell, [25, 50, 95]): add(*r)
    add("")
    hdr_rows.append(len(summ)); add(f"OPTED-IN CSPs — sec on each page-2 content block  (n={len(opted_dwell)})", "p25", "p50", "p90")
    for r in pct_rows(opted_dwell, [25, 50, 90]): add(*r)

    try: ws2 = ss.worksheet("Summary")
    except gspread.WorksheetNotFound: ws2 = ss.add_worksheet("Summary", rows=45, cols=6)
    ws2.clear()
    ws2.update(values=summ, range_name="A1", value_input_option="RAW")
    ws2.format("A1:B1", {"textFormat": {"bold": True, "fontSize": 13}})
    for hr in hdr_rows:
        ws2.format(f"A{hr+1}:E{hr+1}", {"textFormat": {"bold": True}, "backgroundColor": {"red": .93, "green": .90, "blue": .97}})
    print(f"wrote Event Log ({len(rows)}) + Summary. opt-in {len(fun['confirmed'])}/{len(deciders_all)} unique = {optrate}%. OK {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
