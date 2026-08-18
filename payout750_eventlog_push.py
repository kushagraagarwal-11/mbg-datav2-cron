# -*- coding: utf-8 -*-
"""Payout750 FULL event tracker → Google Sheet "750 opt ins".

Pulls every Payout750 event (Viewed / Progress / Confirmed / Declined / Closed) from the
CleverTap Events Export and writes:
  - "Event Log"  : one row per event, all properties (funnel + per-block dwell + choice + consent).
  - "Summary"    : live funnel counts + opt-in rate, overall and per flow.

Env: CT_PASS, GOOGLE_SA_JSON (or local SA file). Optional: P750_LOG_SHEET_ID, P750_FROM.
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


def cid_of(r):
    pd = r.get("profile", {}).get("profileData", {}) or {}
    return (pd.get("cspid") or "").strip() or ("id:" + str(r.get("profile", {}).get("identity", "")))


def fmt_ts(ts):
    ts = str(ts)
    try: return datetime.datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except: return ts


def main():
    frm = int(os.environ.get("P750_FROM", "20260817"))
    to = int((datetime.datetime.now(IST) + datetime.timedelta(days=1)).strftime("%Y%m%d"))
    rows = []          # event-log rows
    seen = set()       # dedupe (event, cspid, ts, choice/milestone)
    funnel = Counter(); by_flow = {}; csps = set()
    for ev in EVENTS:
        short = ev.replace("Payout750_", "")
        for r in export(ev, frm, to):
            ep = r.get("event_props", {}) or {}
            c = cid_of(r); ts = str(r.get("ts", "")); flow = str(ep.get("flow", ""))
            mil = ep.get("milestone", ""); choice = ep.get("choice", "")
            key = (short, c, ts, mil, choice)
            if key in seen: continue
            seen.add(key)
            csps.add(c)
            # funnel accounting
            if short == "Viewed": funnel["viewed"] += 1
            elif short == "Progress" and mil == "content_opened": funnel["opened"] += 1
            elif short == "Progress" and mil == "reached_choice": funnel["reached_choice"] += 1
            elif short == "Confirmed": funnel["confirmed"] += 1
            elif short == "Declined": funnel["declined"] += 1
            elif short == "Closed": funnel["closed"] += 1
            bf = by_flow.setdefault(flow or "(none)", Counter())
            if short in ("Confirmed",): bf["confirmed"] += 1
            if short in ("Declined",): bf["declined"] += 1
            if short == "Viewed": bf["viewed"] += 1
            rows.append([fmt_ts(ts), c, short, flow, mil, choice] +
                        [ep.get("sec_" + b, "") for b in BLOCKS] +
                        [ep.get("max_block", ""), ep.get("seconds", ""), ep.get("lang", ""),
                         ep.get("lang_toggles", ""), ep.get("selection_changes", ""),
                         ep.get("last_selected", ""), ep.get("exit", ""),
                         ep.get("api_status", ""), ep.get("api_error", "")])
    rows.sort(key=lambda x: x[0])
    print(f"events: {len(rows)}  csps: {len([c for c in csps])}  funnel: {dict(funnel)}", flush=True)

    hdr = (["timestamp (IST)", "cspid", "event", "flow", "milestone", "choice"] +
           ["sec_" + b for b in BLOCKS] +
           ["max_block", "seconds", "lang", "lang_toggles", "selection_changes", "last_selected", "exit", "api_status", "api_error"])
    now = datetime.datetime.now(IST)
    banner = [f"PAYOUT750 EVENT LOG — every tracked event from CleverTap. {len(rows)} events · {len(csps)} CSPs. "
              f"Funnel: {funnel['viewed']} viewed → {funnel['opened']} opened → {funnel['reached_choice']} reached choice → "
              f"{funnel['confirmed']} opted-in / {funnel['declined']} declined. Last refresh {now:%Y-%m-%d %H:%M IST} (CleverTap export lags a few hrs)."]

    gc = gspread.authorize(creds()); ss = gc.open_by_key(SHEET_ID)
    # Event Log = the first tab (gid 0), so the shared link opens straight to the data
    try:
        ws = ss.worksheet("Event Log")
    except gspread.WorksheetNotFound:
        ws = ss.sheet1
        try: ws.update_title("Event Log")
        except Exception: ws = ss.add_worksheet("Event Log", rows=max(200, len(rows) + 10), cols=len(hdr))
    ws.clear()
    ws.update(values=[banner] + [[""] * len(hdr)] + [hdr] + rows, range_name="A1", value_input_option="RAW")
    ws.format(f"A3:{chr(64+len(hdr))}3", {"textFormat": {"bold": True}})

    # Summary tab
    optrate = round(100 * funnel["confirmed"] / (funnel["confirmed"] + funnel["declined"])) if (funnel["confirmed"] + funnel["declined"]) else 0
    summ = [["PAYOUT750 SUMMARY", f"updated {now:%Y-%m-%d %H:%M IST}"], [""],
            ["Funnel", "count"],
            ["Banner viewed", funnel["viewed"]],
            ["Opened content", funnel["opened"]],
            ["Reached choice", funnel["reached_choice"]],
            ["Opted in (Confirmed)", funnel["confirmed"]],
            ["Declined", funnel["declined"]],
            ["Closed (terminal)", funnel["closed"]],
            ["Opt-in rate (of deciders)", f"{optrate}%"],
            ["Unique CSPs", len(csps)], [""],
            ["By flow", "viewed", "opted-in", "declined"]]
    for f in sorted(by_flow):
        b = by_flow[f]; summ.append([f, b.get("viewed", 0), b.get("confirmed", 0), b.get("declined", 0)])
    try: ws2 = ss.worksheet("Summary")
    except gspread.WorksheetNotFound: ws2 = ss.add_worksheet("Summary", rows=40, cols=6)
    ws2.clear()
    ws2.update(values=summ, range_name="A1", value_input_option="RAW")
    ws2.format("A1:B1", {"textFormat": {"bold": True, "fontSize": 12}})
    ws2.format("A3:D3", {"textFormat": {"bold": True}})
    ws2.format("A13:D13", {"textFormat": {"bold": True}})
    print(f"wrote Event Log ({len(rows)}) + Summary. opt-in {funnel['confirmed']}/{funnel['confirmed']+funnel['declined']} = {optrate}%. OK {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
