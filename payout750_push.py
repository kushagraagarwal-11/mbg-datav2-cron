# -*- coding: utf-8 -*-
"""Payout750 PUSH channel tracker — the "750 opt in" push notification (campaign 1787309094,
FCM, live 21-Aug 16:14). Writes a "Push (750 opt-in)" tab: clicked -> opted-in funnel + per-CSP
push opt-ins (name, mobile, click time, opt-in time). Opt-ins are attributed to the push when a
CSP CLICKED it (wzrk_id starts with the campaign id) AND opted in on the backend after it went out.

Env: GOOGLE_SA_JSON (or local SA). Optional: METABASE_KEY, P750_PUSH_CAMPAIGN, P750_PUSH_FROM,
     P750_PUSH_SINCE_TS, P750_PUSH_TAB.
"""
import os, sys, json, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

CT_ACC  = os.environ.get("CLEVERTAP_ACCOUNT", "44Z-644-777Z")
CT_PASS = os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
REGION  = os.environ.get("CLEVERTAP_REGION", "eu1")
CT      = f"https://{REGION}.api.clevertap.com"
HP = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
HG = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS}
MB_KEY = os.environ.get("METABASE_KEY", "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
SHEET_ID = "1ap0K6GB6RijeLHRWPf9N84U0cl1XEJs67DSQBql7sGQ"
DESIGN_SHEET_ID = "1SfWil0SaN1lKPTqtTF86edoPk8lT1BxzNzB6vC0_4Yc"
PUSH_CAMPAIGN = os.environ.get("P750_PUSH_CAMPAIGN", "1787309094")
PUSH_FROM     = int(os.environ.get("P750_PUSH_FROM", "20260821"))
PUSH_SINCE_TS = os.environ.get("P750_PUSH_SINCE_TS", "2026-08-21 16:14:00")   # IST send time
PUSH_TAB      = os.environ.get("P750_PUSH_TAB", "Push (750 opt-in)")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def export(ev, frm, to):
    body = json.dumps({"event_name": ev, "from": frm, "to": to}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?batch_size=1000", data=body, headers=HP), timeout=90).read().decode())
    cur = d.get("cursor"); recs = []; p = 0
    while cur and p < 300:
        dd = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?cursor=" + cur, headers=HG), timeout=90).read().decode())
        recs += dd.get("records", []); cur = dd.get("next_cursor"); p += 1
        if not cur: break
    return recs


def mb(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request("https://metabase.wiom.in/api/dataset", data=body, headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=120).read().decode())
        if isinstance(d, dict) and d.get("error"): print("  MB err", str(d["error"])[:120]); return []
        return d["data"]["rows"]
    except Exception as e:
        print("  MB failed", str(e)[:120]); return []


def creds():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if raw:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name, scopes=scopes)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p):
            return service_account.Credentials.from_service_account_file(p, scopes=scopes)
    raise SystemExit("no SA creds")


def mob(p):
    p = (p or "").replace("+", "").strip()
    return p[2:] if p.startswith("91") and len(p) == 12 else p


def main():
    gc = gspread.authorize(creds())
    now = datetime.datetime.now(IST)
    tom = int((now + datetime.timedelta(days=1)).strftime("%Y%m%d"))
    # 1) unique CSPs who clicked THIS push (wzrk_id starts with the campaign id), excl test
    clicks = {}
    for r in export("Notification Clicked", PUSH_FROM, tom):
        ep = r.get("event_props", {}) or {}
        if str(ep.get("wzrk_id", "")).startswith(PUSH_CAMPAIGN):
            c = (r.get("profile", {}).get("profileData", {}) or {}).get("cspid", "")
            if c and not c.upper().startswith("TEST") and c != "a0a0b1":
                ts = str(r.get("ts", ""))
                if c not in clicks or ts < clicks[c]: clicks[c] = ts   # first click
    n_click = len(clicks)
    # 2) of those clickers, who opted in on the backend after the push went out
    opted = {}
    if clicks:
        inlist = ",".join("'%s'" % c.replace("'", "") for c in clicks)
        for r in mb(f"SELECT CSP_ID, TO_CHAR(CONVERT_TIMEZONE('Asia/Kolkata',CONSENT_TIMESTAMP),'YYYY-MM-DD HH24:MI') "
                    f"FROM PROD_DB.CSP_RV_SERVICE_CSP_RV_SERVICE.DOMINANCE_CONSENT WHERE CONSENT_CHOICE='OPTED_IN' "
                    f"AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE AND CSP_ID IN ({inlist}) "
                    f"AND CONSENT_TIMESTAMP >= '{PUSH_SINCE_TS} +05:30'::timestamp_tz"):
            opted[r[0]] = r[1]
    # names + mobile from CSP_ACCOUNT + Design
    ids = list(opted)
    name = {}; acctm = {}
    if ids:
        inl = ",".join("'%s'" % c.replace("'", "") for c in ids)
        acctm = {r[0]: mob(str(r[1] or "")) for r in mb(f"SELECT CSP_ID, MOBILE_NUMBER FROM PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_ACCOUNT WHERE CSP_ID IN ({inl}) AND _FIVETRAN_ACTIVE=TRUE")}
    d = gc.open_by_key(DESIGN_SHEET_ID).worksheet("Design").get_all_values()
    H = d[0]; ci = H.index("CSP ID"); ni = H.index("Partner Name")
    name = {r[ci].strip(): r[ni].strip() for r in d[1:] if len(r) > max(ci, ni) and r[ci].strip()}

    conv = f"{round(100*len(opted)/n_click)}%" if n_click else "-"
    hdr_note = [f"PAYOUT750 — PUSH CHANNEL · \"750 opt in\" push (campaign {PUSH_CAMPAIGN}, FCM, live 21-Aug 16:14) | "
                f"clicked the push: {n_click}  →  opted in: {len(opted)}  ({conv} click→opt) | "
                f"opt-ins attributed = clicked this push AND opted on backend since send | updated {now:%Y-%m-%d %H:%M IST}"]
    head = ["CSP ID", "Business Name", "Mobile", "Opted at (IST)"]
    rows = [[c, name.get(c, ""), acctm.get(c, ""), opted[c]] for c in sorted(opted, key=lambda x: opted[x])]
    out = [hdr_note, [], ["Push clicked (unique CSPs)", n_click], ["Opted in from push", len(opted)], ["Click → opt-in", conv], [], head] + rows

    ss = gc.open_by_key(SHEET_ID)
    try: ws = ss.worksheet(PUSH_TAB); ws.clear()
    except gspread.WorksheetNotFound: ws = ss.add_worksheet(PUSH_TAB, rows=max(30, len(out) + 10), cols=4)
    ws.update(values=out, range_name="A1", value_input_option="RAW")
    ws.format("A1:D1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    ws.format("A7:D7", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}})
    print(f"wrote '{PUSH_TAB}': push clicked={n_click} opted={len(opted)} ({conv}) | {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
