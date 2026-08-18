# -*- coding: utf-8 -*-
"""Payout750 opt-in / declined extractor — OUR owned record of who chose what.

Pulls Payout750_Confirmed / _Declined / _Closed from the CleverTap Events Export,
attributes each to a cspid, and writes a per-CSP log to a Google Sheet. This is the
analytics / reconciliation layer that does NOT depend on the CleverTap UI or the
dominance gateway: even if the backend consent POST fails, the opt-in is captured here
(and `backend_ok` flags whether the POST landed, from the Closed event's api_status).

Env: CT_PASS (CleverTap passcode), GOOGLE_SA_JSON (or local SA file). Optional:
  P750_SHEET_ID (default = MG pilot sheet), P750_FROM (YYYYMMDD, default 20260817).
"""
import os, sys, json, time, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

CT_ACC  = os.environ.get("CLEVERTAP_ACCOUNT", "44Z-644-777Z")
CT_PASS = os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
REGION  = os.environ.get("CLEVERTAP_REGION", "eu1")
CT      = f"https://{REGION}.api.clevertap.com"
HP = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
HG = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS}
SHEET_ID = os.environ.get("P750_SHEET_ID", "1XHqjybQYKyfCpgdraPiL32GBm2R2wf-CguxlHalmu8M")
TAB      = "Payout750 Opt-in Log"
IST      = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
BLOCKS   = ["hero", "timeline", "scenarios", "keypoints", "choice"]


def export(evname, frm, to):
    body = json.dumps({"event_name": evname, "from": frm, "to": to}).encode()
    for _ in range(3):
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?batch_size=1000", data=body, headers=HP), timeout=90).read().decode())
            cur = d.get("cursor"); recs = []; pages = 0
            while cur and pages < 200:
                dd = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?cursor=" + cur, headers=HG), timeout=90).read().decode())
                recs += dd.get("records", []); cur = dd.get("next_cursor"); pages += 1
                if not cur: break
            return recs
        except Exception as e:
            print("  export retry", evname, str(e)[:80], flush=True); time.sleep(5)
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


def main():
    frm = int(os.environ.get("P750_FROM", "20260817"))
    to = int(datetime.datetime.now(IST).strftime("%Y%m%d"))
    conf = export("Payout750_Confirmed", frm, to)
    decl = export("Payout750_Declined", frm, to)
    clos = export("Payout750_Closed", frm, to)
    print(f"pulled: confirmed={len(conf)} declined={len(decl)} closed={len(clos)}", flush=True)

    # backend_ok per cspid = did ANY Closed for that cspid post consent ok
    backend = {}
    for r in clos:
        c = cid_of(r); ep = r.get("event_props", {}) or {}
        st = ep.get("api_status")
        if st == "ok": backend[c] = "ok"
        elif c not in backend and st: backend.setdefault(c, st)

    # per cspid: final decision (opt-in wins; else latest decline)
    rows = {}
    def upd(r, choice):
        c = cid_of(r); ep = r.get("event_props", {}) or {}; ts = str(r.get("ts", ""))
        cur = rows.get(c)
        rank = {"OPTED_IN": 2, "DECLINED": 1}
        if cur and rank[cur["choice"]] > rank[choice]: return
        if cur and cur["choice"] == choice and cur["ts"] >= ts: return  # same choice → keep latest by ts
        rows[c] = {"choice": choice, "ts": ts, "flow": ep.get("flow", ""),
                   "secs": ep.get("seconds", ""), "lang": ep.get("lang", ""),
                   "dwell": [ep.get("sec_" + b, "") for b in BLOCKS], "max_block": ep.get("max_block", "")}
    for r in decl: upd(r, "DECLINED")
    for r in conf: upd(r, "OPTED_IN")

    def fmt_ts(ts):
        try: return datetime.datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
        except: return ts

    hdr = ["cspid", "flow", "choice", "decided_at (IST)", "backend_recorded"] + ["sec_" + b for b in BLOCKS] + ["max_block", "total_secs", "lang"]
    out = [hdr]
    for c in sorted(rows, key=lambda k: rows[k]["ts"]):
        d = rows[c]
        out.append([c, d["flow"], d["choice"], fmt_ts(d["ts"]), backend.get(c, "-")] + d["dwell"] + [d["max_block"], d["secs"], d["lang"]])

    n_opt = sum(1 for d in rows.values() if d["choice"] == "OPTED_IN")
    n_dec = sum(1 for d in rows.values() if d["choice"] == "DECLINED")
    now = datetime.datetime.now(IST)
    banner = [f"PAYOUT750 OPT-IN LOG — our owned record from CleverTap (Confirmed/Declined). "
              f"{n_opt} opted-in · {n_dec} declined · {len(rows)} CSPs decided. backend_recorded = did the consent POST land (from Closed api_status). "
              f"Last refresh {now:%Y-%m-%d %H:%M IST}."]

    gc = gspread.authorize(creds())
    ss = gc.open_by_key(SHEET_ID)
    try:
        ws = ss.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(TAB, rows=max(50, len(out) + 5), cols=len(hdr))
    ws.clear()
    ws.update(values=[banner] + [[""] * len(hdr)] + out, range_name="A1", value_input_option="RAW")
    ws.format("A3:{}3".format(chr(64 + len(hdr))), {"textFormat": {"bold": True}})
    print(f"wrote '{TAB}': {len(rows)} CSPs ({n_opt} opt-in / {n_dec} declined). OK {now:%Y-%m-%d %H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
