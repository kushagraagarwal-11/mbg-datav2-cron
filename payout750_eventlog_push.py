# -*- coding: utf-8 -*-
"""Payout750 FULL event tracker → Google Sheet "750 opt ins".

Pulls every Payout750 event (Viewed / Progress / Confirmed / Declined / Closed) from the
CleverTap Events Export and writes:
  - Event Log : one row per event (run-scoped), all properties + flow_assigned (Design cohort map).
  - Summary   : funnel / decision / avg-time / by-flow / percentiles, for the 19-Aug new run AND
                the 18+19-Aug combined total (same tables, stacked).

flow_assigned comes from the "Different flows for 750" Design tab (cspid -> Flow).

Env: CT_PASS, GOOGLE_SA_JSON (or local SA). Optional: P750_LOG_SHEET_ID, P750_DESIGN_SHEET_ID,
     P750_FROM, P750_RUN_FROM_TS, P750_COMBINED_FROM_TS, P750_EVENTLOG_TAB, P750_SUMMARY_TAB, P750_RUN_LABEL.
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
STAGES = ["viewed", "opened", "reached_choice", "confirmed", "declined", "closed"]

RUN_FROM_TS  = os.environ.get("P750_RUN_FROM_TS", "")
COMBINED_FROM_TS = os.environ.get("P750_COMBINED_FROM_TS", "20260818000000")  # 18+19 combined lower bound
EVENTLOG_TAB = os.environ.get("P750_EVENTLOG_TAB", "Event Log")
SUMMARY_TAB  = os.environ.get("P750_SUMMARY_TAB", "Summary")
RUN_LABEL    = os.environ.get("P750_RUN_LABEL", "")
MB_KEY = os.environ.get("METABASE_KEY", "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")


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
    try:
        vals = gc.open_by_key(DESIGN_SHEET_ID).worksheet("Design").get_all_values()
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


def mb_query(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://metabase.wiom.in/api/dataset", data=body,
            headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=120).read().decode())
        if isinstance(d, dict) and d.get("error"):
            print("  MB err", str(d["error"])[:120], flush=True); return []
        return d["data"]["rows"]
    except Exception as e:
        print("  MB query failed:", str(e)[:120], flush=True); return []


def backend_optins(from_ts):
    """OPTED_IN rows from DOMINANCE_CONSENT since from_ts (IST), deduped per CSP → [(cspid, ist_ts)]."""
    if not from_ts or len(from_ts) < 14: return []
    r = from_ts
    ist = f"{r[0:4]}-{r[4:6]}-{r[6:8]} {r[8:10]}:{r[10:12]}:{r[12:14]}"
    sql = ("SELECT CSP_ID, TO_CHAR(CONVERT_TIMEZONE('Asia/Kolkata', MAX(CONSENT_TIMESTAMP)), 'YYYY-MM-DD HH24:MI:SS') "
           "FROM PROD_DB.CSP_RV_SERVICE_CSP_RV_SERVICE.DOMINANCE_CONSENT "
           "WHERE CONSENT_CHOICE='OPTED_IN' AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE "
           f"AND CONSENT_TIMESTAMP >= '{ist} +05:30'::timestamp_tz "
           "AND CSP_ID NOT ILIKE 'TEST%' AND CSP_ID != 'a0a0b1' "   # drop test/junk consent rows
           "GROUP BY CSP_ID ORDER BY 2")
    return mb_query(sql)


def load_names(gc):
    try:
        vals = gc.open_by_key(DESIGN_SHEET_ID).worksheet("Design").get_all_values()
        h = vals[0]; ci = h.index("CSP ID"); ni = h.index("Partner Name")
    except Exception:
        return {}
    return {r[ci].strip(): r[ni].strip() for r in vals[1:] if len(r) > max(ci, ni) and r[ci].strip()}


def aggregate(records):
    """Dedupe records and build funnel/by-flow/dwell aggregates for one scope."""
    seen = set()
    fun = {k: set() for k in STAGES}; fl = {}
    dwell_by_csp = {}; declined_dwell = {}; opted_dwell = {}
    csps = set(); test_csps = set()
    def flset(flow): return fl.setdefault(flow or "TEST", {"viewed": set(), "confirmed": set(), "declined": set()})
    for rec in records:
        short = rec["short"]; c = rec["c"]; ts = rec["ts"]; mil = rec["mil"]; choice = rec["choice"]; ep = rec["ep"]; fa = rec["fa"]
        key = (short, c, ts, mil, choice)
        if key in seen: continue
        seen.add(key); csps.add(c)
        if fa == "TEST": test_csps.add(c)
        if short == "Viewed": fun["viewed"].add(c); flset(fa)["viewed"].add(c)
        elif short == "Progress" and mil == "content_opened": fun["opened"].add(c)
        elif short == "Progress" and mil == "reached_choice": fun["reached_choice"].add(c)
        elif short == "Confirmed":
            fun["confirmed"].add(c); flset(fa)["confirmed"].add(c)
            if fa != "TEST":
                prev = opted_dwell.get(c)
                if prev is None or ts > prev[0]: opted_dwell[c] = [ts] + [ep.get("sec_" + b) for b in BLOCKS] + [ep.get("seconds")]
        elif short == "Declined":
            fun["declined"].add(c); flset(fa)["declined"].add(c)
            if fa != "TEST":
                prev = declined_dwell.get(c)
                if prev is None or ts > prev[0]: declined_dwell[c] = [ts] + [ep.get("sec_" + b) for b in BLOCKS] + [ep.get("seconds")]
        elif short == "Closed":
            fun["closed"].add(c)
            if choice == "new": fun["confirmed"].add(c); flset(fa)["confirmed"].add(c)
            elif choice == "later": fun["declined"].add(c); flset(fa)["declined"].add(c)
            prev = dwell_by_csp.get(c)
            if prev is None or ts > prev[0]: dwell_by_csp[c] = [ts] + [ep.get("sec_" + b) for b in BLOCKS] + [ep.get("seconds")]
    return dict(fun=fun, fl=fl, dwell_by_csp=dwell_by_csp, declined_dwell=declined_dwell, opted_dwell=opted_dwell, csps=csps, test_csps=test_csps)


def main():
    gc = gspread.authorize(creds())
    ss = gc.open_by_key(SHEET_ID)
    design = load_design(gc)
    names = load_names(gc)
    print(f"design map: {len(design)} CSPs", flush=True)

    frm = int(os.environ.get("P750_FROM", "20260818"))   # pull both days so the combined block has 18-Aug
    to = int((datetime.datetime.now(IST) + datetime.timedelta(days=1)).strftime("%Y%m%d"))

    # Parse ALL exported events once into records; aggregate twice with different lower bounds.
    allrec = []
    for ev in EVENTS:
        short = ev.replace("Payout750_", "")
        for r in export(ev, frm, to):
            ep = r.get("event_props", {}) or {}
            c = cid_of(r); ts = str(r.get("ts", "")); flow = norm_flow(str(ep.get("flow", "")))
            mil = ep.get("milestone", ""); choice = ep.get("choice", "")
            fa = design.get(c, "") or flow or ("TEST" if design else "")
            allrec.append({"short": short, "c": c, "ts": ts, "flow": flow, "mil": mil, "choice": choice, "ep": ep, "fa": fa})

    rec19 = [r for r in allrec if not (RUN_FROM_TS and r["ts"] and r["ts"] < RUN_FROM_TS)]
    recAll = [r for r in allrec if r["ts"] >= COMBINED_FROM_TS]
    A19 = aggregate(rec19)
    Aall = aggregate(recAll)

    # ---- Event Log (19-run only) ----
    hdr = (["timestamp (IST)", "cspid", "event", "flow_assigned", "flow_tag", "milestone", "choice"] +
           ["sec_" + b for b in BLOCKS] +
           ["max_block", "seconds", "lang", "lang_toggles", "selection_changes", "last_selected", "exit", "api_status", "api_error"])
    seen_rows = set(); rows = []
    for r in rec19:
        ep = r["ep"]; key = (r["short"], r["c"], r["ts"], r["mil"], r["choice"])
        if key in seen_rows: continue
        seen_rows.add(key)
        rows.append([fmt_ts(r["ts"]), r["c"], r["short"], r["fa"], r["flow"], r["mil"], r["choice"]] +
                    [ep.get("sec_" + b, "") for b in BLOCKS] +
                    [ep.get("max_block", ""), ep.get("seconds", ""), ep.get("lang", ""), ep.get("lang_toggles", ""),
                     ep.get("selection_changes", ""), ep.get("last_selected", ""), ep.get("exit", ""),
                     ep.get("api_status", ""), ep.get("api_error", "")])
    rows.sort(key=lambda x: x[0])

    f19 = {k: len(v - A19["test_csps"]) for k, v in A19["fun"].items()}
    deciders_all = (A19["fun"]["confirmed"] | A19["fun"]["declined"]) - A19["test_csps"]
    optrate = round(100 * len(A19["fun"]["confirmed"] - A19["test_csps"]) / len(deciders_all)) if deciders_all else 0
    n_csps19 = len(A19["csps"] - A19["test_csps"])
    now = datetime.datetime.now(IST)
    print(f"events(19-run): {len(rows)}  real CSPs: {n_csps19}  funnel: {f19}", flush=True)

    banner = [f"PAYOUT750 EVENT LOG{(' — ' + RUN_LABEL) if RUN_LABEL else ''} — every tracked event from CleverTap. {len(rows)} events · {n_csps19} real CSPs. "
              f"Funnel (UNIQUE CSPs): {f19['viewed']} viewed → {f19['opened']} opened → {f19['reached_choice']} reached choice → "
              f"{f19['confirmed']} opted-in / {f19['declined']} declined. flow_assigned = from Design cohort map (cspid→Flow). "
              f"{('Scope: events from ' + RUN_FROM_TS + ' onward. ') if RUN_FROM_TS else ''}"
              f"Last refresh {now:%Y-%m-%d %H:%M IST} (CleverTap export lags ~1 hr)."]
    try:
        ws = ss.worksheet(EVENTLOG_TAB)
    except gspread.WorksheetNotFound:
        if EVENTLOG_TAB == "Event Log":
            try: ws = ss.sheet1; ws.update_title("Event Log")
            except Exception: ws = ss.add_worksheet(EVENTLOG_TAB, rows=max(200, len(rows) + 10), cols=len(hdr))
        else:
            ws = ss.add_worksheet(EVENTLOG_TAB, rows=max(200, len(rows) + 10), cols=len(hdr))
    ws.clear()
    ws.update(values=[banner] + [[""] * len(hdr)] + [hdr] + rows, range_name="A1", value_input_option="RAW")
    ws.format(f"A3:{chr(64+len(hdr))}3", {"textFormat": {"bold": True}})

    # ---- Summary: two stacked blocks (19-run, then 18+19 combined) ----
    summ = []; hdr_rows = []
    def add(*row): summ.append(list(row))

    def emit_block(A, be, label):
        fun = A["fun"]; fl = A["fl"]; test = A["test_csps"]
        dwell_by_csp = A["dwell_by_csp"]; declined_dwell = A["declined_dwell"]; opted_dwell = A["opted_dwell"]
        funnel = {k: len(v - test) for k, v in fun.items()}
        viewed = funnel["viewed"]; opened = funnel["opened"]; reached = funnel["reached_choice"]
        n_opted = funnel["confirmed"]; closed = funnel["closed"]
        decided = len((fun["confirmed"] | fun["declined"]) - test)
        n_csps = len(A["csps"] - test)
        def pv(x): return f"{round(100*x/viewed)}%" if viewed else "-"
        def step(x, prev): return f"{round(100*x/prev)}%" if prev else "-"
        def drop(prev, x): return f"-{prev-x}" if prev >= x else "+" + str(x-prev)
        opened_csps = fun["opened"] - test
        def avgsec(idx):
            vals = []
            for c, v in dwell_by_csp.items():
                if c not in opened_csps: continue
                x = v[1 + idx]
                if x in (None, ""): continue
                try: vals.append(float(x))
                except Exception: pass
            return round(sum(vals) / len(vals), 1) if vals else 0
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
            rr = []
            for i, lab in enumerate(["Hero (₹750 intro)", "Timeline (journey)", "Scenarios (stops early)", "Key points", "Choice (decision)"]):
                rr.append([lab] + [pctile(col(i), p) for p in ps])
            rr.append(["Page 2 total (active)"] + [pctile(total(), p) for p in ps])
            rr.append(["Whole session (wall-clock)"] + [pctile(col(5), p) for p in ps])
            return rr

        add("")
        hdr_rows.append(len(summ)); add(f"━━━ {label} ━━━", (f"⚡ backend opt-ins (real-time) = {len(be)}" if be is not None else ""))
        hdr_rows.append(len(summ)); add("FUNNEL (unique CSPs)", "CSPs", "% of viewed", "step conv.", "drop-off")
        add("1 · Viewed  (page 1 shown)",          viewed,  "100%",      "—",                   "—")
        add("2 · Opened content  (→ page 2)",       opened,  pv(opened),  step(opened, viewed),  drop(viewed, opened))
        add("3 · Reached choice  (scrolled down)",  reached, pv(reached), step(reached, opened), drop(opened, reached))
        add("4 · Made a decision",                  decided, pv(decided), step(decided, reached),drop(reached, decided))
        add("")
        # Opted-in count = real-time BACKEND (all channels incl. push, test-excluded), not the
        # lagged banner export. Declines stay export-based (no backend feed). Deciders recomputed.
        opted_disp = len(be) if be is not None else n_opted
        declined_only = decided - n_opted
        deciders_disp = opted_disp + declined_only
        hdr_rows.append(len(summ)); add("DECISION", "CSPs", "% of deciders", "% of viewed")
        add("Opted in  ✅", opted_disp,    f"{round(100*opted_disp/deciders_disp)}%" if deciders_disp else "-", pv(opted_disp))
        add("Declined",     declined_only, f"{round(100*declined_only/deciders_disp)}%" if deciders_disp else "-", pv(declined_only))
        add("")
        add("Closed  (any exit, incl. abandon)", closed, pv(closed))
        add("Unique CSPs (real cohort)", n_csps)
        add("")
        hdr_rows.append(len(summ)); add("AVG TIME on page 2  (sec per section · CSPs who opened)", "avg sec")
        add("Hero — ₹750 intro", avgsec(0)); add("Timeline — the journey", avgsec(1)); add("Scenarios — stops early", avgsec(2))
        add("Key points", avgsec(3)); add("Choice — the decision", avgsec(4)); add("Whole session (incl. cover + confirm screen)", avgsec(5))
        add("")
        hdr_rows.append(len(summ)); add("BY FLOW (assigned) — unique CSPs", "viewed", "opted-in", "declined", "opt-in %")
        for f in ["1", "2", "3"] + [x for x in sorted(fl) if x not in ("1", "2", "3", "TEST")]:
            if f not in fl: continue
            d = fl[f]; opted = d["confirmed"]; dec_only = d["declined"] - opted
            deciders = len(opted) + len(dec_only)
            add(("Flow " + f) if f in ("1", "2", "3") else f, len(d["viewed"]), len(opted), len(dec_only),
                f"{round(100*len(opted)/deciders)}%" if deciders else "-")
        add("")
        hdr_rows.append(len(summ)); add(f"DECLINED CSPs — sec on each page-2 content block  (n={len(declined_dwell)})", "p25", "p50", "p95")
        for r in pct_rows(declined_dwell, [25, 50, 95]): add(*r)
        add("")
        hdr_rows.append(len(summ)); add(f"OPTED-IN CSPs — sec on each page-2 content block  (n={len(opted_dwell)})", "p25", "p50", "p90")
        for r in pct_rows(opted_dwell, [25, 50, 90]): add(*r)

    be19 = backend_optins(RUN_FROM_TS) if RUN_FROM_TS else None
    beAll = backend_optins(COMBINED_FROM_TS)
    add(f"PAYOUT750 SUMMARY{(' — ' + RUN_LABEL) if RUN_LABEL else ''}", f"updated {now:%Y-%m-%d %H:%M IST}")
    if be19 is not None:
        add(f"⚡ LIVE opt-ins (backend, real-time) = {len(be19)}", "← authoritative for the 19-Aug run. The export-based funnels below lag ~1 hr.")
    emit_block(A19, be19, "19-AUG NEW RUN")
    add("")
    emit_block(Aall, beAll, "18 + 19 AUG — COMBINED (all campaigns)")

    if be19 is not None:
        add("")
        hdr_rows.append(len(summ)); add(f"BACKEND OPT-INS (19-Aug) — real-time from DOMINANCE_CONSENT  (n={len(be19)})", "opted at (IST)")
        for cid, t in be19:
            nm = names.get(cid, "")
            add((nm + "  (" + cid + ")") if nm else cid, t)

    try: ws2 = ss.worksheet(SUMMARY_TAB)
    except gspread.WorksheetNotFound: ws2 = ss.add_worksheet(SUMMARY_TAB, rows=90, cols=6)
    ws2.clear()
    ws2.update(values=summ, range_name="A1", value_input_option="RAW")
    ws2.format("A1:B1", {"textFormat": {"bold": True, "fontSize": 13}})
    for hr in hdr_rows:
        ws2.format(f"A{hr+1}:E{hr+1}", {"textFormat": {"bold": True}, "backgroundColor": {"red": .93, "green": .90, "blue": .97}})
    print(f"wrote Event Log ({len(rows)}) + Summary [19-run + combined]. 19-run opt-in {f19['confirmed']}/{len(deciders_all)} = {optrate}%. OK {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
